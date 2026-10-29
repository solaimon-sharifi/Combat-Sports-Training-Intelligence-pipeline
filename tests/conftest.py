import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

SERVER_MODULES = ("server", "reports", "migrations")


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


def valid_report_text(frame_count=4):
    import json

    return json.dumps({
        "summary": "Closed guard with posture broken on the second frame.",
        "observations": [
            {"description": "Hips square to the partner in the closed guard.", "frames": [1, 2], "confidence": "observed"},
            {"description": "Grips may be inside the elbows.", "frames": [3], "confidence": "inferred"},
            {"description": "Partner is partly hidden.", "frames": [], "confidence": "observed"},
        ],
        "technical_findings": [
            {
                "area": "guard_defense",
                "finding": "Elbows drift away from the ribs.",
                "fix": "Keep elbows tight before opening the guard.",
                "frames": [3],
                "confidence": "inferred",
            },
        ],
        "drills": [
            {
                "name": "Elbow-tight guard frames",
                "addresses": "guard_defense",
                "instructions": "Hold closed guard with elbows touching the ribs for ten seconds.",
                "objective": "Keep the guard closed under pressure.",
                "reps_or_duration": "5 x 10 seconds",
            },
        ],
        "top_priorities": [
            {"title": "Elbow discipline", "reason": "Visible in frame 3.", "frames": [3]},
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
