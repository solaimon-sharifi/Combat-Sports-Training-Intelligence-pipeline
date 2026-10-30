"""Shared coaching pipeline for the web app and the command-line script.

Nothing here imports Flask. The client is passed in, so tests and the CLI can
use it without a web server.
"""
import hashlib
import json
import logging

from disciplines import DISCIPLINES, REPORT_VERSION, build_prompt, label_for, live_cue_prompt, prompt_version, report_schema
from reports import ModelRefused, ReportError, parse_report

log = logging.getLogger("filmroom.coaching")
REVIEW_MAX_TOKENS = 4000
CUE_MAX_TOKENS = 120


def request_fingerprint(model, discipline, frames_b64, frame_times, context, athlete=""):
    """Identifies everything that changes a report. Two requests share a cached
    report only when every input here matches."""
    material = {
        "model": model,
        "report_version": REPORT_VERSION,
        "discipline": discipline,
        "prompt_version": prompt_version(discipline),
        "frame_times": frame_times,
        "context": context,
        "athlete": athlete,
        "frames": [hashlib.sha256(data.encode()).hexdigest() for data in frames_b64],
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


def frame_content(frames_b64, frame_times):
    content = []
    total = len(frames_b64)
    for i, (data, seconds) in enumerate(zip(frames_b64, frame_times), start=1):
        content.append({"type": "text", "text": f"F{i:02d} of {total}, at {seconds:.1f} s into the clip:"})
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": data},
        })
    return content


def request_report(client, model, frames_b64, frame_times, discipline, context, athlete=""):
    """One structured-output call. Returns (text, usage) or raises a typed error."""
    content = frame_content(frames_b64, frame_times)
    content.append({"type": "text", "text": build_prompt(discipline, len(frames_b64), context, athlete)})
    response = client.messages.create(
        model=model,
        max_tokens=REVIEW_MAX_TOKENS,
        messages=[{"role": "user", "content": content}],
        output_config={"format": {"type": "json_schema", "schema": report_schema(discipline)}},
    )
    if response.stop_reason == "refusal":
        raise ModelRefused("The model declined to review these frames.")
    if response.stop_reason == "max_tokens":
        raise ReportError("The report was cut off before it finished. Try a shorter clip.")
    text = "".join(block.text for block in response.content if block.type == "text")
    return text, response.usage


def generate_report(client, model, frames_b64, frame_times, discipline, context, athlete="", attempts=2):
    """Build a validated report. Malformed output is retried, up to `attempts` calls."""
    last_error = None
    for attempt in range(1, attempts + 1):
        text, usage = request_report(client, model, frames_b64, frame_times, discipline, context, athlete)
        try:
            return parse_report(text, frame_times, discipline, athlete), usage
        except ReportError as exc:
            log.warning("Report failed validation on attempt %s: %s", attempt, exc)
            last_error = exc
    raise last_error


def live_cue(client, model, frames_b64, discipline):
    content = []
    for i, data in enumerate(frames_b64, start=1):
        content.append({"type": "text", "text": f"Frame {i}/{len(frames_b64)}:"})
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": data},
        })
    content.append({"type": "text", "text": live_cue_prompt(discipline)})
    response = client.messages.create(
        model=model,
        max_tokens=CUE_MAX_TOKENS,
        messages=[{"role": "user", "content": content}],
    )
    if response.stop_reason == "refusal":
        return "Nothing clear yet. Keep the camera on the athlete."
    return "".join(block.text for block in response.content if block.type == "text").strip()


def discipline_label(key):
    return label_for(key)
