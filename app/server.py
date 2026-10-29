"""Film Room: serves the mobile-first front end and sends training frames to Claude.

The Anthropic key lives only on this server. The browser talks to /api/* and
never sees it.

Run locally:
    python app/server.py                      # http://127.0.0.1:5000
Environment:
    ANTHROPIC_API_KEY   required for reviews (or `ant auth login`)
    CLAUDE_MODEL        default claude-sonnet-5-5
    APP_TOKEN           optional access code; when set, every /api/* call needs it
    RATE_LIMIT_PER_MIN  default 20 requests per client per minute
    REVIEW_MAX_FRAMES   frame cap per review, 1 to 24, default 16
    REVIEW_MIN_FRAMES   frame floor per review, default 4
    FRAME_INTERVAL_SECONDS  target spacing between frames, default 8
    FILM_ROOM_DB        database path override (used by tests)
    HOST / PORT         default 127.0.0.1 / 5000
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
from flask import Flask, jsonify, request, send_from_directory

import coaching
from disciplines import REPORT_VERSION, config_payload, prompt_version, validate_discipline
from migrations import migrate
from reports import ModelRefused, ReportError, to_plain_text, validate_frame_times

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("FILM_ROOM_DB", ROOT / "db" / "training.db"))
SCHEMA_PATH = ROOT / "schema.sql"
STATIC_DIR = Path(__file__).resolve().parent / "static"

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
APP_TOKEN = os.environ.get("APP_TOKEN", "")
RATE_LIMIT_PER_MIN = int(os.environ.get("RATE_LIMIT_PER_MIN", "20"))
MAX_FRAMES = int(os.environ.get("REVIEW_MAX_FRAMES", "16"))
MIN_FRAMES = int(os.environ.get("REVIEW_MIN_FRAMES", "4"))
FRAME_INTERVAL_SECONDS = float(os.environ.get("FRAME_INTERVAL_SECONDS", "8"))
if not 1 <= MAX_FRAMES <= 24 or not 1 <= MIN_FRAMES <= MAX_FRAMES:
    raise SystemExit("REVIEW_MAX_FRAMES must be 1-24 and REVIEW_MIN_FRAMES must be between 1 and it.")

LIVE_MAX_FRAMES = 3
FRAME_MAX_CHARS = 1_500_000
CONTEXT_MAX_CHARS = 500
FRAME_PATTERN = re.compile(r"data:image/jpeg;base64,([A-Za-z0-9+/=]+)")

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024
log = logging.getLogger("filmroom")

_client = None
_hits = defaultdict(deque)
_hits_lock = threading.Lock()


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


def over_rate_limit(key):
    now = time.monotonic()
    with _hits_lock:
        hits = _hits[key]
        while hits and now - hits[0] > 60:
            hits.popleft()
        if len(hits) >= RATE_LIMIT_PER_MIN:
            return True
        hits.append(now)
        return False


@app.before_request
def guard_api():
    if not request.path.startswith("/api/"):
        return None
    if APP_TOKEN and not hmac.compare_digest(request.headers.get("X-App-Token", ""), APP_TOKEN):
        return jsonify(error="Enter the access code to continue."), 401
    if over_rate_limit(request.remote_addr or "unknown"):
        return jsonify(error="Too many requests. Wait a minute and try again."), 429
    return None


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


def clean_context(raw):
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise ValueError("Context must be text.")
    return re.sub(r"[<>]", "", raw).strip()[:CONTEXT_MAX_CHARS]


def ai_failure(exc):
    if isinstance(exc, anthropic.RateLimitError):
        return jsonify(error="The AI service is busy. Try again in a minute."), 429
    if isinstance(exc, anthropic.AuthenticationError):
        log.error("Anthropic rejected the API key.")
        return jsonify(error="The server's Anthropic API key is missing or invalid."), 500
    if isinstance(exc, anthropic.PermissionDeniedError):
        return jsonify(error="This API key cannot use the configured model."), 500
    if isinstance(exc, anthropic.BadRequestError):
        log.error("Anthropic rejected a request: %s", exc)
        return jsonify(error="The AI service rejected these frames. Try a different clip or a shorter one."), 422
    if isinstance(exc, anthropic.APIConnectionError):
        return jsonify(error="Could not reach the AI service. Check the connection and try again."), 503
    if isinstance(exc, anthropic.InternalServerError):
        return jsonify(error="The AI service had a problem. Try again in a moment."), 502
    log.exception("Unexpected AI service error")
    return jsonify(error="The AI service returned an error. Try again."), 502


def find_cached(request_hash, discipline, model, prompt):
    """A cached report is reused only when every setting that shapes it matches."""
    with closing(db()) as conn:
        return conn.execute(
            "SELECT review_id, report_json FROM film_reviews "
            "WHERE request_hash = ? AND discipline = ? AND model = ? AND report_version = ? "
            "AND prompt_version = ? AND report_json IS NOT NULL ORDER BY review_id DESC LIMIT 1",
            (request_hash, discipline, model, REPORT_VERSION, prompt),
        ).fetchone()


def save_review(mode, context, frame_times, report, request_hash, usage, prompt):
    with closing(db()) as conn:
        cur = conn.execute(
            "INSERT INTO film_reviews (created_at, mode, context, frame_count, feedback, report_json, "
            "report_version, request_hash, frame_times_json, model, input_tokens, output_tokens, "
            "discipline, prompt_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            ),
        )
        conn.commit()
        return cur.lastrowid


@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/api/config")
def config():
    return jsonify(
        maxFrames=MAX_FRAMES,
        minFrames=MIN_FRAMES,
        frameIntervalSeconds=FRAME_INTERVAL_SECONDS,
        model=MODEL,
        disciplines=config_payload(),
    )


@app.post("/api/review")
def review():
    body = request.get_json(silent=True) or {}
    mode = body.get("mode")
    if mode not in ("upload", "live"):
        return jsonify(error="Mode must be 'upload' or 'live'."), 400
    try:
        discipline = validate_discipline(body.get("discipline"))
        frames = parse_frames(body.get("frames"), MAX_FRAMES)
        frame_times = validate_frame_times(body.get("frameTimes"), len(frames))
        context = clean_context(body.get("context"))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    prompt = prompt_version(discipline)
    request_hash = coaching.request_fingerprint(MODEL, discipline, frames, frame_times, context)
    cached = find_cached(request_hash, discipline, MODEL, prompt)
    if cached:
        return jsonify(id=cached["review_id"], report=json.loads(cached["report_json"]), cached=True)

    try:
        report, usage = coaching.generate_report(claude(), MODEL, frames, frame_times, discipline, context)
    except ModelRefused as exc:
        return jsonify(error=str(exc)), 422
    except ReportError as exc:
        return jsonify(error=str(exc)), 502
    except anthropic.APIError as exc:
        return ai_failure(exc)

    review_id = save_review(mode, context, frame_times, report, request_hash, usage, prompt)
    return jsonify(id=review_id, report=report, cached=False)


@app.post("/api/live-cue")
def live_cue():
    body = request.get_json(silent=True) or {}
    try:
        discipline = validate_discipline(body.get("discipline"))
        frames = parse_frames(body.get("frames"), LIVE_MAX_FRAMES)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    try:
        cue = coaching.live_cue(claude(), MODEL, frames, discipline)
    except anthropic.APIError as exc:
        return ai_failure(exc)
    return jsonify(cue=cue)


@app.get("/api/reviews")
def list_reviews():
    with closing(db()) as conn:
        rows = conn.execute(
            "SELECT review_id AS id, created_at, mode, context, frame_count, feedback, report_json, discipline "
            "FROM film_reviews ORDER BY review_id DESC LIMIT 20"
        ).fetchall()
    reviews = []
    for row in rows:
        item = dict(row)
        item["report"] = json.loads(item.pop("report_json")) if item.get("report_json") else None
        item["discipline_label"] = coaching.discipline_label(item.get("discipline"))
        reviews.append(item)
    return jsonify(reviews=reviews)


init_db()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "5000")),
    )
