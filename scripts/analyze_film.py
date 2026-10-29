"""
Command-line film review. Samples frames from a clip, then uses the same
discipline-specific coaching pipeline as the web app.

Requires:
    pip install opencv-python anthropic
    ANTHROPIC_API_KEY set in the environment (never pass the key on the command line)

Usage:
    python scripts/analyze_film.py path/to/clip.mp4 --discipline boxing
    python scripts/analyze_film.py path/to/clip.mp4 --discipline bjj --context "closed guard posture"

Disciplines: boxing, kickboxing, muay_thai, mma, bjj
"""
import argparse
import base64
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

import coaching  # noqa: E402
from disciplines import DISCIPLINES  # noqa: E402
from reports import ModelRefused, ReportError, to_plain_text  # noqa: E402

REVIEWS_DIR = ROOT / "film_reviews"
DEFAULT_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
MAX_FRAME_SIDE = 768
MIN_FRAMES, MAX_FRAMES, INTERVAL_SECONDS = 4, 16, 8.0


def plan_times(duration):
    count = min(MAX_FRAMES, max(MIN_FRAMES, int(-(-duration // INTERVAL_SECONDS))))
    return [(duration * (i + 0.5)) / count for i in range(count)]


def extract_frames(video_path):
    """Return (frames_b64, times_in_seconds), sampled to match the clip length."""
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video file: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total <= 0 or fps <= 0:
        cap.release()
        raise RuntimeError(f"Video has no readable frames (or an unsupported container): {video_path}")

    duration = total / fps
    frames, times = [], []
    for seconds in plan_times(duration):
        cap.set(cv2.CAP_PROP_POS_FRAMES, min(total - 1, round(seconds * fps)))
        ok, frame = cap.read()
        if not ok:
            continue
        height, width = frame.shape[:2]
        scale = MAX_FRAME_SIDE / max(height, width)
        if scale < 1:
            frame = cv2.resize(frame, (int(width * scale), int(height * scale)))
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if ok:
            frames.append(base64.b64encode(buf.tobytes()).decode("ascii"))
            times.append(seconds)
    cap.release()
    if not frames:
        raise RuntimeError(f"Failed to extract any frames from: {video_path}")
    return frames, times


def save_review(video_path, discipline, report_text, frame_count):
    REVIEWS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = REVIEWS_DIR / f"{video_path.stem}_{discipline}_{stamp}.md"
    out_path.write_text(
        f"# Film review: {video_path.name}\n\n"
        f"Discipline: {DISCIPLINES[discipline]['label']}. Generated "
        f"{datetime.now().isoformat(timespec='seconds')} from {frame_count} sampled frames.\n\n"
        f"{report_text}\n"
    )
    return out_path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("video", type=Path, help="path to a video clip (mp4, mov, etc.)")
    parser.add_argument("--discipline", required=True, choices=sorted(DISCIPLINES), help="sport the footage shows")
    parser.add_argument("--context", default="", help="optional note about what you are working on")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"model id (default: {DEFAULT_MODEL})")
    args = parser.parse_args()

    if not args.video.exists():
        print(f"Video file not found: {args.video}", file=sys.stderr)
        sys.exit(1)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set. Set it in this terminal, then run again.", file=sys.stderr)
        sys.exit(1)

    frames, times = extract_frames(args.video)
    print(f"Sampled {len(frames)} frames for {DISCIPLINES[args.discipline]['label']}. Sending for review...")

    import anthropic

    client = anthropic.Anthropic(max_retries=3, timeout=180.0)
    try:
        report, usage = coaching.generate_report(client, args.model, frames, times, args.discipline, args.context)
    except ModelRefused as exc:
        print(f"Review declined: {exc}", file=sys.stderr)
        sys.exit(2)
    except ReportError as exc:
        print(f"No usable report: {exc}", file=sys.stderr)
        sys.exit(2)
    except anthropic.APIError as exc:
        print(f"AI service error: {type(exc).__name__}. Try again later.", file=sys.stderr)
        sys.exit(2)

    text = to_plain_text(report)
    print(f"\n{text}\n")
    out_path = save_review(args.video, args.discipline, text, len(frames))
    print(f"Saved to {out_path}")
    print(f"Tokens: input {usage.input_tokens}, output {usage.output_tokens}")


if __name__ == "__main__":
    main()
