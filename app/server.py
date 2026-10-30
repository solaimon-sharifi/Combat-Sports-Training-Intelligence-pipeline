"""Film Room: private combat sports video analysis.

The Anthropic key and the access code live only on this server. The browser
logs in with the access code and then holds an HttpOnly session cookie. Videos
are stored privately and are served only to the logged-in operator.

Run locally:
    python app/server.py                      # http://127.0.0.1:5000
Environment (APP_TOKEN is required; the server will not start without it):
    APP_TOKEN                access code, at least 12 characters
    ANTHROPIC_API_KEY        required for reviews (or `ant auth login`)
    CLAUDE_MODEL             default claude-sonnet-5-5
    RATE_LIMIT_PER_MIN       default 20 requests per client per minute
    REVIEW_MAX_FRAMES        frame cap per review, 1 to 24, default 16
    REVIEW_MIN_FRAMES        frame floor per review, default 4
    FRAME_INTERVAL_SECONDS   target spacing between frames, default 8
    VIDEO_RETENTION_DAYS     default 7
    VIDEO_MAX_MB             default 500
    VIDEO_MAX_SECONDS        default 1800
    FILM_ROOM_STORAGE        video storage directory (default ./storage, git-ignored)
    FILM_ROOM_COOKIE_SECURE  set to 1 when served over HTTPS
    FILM_ROOM_DB             database path override (used by tests)
    HOST / PORT              default 127.0.0.1 / 5000
"""
import hmac
import json
import logging
import os
import re
import sqlite3
import threading
import time
from collections import defaultdict, deque
from contextlib import closing
from datetime import datetime
from pathlib import Path

import anthropic
from flask import Flask, g, jsonify, request, send_file, send_from_directory

import auth
import coaching
import storage
from disciplines import REPORT_VERSION, config_payload, prompt_version, validate_discipline
from migrations import migrate
from reports import ModelRefused, ReportError, to_plain_text, validate_frame_times

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("FILM_ROOM_DB", ROOT / "db" / "training.db"))
SCHEMA_PATH = ROOT / "schema.sql"
STATIC_DIR = Path(__file__).resolve().parent / "static"

APP_TOKEN = os.environ.get("APP_TOKEN", "")
if len(APP_TOKEN) < 12:
    raise SystemExit("Set APP_TOKEN to an access code of at least 12 characters before starting Film Room.")

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
RATE_LIMIT_PER_MIN = int(os.environ.get("RATE_LIMIT_PER_MIN", "60"))
MEDIA_RATE_LIMIT_PER_MIN = int(os.environ.get("MEDIA_RATE_LIMIT_PER_MIN", "600"))
MAX_FRAMES = int(os.environ.get("REVIEW_MAX_FRAMES", "16"))
MIN_FRAMES = int(os.environ.get("REVIEW_MIN_FRAMES", "4"))
FRAME_INTERVAL_SECONDS = float(os.environ.get("FRAME_INTERVAL_SECONDS", "8"))
if not 1 <= MAX_FRAMES <= 24 or not 1 <= MIN_FRAMES <= MAX_FRAMES:
    raise SystemExit("REVIEW_MAX_FRAMES must be 1-24 and REVIEW_MIN_FRAMES must be between 1 and it.")

LIVE_MAX_FRAMES = 3
JSON_MAX_BYTES = 12 * 1024 * 1024
FRAME_MAX_CHARS = 1_500_000
CONTEXT_MAX_CHARS = 500
ATHLETE_MAX_CHARS = 200
FRAME_PATTERN = re.compile(r"data:image/jpeg;base64,([A-Za-z0-9+/=]+)")

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = storage.MAX_BYTES + 5 * 1024 * 1024
log = logging.getLogger("filmroom")

_client = None
_hits = defaultdict(deque)
_hits_lock = threading.Lock()
UPLOAD_WINDOW_SECONDS = 600
UPLOADS_PER_WINDOW = 10
_upload_hits = defaultdict(deque)
DELETE_ALL_PHRASE = "DELETE ALL REPORTS"


def claude():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(max_retries=3, timeout=180.0)
    return _client


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    with closing(db()) as conn:
        conn.executescript(SCHEMA_PATH.read_text())
        migrate(conn)


def over_upload_limit(key):
    now = time.monotonic()
    with _hits_lock:
        hits = _upload_hits[key]
        while hits and now - hits[0] > UPLOAD_WINDOW_SECONDS:
            hits.popleft()
        if len(hits) >= UPLOADS_PER_WINDOW:
            return True
        hits.append(now)
        return False


def over_rate_limit(key, limit=None):
    now = time.monotonic()
    with _hits_lock:
        hits = _hits[key]
        while hits and now - hits[0] > 60:
            hits.popleft()
        if len(hits) >= (limit or RATE_LIMIT_PER_MIN):
            return True
        hits.append(now)
        return False


def client_key():
    return request.remote_addr or "unknown"


def error(message, status):
    return jsonify(error=message), status


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    if request.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.before_request
def guard_api():
    if not request.path.startswith("/api/"):
        return None
    if request.path.endswith("/media"):
        if over_rate_limit("media:" + client_key(), MEDIA_RATE_LIMIT_PER_MIN):
            return error("Too many playback requests. Wait a moment and try again.", 429)
    elif over_rate_limit(client_key()):
        return error("Too many requests. Wait a minute and try again.", 429)
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.headers.get("X-Film-Room") != "1":
        return error("This request must come from the Film Room page.", 403)
    if request.path != "/api/videos" and (request.content_length or 0) > JSON_MAX_BYTES:
        return error("The request is too large.", 413)
    if request.path == "/api/login":
        return None

    owner = None
    token = request.cookies.get(auth.COOKIE_NAME)
    if token:
        with closing(db()) as conn:
            owner = auth.session_owner(conn, token)
    if owner is None and hmac.compare_digest(request.headers.get("X-App-Token", "").encode(), APP_TOKEN.encode()):
        owner = auth.OWNER
    if owner is None:
        return error("Log in with the access code to continue.", 401)
    g.owner = owner
    return None


@app.errorhandler(413)
def too_large(_):
    return error("The upload is too large. The limit is 500 MB and 30 minutes.", 413)


def parse_frames(raw, limit):
    if not isinstance(raw, list) or not raw:
        raise ValueError("Send at least one frame.")
    if len(raw) > limit:
        raise ValueError(f"Send at most {limit} frames.")
    frames = []
    for i, item in enumerate(raw, start=1):
        match = FRAME_PATTERN.fullmatch(item) if isinstance(item, str) else None
        if not match:
            raise ValueError(f"Frame {i} must be a JPEG data URL.")
        if len(match.group(1)) > FRAME_MAX_CHARS:
            raise ValueError(f"Frame {i} is too large. Keep frames at 768 px or smaller.")
        frames.append(match.group(1))
    return frames


def clean_text(raw, limit, field):
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise ValueError(f"{field} must be text.")
    return re.sub(r"[<>]", "", raw).strip()[:limit]


def resolve_video(raw, frame_times, mode):
    """A review may point at a stored video only if the operator owns it, it has not expired,
    and every sampled time falls inside it. Timestamps are never shown beyond the clip."""
    if raw is None:
        return None
    if mode != "upload":
        raise ValueError("Only uploaded clips can be stored for playback.")
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError("The video reference is not valid.")
    with closing(db()) as conn:
        storage.purge_expired(conn)
        row = conn.execute(
            "SELECT duration_seconds FROM videos WHERE video_id = ? AND owner = ?", (raw, g.owner)
        ).fetchone()
    if row is None:
        raise ValueError("That video is no longer available. Upload it again.")
    if frame_times and max(frame_times) > row["duration_seconds"] + 1.0:
        raise ValueError("Sampled times extend beyond the stored video.")
    return raw


def ai_failure(exc):
    if isinstance(exc, anthropic.RateLimitError):
        return error("The AI service is busy. Try again in a minute.", 429)
    if isinstance(exc, anthropic.AuthenticationError):
        log.error("Anthropic rejected the API key.")
        return error("The server's Anthropic API key is missing or invalid.", 500)
    if isinstance(exc, anthropic.PermissionDeniedError):
        return error("This API key cannot use the configured model.", 500)
    if isinstance(exc, anthropic.BadRequestError):
        log.error("Anthropic rejected a request: %s", exc)
        return error("The AI service rejected these frames. Try a different clip or a shorter one.", 422)
    if isinstance(exc, anthropic.APIConnectionError):
        return error("Could not reach the AI service. Check the connection and try again.", 503)
    if isinstance(exc, anthropic.InternalServerError):
        return error("The AI service had a problem. Try again in a moment.", 502)
    log.exception("Unexpected AI service error")
    return error("The AI service returned an error. Try again.", 502)


def find_cached(request_hash, discipline, model, prompt):
    """A cached report is reused only when every setting that shapes it matches."""
    with closing(db()) as conn:
        return conn.execute(
            "SELECT review_id, report_json FROM film_reviews "
            "WHERE request_hash = ? AND discipline = ? AND model = ? AND report_version = ? "
            "AND prompt_version = ? AND report_json IS NOT NULL ORDER BY review_id DESC LIMIT 1",
            (request_hash, discipline, model, REPORT_VERSION, prompt),
        ).fetchone()


def save_review(mode, context, frame_times, report, request_hash, usage, prompt, video_id, athlete):
    with closing(db()) as conn:
        cur = conn.execute(
            "INSERT INTO film_reviews (created_at, mode, context, frame_count, feedback, report_json, "
            "report_version, request_hash, frame_times_json, model, input_tokens, output_tokens, "
            "discipline, prompt_version, video_id, athlete_description) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now().isoformat(timespec="seconds"),
                mode,
                context or None,
                len(frame_times),
                to_plain_text(report),
                json.dumps(report),
                REPORT_VERSION,
                request_hash,
                json.dumps(frame_times),
                MODEL,
                getattr(usage, "input_tokens", None),
                getattr(usage, "output_tokens", None),
                report["discipline"],
                prompt,
                video_id,
                athlete or None,
            ),
        )
        conn.commit()
        return cur.lastrowid


def set_session_cookie(response, token):
    secure = request.is_secure or os.environ.get("FILM_ROOM_COOKIE_SECURE") == "1"
    response.set_cookie(
        auth.COOKIE_NAME,
        token,
        max_age=auth.SESSION_HOURS * 3600,
        httponly=True,
        samesite="Strict",
        secure=secure,
        path="/",
    )


@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.post("/api/login")
def login():
    key = client_key()
    if auth.login_blocked(key):
        return error("Too many failed attempts. Wait 15 minutes and try again.", 429)
    body = request.get_json(silent=True) or {}
    if not auth.code_matches(body.get("code"), APP_TOKEN):
        auth.record_login_failure(key)
        return error("That access code is not right.", 401)
    auth.clear_login_failures(key)
    with closing(db()) as conn:
        token, expires = auth.create_session(conn)
    response = jsonify(ok=True, expiresAt=expires)
    set_session_cookie(response, token)
    return response


@app.post("/api/logout")
def logout():
    token = request.cookies.get(auth.COOKIE_NAME)
    if token:
        with closing(db()) as conn:
            auth.end_session(conn, token)
    response = jsonify(ok=True)
    response.delete_cookie(auth.COOKIE_NAME, path="/", samesite="Strict")
    return response


@app.get("/api/session")
def session():
    return jsonify(authenticated=True, owner=g.owner, retentionDays=storage.RETENTION_DAYS)


@app.get("/api/config")
def config():
    return jsonify(
        maxFrames=MAX_FRAMES,
        minFrames=MIN_FRAMES,
        frameIntervalSeconds=FRAME_INTERVAL_SECONDS,
        model=MODEL,
        disciplines=config_payload(),
        retentionDays=storage.RETENTION_DAYS,
        videoMaxMB=storage.MAX_BYTES // (1024 * 1024),
        videoMaxSeconds=int(storage.MAX_SECONDS),
        videoFormats=sorted(storage.ALLOWED_EXTENSIONS),
    )


@app.post("/api/videos")
def upload_video():
    if over_upload_limit(client_key()):
        return error("Too many uploads. Wait ten minutes before uploading another video.", 429)
    with closing(db()) as conn:
        storage.purge_expired(conn)
    upload = request.files.get("file")
    if upload is None:
        return error("Choose a video file.", 400)
    try:
        info = storage.save_upload(upload.stream, upload.filename)
    except storage.UploadError as exc:
        return error(str(exc), exc.status)
    created = auth.now_utc()
    expires = storage.expiry_for(created)
    with closing(db()) as conn:
        cur = conn.execute(
            "INSERT INTO videos (owner, storage_name, display_name, mime, size_bytes, duration_seconds, "
            "codec, sha256, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                g.owner, info["storage_name"], info["display_name"], info["mime"], info["size_bytes"],
                info["duration_seconds"], info["codec"], info["sha256"], auth.iso(created), expires,
            ),
        )
        conn.commit()
        video_id = cur.lastrowid
    return jsonify(
        id=video_id,
        displayName=info["display_name"],
        durationSeconds=info["duration_seconds"],
        sizeBytes=info["size_bytes"],
        expiresAt=expires,
    ), 201


@app.get("/api/videos")
def list_videos():
    with closing(db()) as conn:
        storage.purge_expired(conn)
        rows = conn.execute(
            "SELECT video_id AS id, display_name, duration_seconds, size_bytes, created_at, expires_at "
            "FROM videos WHERE owner = ? ORDER BY video_id DESC",
            (g.owner,),
        ).fetchall()
    return jsonify(videos=[dict(row) for row in rows])


@app.delete("/api/videos/<int:video_id>")
def remove_video(video_id):
    with closing(db()) as conn:
        storage.purge_expired(conn)
        removed = storage.delete_video(conn, video_id, g.owner)
    if not removed:
        return error("Video not found.", 404)
    return jsonify(ok=True)


@app.get("/api/videos/<int:video_id>/media")
def video_media(video_id):
    with closing(db()) as conn:
        storage.purge_expired(conn)
        row = conn.execute(
            "SELECT storage_name, mime FROM videos WHERE video_id = ? AND owner = ?",
            (video_id, g.owner),
        ).fetchone()
    if row is None:
        return error("Video not found.", 404)
    path = storage.video_path(row["storage_name"])
    if not path.exists():
        return error("Video not found.", 404)
    response = send_file(path, mimetype=row["mime"], conditional=True)
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.delete("/api/reviews/<int:review_id>")
def remove_review(review_id):
    """Deletes one report. Its stored video, if any, and every other report are left alone."""
    with closing(db()) as conn:
        cur = conn.execute("DELETE FROM film_reviews WHERE review_id = ?", (review_id,))
        conn.commit()
        removed = cur.rowcount
    if not removed:
        return error("Report not found.", 404)
    return jsonify(ok=True, deleted=1)


@app.delete("/api/reviews")
def remove_all_reviews():
    """Deletes every report after an explicit confirmation phrase. Stored videos are not touched."""
    body = request.get_json(silent=True) or {}
    if body.get("confirm") != DELETE_ALL_PHRASE:
        return error(f"Type {DELETE_ALL_PHRASE} to confirm.", 400)
    with closing(db()) as conn:
        cur = conn.execute("DELETE FROM film_reviews")
        conn.commit()
        removed = cur.rowcount
    return jsonify(ok=True, deleted=removed)


@app.post("/api/review")
def review():
    body = request.get_json(silent=True) or {}
    mode = body.get("mode")
    if mode not in ("upload", "live"):
        return error("Mode must be 'upload' or 'live'.", 400)
    try:
        discipline = validate_discipline(body.get("discipline"))
        athlete = clean_text(body.get("athleteDescription"), ATHLETE_MAX_CHARS, "Athlete description")
        frames = parse_frames(body.get("frames"), MAX_FRAMES)
        frame_times = validate_frame_times(body.get("frameTimes"), len(frames))
        context = clean_text(body.get("context"), CONTEXT_MAX_CHARS, "Context")
        video_id = resolve_video(body.get("videoId"), frame_times, mode)
    except ValueError as exc:
        return error(str(exc), 400)

    prompt = prompt_version(discipline)
    request_hash = coaching.request_fingerprint(MODEL, discipline, frames, frame_times, context, athlete)
    cached = find_cached(request_hash, discipline, MODEL, prompt)
    if cached:
        return jsonify(id=cached["review_id"], report=json.loads(cached["report_json"]), cached=True)

    try:
        report, usage = coaching.generate_report(claude(), MODEL, frames, frame_times, discipline, context, athlete)
    except ModelRefused as exc:
        return error(str(exc), 422)
    except ReportError as exc:
        return error(str(exc), 502)
    except anthropic.APIError as exc:
        return ai_failure(exc)

    review_id = save_review(mode, context, frame_times, report, request_hash, usage, prompt, video_id, athlete)
    return jsonify(id=review_id, report=report, cached=False)


@app.post("/api/live-cue")
def live_cue():
    body = request.get_json(silent=True) or {}
    try:
        discipline = validate_discipline(body.get("discipline"))
        frames = parse_frames(body.get("frames"), LIVE_MAX_FRAMES)
    except ValueError as exc:
        return error(str(exc), 400)
    try:
        cue = coaching.live_cue(claude(), MODEL, frames, discipline)
    except anthropic.APIError as exc:
        return ai_failure(exc)
    return jsonify(cue=cue)


@app.get("/api/reviews")
def list_reviews():
    with closing(db()) as conn:
        storage.purge_expired(conn)
        rows = conn.execute(
            "SELECT r.review_id AS id, r.created_at, r.mode, r.context, r.frame_count, r.feedback, "
            "r.report_json, r.discipline, r.video_id, r.athlete_description, "
            "EXISTS (SELECT 1 FROM videos v WHERE v.video_id = r.video_id AND v.owner = ?) AS video_available "
            "FROM film_reviews r ORDER BY r.review_id DESC LIMIT 20",
            (g.owner,),
        ).fetchall()
    reviews = []
    for row in rows:
        item = dict(row)
        item["report"] = json.loads(item.pop("report_json")) if item.get("report_json") else None
        item["discipline_label"] = coaching.discipline_label(item.get("discipline"))
        item["video_available"] = bool(item["video_available"])
        reviews.append(item)
    return jsonify(reviews=reviews)


def sweep_expired():
    """Remove expired sessions and expired videos. Returns the number of videos removed."""
    with closing(db()) as conn:
        auth.purge_expired_sessions(conn)
        return storage.purge_expired(conn)


def retention_sweep_forever():
    while True:
        time.sleep(3600)
        try:
            removed = sweep_expired()
            if removed:
                log.info("Retention sweep removed %s expired video(s).", removed)
        except Exception:
            log.exception("Retention sweep failed")


init_db()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sweep_expired()
    threading.Thread(target=retention_sweep_forever, daemon=True).start()
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "5000")),
    )
