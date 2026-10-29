"""
AI-assisted film review: point this at a training video clip and get back
qualitative coaching feedback from a vision-capable Claude model.

This is NOT automatic event detection — it samples a handful of frames
across the clip and asks the model to comment on what's visible (position,
base, grips, structural issues). It cannot reliably identify fast
transitions or name exact techniques from a sparse frame sample, and the
prompt tells the model to say so rather than guess with false confidence —
same "state the N, don't overclaim" rule the rest of this project follows,
applied to frame count instead of sample size.

Requires:
    pip install opencv-python anthropic
    export ANTHROPIC_API_KEY=sk-ant-...        (get one at console.anthropic.com)

Usage:
    python scripts/analyze_film.py path/to/clip.mp4
    python scripts/analyze_film.py path/to/clip.mp4 --frames 12
    python scripts/analyze_film.py path/to/clip.mp4 --session-id 42
"""
import argparse
import base64
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

REVIEWS_DIR = ROOT / "film_reviews"
DEFAULT_MODEL = "claude-sonnet-5"
MAX_FRAME_SIDE = 1024  # keep image payloads small — vision quality plateaus well below this


def extract_frames(video_path: Path, n_frames: int) -> list[bytes]:
    """Pull n_frames JPEG-encoded frames, evenly spaced across the clip."""
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video file: {video_path}")

    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total <= 0:
        cap.release()
        raise RuntimeError(f"Video has no readable frames (or a corrupt/unsupported container): {video_path}")

    n_frames = min(n_frames, total)
    indices = [round(i * (total - 1) / max(1, n_frames - 1)) for i in range(n_frames)]

    frames = []
    for idx in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok:
            continue
        h, w = frame.shape[:2]
        scale = MAX_FRAME_SIDE / max(h, w)
        if scale < 1:
            frame = cv2.resize(frame, (int(w * scale), int(h * scale)))
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if ok:
            frames.append(buf.tobytes())
    cap.release()

    if not frames:
        raise RuntimeError(f"Failed to extract any frames from: {video_path}")
    return frames


FEEDBACK_PROMPT = """You are a combat sports coach (BJJ / No-Gi / wrestling / MMA) reviewing training footage for a practitioner who wants feedback.

You are shown {n} still frames sampled evenly across one video clip ("{name}"). This is a SPARSE sample of a continuous roll or drill — not full video. That means:
- You cannot reliably identify fast transitions, scrambles, or exact technique names with confidence.
- You CAN comment on what's visible in a given frame: apparent position (e.g. closed guard, half guard, mount, back, standing), base and posture, grip placement, weight distribution, head position, and other structural details.
- Where you're inferring rather than certain, say so explicitly. Do not name a specific submission or takedown unless it's unambiguous in the frame — a guess dressed up as an identification is worse than no comment.

Structure your response as:

**Observations** — what you can see across the frames, called out per frame or per cluster of frames where useful (e.g. "frames 1-3: ...").

**Areas to work on** — concrete, specific coaching notes tied to what's actually visible (not generic advice).

**Sampling caveat** — one or two sentences on what this frame count can and can't tell you about the full clip.

Keep the whole thing tight — a coach's notes, not an essay."""


def call_claude_vision(frames: list[bytes], video_name: str, model: str) -> str:
    try:
        import anthropic
    except ImportError:
        raise RuntimeError("Missing dependency. Run: pip install anthropic")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Get a key at console.anthropic.com and run:\n"
            "  export ANTHROPIC_API_KEY=sk-ant-...        (bash)\n"
            "  $env:ANTHROPIC_API_KEY = \"sk-ant-...\"       (PowerShell)"
        )

    client = anthropic.Anthropic(api_key=api_key)

    content = []
    for i, frame_bytes in enumerate(frames, start=1):
        content.append({"type": "text", "text": f"Frame {i}/{len(frames)}:"})
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": base64.b64encode(frame_bytes).decode("ascii"),
            },
        })
    content.append({"type": "text", "text": FEEDBACK_PROMPT.format(n=len(frames), name=video_name)})

    response = client.messages.create(
        model=model,
        max_tokens=1500,
        messages=[{"role": "user", "content": content}],
    )
    return "".join(block.text for block in response.content if block.type == "text")


def save_review(video_path: Path, n_frames: int, feedback: str) -> Path:
    REVIEWS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = REVIEWS_DIR / f"{video_path.stem}_{stamp}.md"
    out_path.write_text(
        f"# Film review — {video_path.name}\n\n"
        f"Generated {datetime.now().isoformat(timespec='seconds')} from {n_frames} sampled frames.\n\n"
        f"{feedback}\n"
    )
    return out_path


def attach_to_session(session_id: int, video_path: Path, feedback: str) -> None:
    from db_utils import get_connection

    conn = get_connection()
    row = conn.execute("SELECT session_id FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    if row is None:
        conn.close()
        raise ValueError(f"No session with session_id={session_id}. Check `sessions` table.")

    note = f"\n\n--- AI film review ({video_path.name}) ---\n{feedback}"
    conn.execute(
        "UPDATE sessions SET notes = COALESCE(notes, '') || ? WHERE session_id = ?",
        (note, session_id),
    )
    conn.commit()
    conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("video", type=Path, help="path to a video clip (mp4, mov, etc.)")
    parser.add_argument("--frames", type=int, default=8, help="number of frames to sample (default: 8)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"vision-capable model id (default: {DEFAULT_MODEL})")
    parser.add_argument("--session-id", type=int, default=None,
                         help="if given, append the review to this session's notes in the database")
    args = parser.parse_args()

    if not args.video.exists():
        print(f"Video file not found: {args.video}", file=sys.stderr)
        sys.exit(1)

    print(f"Extracting {args.frames} frames from {args.video.name}...")
    frames = extract_frames(args.video, args.frames)
    print(f"Got {len(frames)} frames. Sending to {args.model} for review...")

    feedback = call_claude_vision(frames, args.video.name, args.model)

    out_path = save_review(args.video, len(frames), feedback)
    print(f"\n{feedback}\n")
    print(f"Saved to {out_path}")

    if args.session_id is not None:
        attach_to_session(args.session_id, args.video, feedback)
        print(f"Appended to sessions.notes for session_id={args.session_id}")


if __name__ == "__main__":
    main()
