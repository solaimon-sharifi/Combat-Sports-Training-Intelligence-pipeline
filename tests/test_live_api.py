"""One real Claude call that checks the structured-report path end to end.

Skipped unless RUN_LIVE_API=1. It uses the key from the environment and never
prints it. It spends a small amount of API credit, and the frames are plain
grey test images, so it checks the plumbing, not analysis quality.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_API") != "1",
    reason="set RUN_LIVE_API=1 to make one real API call",
)


def test_real_api_returns_a_validated_structured_report(tmp_path, monkeypatch, frame):
    import anthropic

    monkeypatch.setenv("FILM_ROOM_DB", str(tmp_path / "live.db"))
    for name in ("server", "reports", "migrations"):
        sys.modules.pop(name, None)
    import server

    assert os.environ.get("ANTHROPIC_API_KEY"), "ANTHROPIC_API_KEY is not set in this environment"
    bare = frame.split(",", 1)[1]
    text, usage = server.request_report([bare, bare], [0.0, 8.0], "plumbing check")
    report = server.parse_report(text, [0.0, 8.0])

    assert report["summary"]
    assert report["sampling_caveat"]
    assert len(report["top_priorities"]) <= 3
    print(f"\nmodel={server.MODEL} input_tokens={usage.input_tokens} output_tokens={usage.output_tokens}")
    assert isinstance(usage.input_tokens, int) and usage.input_tokens > 0
    assert isinstance(anthropic.__version__, str)
