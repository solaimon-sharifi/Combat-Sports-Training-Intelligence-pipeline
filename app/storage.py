"""Private video storage: validation, retention, and deletion.

Videos are written under the storage directory, which is outside the static
web root and has no URL of its own. The only way to read one is the
authenticated media route in server.py. Stored file names are random, so a
name reveals nothing about the footage.

Retention: every video expires RETENTION_DAYS after upload. Expired videos are
deleted on each video request and by an hourly background sweep.
"""
import hashlib
import os
import secrets
import shutil
from datetime import timedelta
from pathlib import Path

from auth import iso, now_utc

ROOT = Path(__file__).resolve().parent.parent
STORAGE_DIR = Path(os.environ.get("FILM_ROOM_STORAGE", ROOT / "storage"))
RETENTION_DAYS = int(os.environ.get("VIDEO_RETENTION_DAYS", "7"))
MAX_BYTES = int(float(os.environ.get("VIDEO_MAX_MB", "500")) * 1024 * 1024)
MAX_SECONDS = float(os.environ.get("VIDEO_MAX_SECONDS", "1800"))
MIN_SECONDS = 1.0
MAX_DIMENSION = 4320
CHUNK = 1024 * 1024

ALLOWED_EXTENSIONS = {".mp4": "video/mp4", ".mov": "video/quicktime"}
MP4_BOXES = {b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip"}
# Codecs that no browser can decode. Accepting them would store footage that
# cannot be played back or sampled for review.
UNPLAYABLE_CODECS = {"mp4v", "mjpg", "mjpa", "mjpb", "jpeg", "xvid", "divx", "mpeg", "dx50"}
CONTAINER_BOXES = (b"moov", b"trak", b"mdia", b"minf", b"stbl")


class UploadError(Exception):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


def videos_dir():
    path = STORAGE_DIR / "videos"
    path.mkdir(parents=True, exist_ok=True)
    return path


def temp_dir():
    path = STORAGE_DIR / "tmp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def extension_for(filename):
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise UploadError("Upload an MP4 or MOV video.")
    return ext


def display_name(filename):
    cleaned = "".join(ch for ch in Path(filename or "clip").name if ch.isprintable())
    return cleaned[:120] or "clip"


def has_video_signature(header):
    """MP4 and MOV both carry a box type at byte offset 4."""
    return len(header) >= 8 and header[4:8] in MP4_BOXES


def _boxes(handle, start, end):
    """Yield (type, payload_start, box_end) for each ISO-BMFF box between start and end."""
    position = start
    while position + 8 <= end:
        handle.seek(position)
        header = handle.read(8)
        if len(header) < 8:
            return
        size = int.from_bytes(header[:4], "big")
        box_type = header[4:8]
        header_len = 8
        if size == 1:
            size = int.from_bytes(handle.read(8), "big")
            header_len = 16
        elif size == 0:
            size = end - position
        if size < header_len:
            return
        yield box_type, position + header_len, min(position + size, end)
        position += size


def _track_codec(handle, start, end):
    """Return the codec of a track if it is a video track, else None."""
    children = {box_type: (ps, pe) for box_type, ps, pe in _boxes(handle, start, end)}
    if b"mdia" not in children:
        return None
    mdia_start, mdia_end = children[b"mdia"]
    media = {box_type: (ps, pe) for box_type, ps, pe in _boxes(handle, mdia_start, mdia_end)}
    if b"hdlr" not in media:
        return None
    handler_start = media[b"hdlr"][0]
    handle.seek(handler_start + 8)
    if handle.read(4) != b"vide":
        return None
    if b"minf" not in media:
        return None
    minf_start, minf_end = media[b"minf"]
    minf = {box_type: (ps, pe) for box_type, ps, pe in _boxes(handle, minf_start, minf_end)}
    if b"stbl" not in minf:
        return None
    stbl_start, stbl_end = minf[b"stbl"]
    stbl = {box_type: (ps, pe) for box_type, ps, pe in _boxes(handle, stbl_start, stbl_end)}
    if b"stsd" not in stbl:
        return None
    stsd_start = stbl[b"stsd"][0]
    handle.seek(stsd_start + 8 + 4)
    return handle.read(4).decode("latin-1", errors="replace").lower()


def video_codec(path):
    """Read the video codec from the file's sample description boxes, which is where
    the container records it. Returns None if the file has no video track we can read."""
    with open(path, "rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        for box_type, ps, pe in _boxes(handle, 0, size):
            if box_type != b"moov":
                continue
            for child_type, cps, cpe in _boxes(handle, ps, pe):
                if child_type != b"trak":
                    continue
                codec = _track_codec(handle, cps, cpe)
                if codec:
                    return codec
    return None


def probe_clip(path):
    """Return (duration_seconds, codec) or raise UploadError if the clip is unusable."""
    import cv2

    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            raise UploadError("This video could not be read. It may be corrupt or use an unsupported codec.")
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        if fps <= 0 or frames <= 0:
            raise UploadError("This video has no readable frames.")
        codec = video_codec(path) or "unknown"
        if codec in UNPLAYABLE_CODECS:
            raise UploadError(
                f"This video uses the {codec.upper()} codec, which browsers cannot play. "
                "Re-export it as H.264 MP4 or MOV and upload again."
            )
        width = cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0
        height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0
        if max(width, height) > MAX_DIMENSION:
            raise UploadError(
                f"This video is {int(width)}x{int(height)}. Resize it to {MAX_DIMENSION} pixels "
                "or less on its longest side."
            )
        seconds = frames / fps
        ok, _ = cap.read()
        if not ok:
            raise UploadError("The first frame of this video could not be decoded.")
    finally:
        cap.release()
    if seconds < MIN_SECONDS:
        raise UploadError("This clip is shorter than one second.")
    if seconds > MAX_SECONDS:
        raise UploadError(f"This clip is longer than {int(MAX_SECONDS // 60)} minutes. Trim it and try again.")
    return seconds, codec


def save_upload(stream, filename):
    """Stream an upload to a temporary file, validate it, and move it into storage."""
    ext = extension_for(filename)
    temp_path = temp_dir() / secrets.token_hex(16)
    digest = hashlib.sha256()
    size = 0
    try:
        with open(temp_path, "wb") as handle:
            first = True
            while True:
                chunk = stream.read(CHUNK)
                if not chunk:
                    break
                if first:
                    if not has_video_signature(chunk[:12]):
                        raise UploadError("The file is not an MP4 or MOV video.")
                    first = False
                size += len(chunk)
                if size > MAX_BYTES:
                    raise UploadError(
                        f"This video is larger than {MAX_BYTES // (1024 * 1024)} MB. Trim it or lower the quality.",
                        status=413,
                    )
                digest.update(chunk)
                handle.write(chunk)
        if size == 0:
            raise UploadError("The upload was empty.")
        seconds, codec = probe_clip(temp_path)
        storage_name = secrets.token_hex(24) + ext
        shutil.move(str(temp_path), str(videos_dir() / storage_name))
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return {
        "storage_name": storage_name,
        "mime": ALLOWED_EXTENSIONS[ext],
        "size_bytes": size,
        "duration_seconds": round(seconds, 3),
        "codec": codec,
        "sha256": digest.hexdigest(),
        "display_name": display_name(filename),
    }


def expiry_for(created):
    return iso(created + timedelta(days=RETENTION_DAYS))


def video_path(storage_name):
    return videos_dir() / storage_name


def _remove_file(storage_name):
    try:
        video_path(storage_name).unlink()
    except FileNotFoundError:
        pass


def delete_video(conn, video_id, owner):
    """Delete the file and its row. Reviews keep their reports but lose the video link."""
    row = conn.execute(
        "SELECT storage_name FROM videos WHERE video_id = ? AND owner = ?", (video_id, owner)
    ).fetchone()
    if row is None:
        return False
    _remove_file(row["storage_name"])
    conn.execute("UPDATE film_reviews SET video_id = NULL WHERE video_id = ?", (video_id,))
    conn.execute("DELETE FROM videos WHERE video_id = ?", (video_id,))
    conn.commit()
    return True


def purge_expired(conn):
    """Delete every video past its expiry. Returns how many were removed."""
    rows = conn.execute(
        "SELECT video_id, storage_name FROM videos WHERE expires_at <= ?", (iso(now_utc()),)
    ).fetchall()
    for row in rows:
        _remove_file(row["storage_name"])
        conn.execute("UPDATE film_reviews SET video_id = NULL WHERE video_id = ?", (row["video_id"],))
        conn.execute("DELETE FROM videos WHERE video_id = ?", (row["video_id"],))
    conn.commit()
    return len(rows)
