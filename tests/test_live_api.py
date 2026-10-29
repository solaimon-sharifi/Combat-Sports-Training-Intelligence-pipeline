"""Real Claude calls that check the structured-report path end to end.

Skipped unless RUN_LIVE_API=1. Each test uses the key from the environment and
never prints it. Each spends a small amount of API credit. The frames are plain
grey test images, so these check plumbing and sport-specific output shape, not
analysis quality. Use real footage (see README) to judge quality.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_API") != "1",
    reason="set RUN_LIVE_API=1 to make real API calls",
)


@pytest.mark.parametrize("discipline", ["boxing", "bjj"])
def test_real_api_returns_a_validated_report_for_the_selected_sport(discipline, frame):
    import anthropic

    import coaching
    from disciplines import DISCIPLINES
    from reports import parse_report

    assert os.environ.get("ANTHROPIC_API_KEY"), "ANTHROPIC_API_KEY is not set in this environment"
    bare = frame.split(",", 1)[1]
    client = anthropic.Anthropic(max_retries=2, timeout=180.0)
    text, usage = coaching.request_report(
        client, os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5"),
        [bare, bare], [0.0, 8.0], discipline, "plumbing check",
    )
    report = parse_report(text, [0.0, 8.0], discipline)

    assert report["discipline"] == discipline
    assert report["evidence_status"] in ("sufficient", "limited", "insufficient")
    assert report["sampling_caveat"]
    assert len(report["top_priorities"]) <= 3
    for finding in report["technical_findings"]:
        assert finding["area"] in DISCIPLINES[discipline]["areas"]
    print(f"\n{discipline}: input_tokens={usage.input_tokens} output_tokens={usage.output_tokens}")
