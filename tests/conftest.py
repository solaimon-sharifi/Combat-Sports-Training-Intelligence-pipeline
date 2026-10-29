import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

SERVER_MODULES = ("server", "coaching", "reports", "disciplines", "migrations")


class FakeBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class FakeUsage:
    input_tokens = 1200
    output_tokens = 450


class FakeResponse:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [FakeBlock(text)]
        self.stop_reason = stop_reason
        self.usage = FakeUsage()


class FakeMessages:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        return item


class FakeClient:
    def __init__(self, script):
        self.messages = FakeMessages(script)


AREA_FOR = {
    "boxing": "punching",
    "kickboxing": "kicking",
    "muay_thai": "clinch_positioning",
    "mma": "takedown_defense",
    "bjj": "guard_retention",
}


def valid_report_text(discipline="bjj", evidence_status="sufficient"):
    """A schema-shaped report for one discipline, using only that discipline's areas."""
    import json

    area = AREA_FOR[discipline]
    insufficient = evidence_status == "insufficient"
    return json.dumps({
        "evidence_status": evidence_status,
        "evidence_note": "Partner partly hidden in frame 3." if evidence_status != "sufficient" else "",
        "summary": "Visible posture is reasonable in the first frames, with a gap in frame 3.",
        "observations": [
            {"description": "The athlete's head and shoulders are visible in frames 1 and 2.", "frames": [1, 2], "confidence": "observed"},
            {"description": "Weight may sit back in frame 3.", "frames": [3], "confidence": "inferred"},
            {"description": "The partner is partly hidden.", "frames": [], "confidence": "observed"},
        ],
        "technical_findings": [] if insufficient else [
            {
                "area": area,
                "finding": "The posture breaks down in frame 3.",
                "fix": "Keep the head and chest over the hips before the next movement.",
                "frames": [3],
                "confidence": "inferred",
            },
        ],
        "drills": [] if insufficient else [
            {
                "name": "Posture hold",
                "addresses": area,
                "instructions": "Hold the position for ten seconds and reset.",
                "objective": "Keep posture under pressure.",
                "reps_or_duration": "5 x 10 seconds",
            },
        ],
        "top_priorities": [] if insufficient else [
            {"title": "Posture under pressure", "reason": "Visible in frame 3.", "frames": [3]},
        ],
        "sampling_caveat": "Four frames cannot show transitions between positions.",
    })


@pytest.fixture
def server(tmp_path, monkeypatch):
    """Import a fresh server module pointed at a temporary database."""
    monkeypatch.setenv("FILM_ROOM_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-SENTINEL-do-not-leak")
    monkeypatch.delenv("APP_TOKEN", raising=False)
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "1000")
    monkeypatch.setenv("REVIEW_MAX_FRAMES", "16")
    monkeypatch.setenv("REVIEW_MIN_FRAMES", "4")
    monkeypatch.setenv("FRAME_INTERVAL_SECONDS", "8")
    for name in SERVER_MODULES:
        sys.modules.pop(name, None)
    import server as module

    module.app.config["TESTING"] = True
    module._client = None
    yield module
    for name in SERVER_MODULES:
        sys.modules.pop(name, None)


@pytest.fixture
def fake_claude(server, monkeypatch):
    """Route server.claude() to a scripted fake. Call with a list of responses or errors."""
    def install(script):
        client = FakeClient(script)
        monkeypatch.setattr(server, "claude", lambda: client)
        return client
    return install


@pytest.fixture
def frame():
    """A tiny but valid JPEG data URL, sized like a real sampled frame."""
    import base64

    import cv2
    import numpy as np

    image = np.full((48, 64, 3), 140, dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", image)
    assert ok
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("ascii")
