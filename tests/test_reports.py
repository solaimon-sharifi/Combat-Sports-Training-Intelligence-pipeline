import json

import pytest

from conftest import valid_report_text
from reports import AREAS, MAX_PRIORITIES, REPORT_SCHEMA, ReportError, build_prompt, parse_report, to_plain_text, validate_frame_times

TIMES = [0.0, 6.5, 13.0, 19.5]


def load(**overrides):
    data = json.loads(valid_report_text())
    data.update(overrides)
    return json.dumps(data)


def test_valid_report_is_accepted_with_frame_times_attached():
    report = parse_report(valid_report_text(), TIMES)
    assert report["summary"].startswith("Closed guard")
    assert report["frames"][2] == {"number": 3, "time_seconds": 13.0, "label": "00:13.0"}
    assert report["technical_findings"][0]["area"] == "guard_defense"


def test_observed_claim_without_frames_is_downgraded_to_uncertain():
    report = parse_report(valid_report_text(), TIMES)
    partly_hidden = next(o for o in report["observations"] if "partly hidden" in o["description"])
    assert partly_hidden["confidence"] == "uncertain"


def test_frame_references_outside_the_sample_are_dropped():
    data = json.loads(valid_report_text())
    data["observations"][0]["frames"] = [0, 2, 9, 2, "3", True]
    report = parse_report(json.dumps(data), TIMES)
    assert report["observations"][0]["frames"] == [2]


def test_more_than_three_priorities_are_trimmed_to_the_first_three():
    data = json.loads(valid_report_text())
    data["top_priorities"] = [
        {"title": f"Priority {i}", "reason": "Visible.", "frames": [1]} for i in range(5)
    ]
    report = parse_report(json.dumps(data), TIMES)
    assert len(report["top_priorities"]) == MAX_PRIORITIES
    assert report["top_priorities"][0]["title"] == "Priority 0"


def test_empty_priorities_are_allowed_when_footage_does_not_support_them():
    data = json.loads(valid_report_text())
    data["top_priorities"] = []
    data["drills"] = []
    report = parse_report(json.dumps(data), TIMES)
    assert report["top_priorities"] == []
    assert report["drills"] == []


def test_unknown_area_is_rejected():
    data = json.loads(valid_report_text())
    data["technical_findings"][0]["area"] = "speed"
    with pytest.raises(ReportError, match="unknown area"):
        parse_report(json.dumps(data), TIMES)


def test_unknown_confidence_is_rejected():
    data = json.loads(valid_report_text())
    data["observations"][0]["confidence"] = "certain"
    with pytest.raises(ReportError, match="confidence"):
        parse_report(json.dumps(data), TIMES)


def test_missing_required_key_is_rejected():
    data = json.loads(valid_report_text())
    del data["sampling_caveat"]
    with pytest.raises(ReportError, match="sampling_caveat"):
        parse_report(json.dumps(data), TIMES)


def test_invalid_json_is_rejected():
    with pytest.raises(ReportError, match="not valid JSON"):
        parse_report("Observations: this is plain text", TIMES)


def test_schema_covers_every_required_section_and_closes_objects():
    assert set(REPORT_SCHEMA["required"]) == {
        "summary", "observations", "technical_findings", "drills", "top_priorities", "sampling_caveat",
    }
    assert REPORT_SCHEMA["additionalProperties"] is False
    area_enum = REPORT_SCHEMA["properties"]["technical_findings"]["items"]["properties"]["area"]["enum"]
    assert area_enum == AREAS


def test_prompt_states_frame_count_context_and_the_no_speed_rule():
    prompt = build_prompt(16, "Closed guard, bigger partner")
    assert "F16" in prompt
    assert "Closed guard, bigger partner" in prompt
    assert "speed, power, timing" in prompt
    assert "(none given)" in build_prompt(4, "")


def test_plain_text_export_keeps_the_caveat_and_confidence_labels():
    report = parse_report(valid_report_text(), TIMES)
    text = to_plain_text(report)
    assert "[observed]" in text
    assert text.rstrip().endswith(report["sampling_caveat"])


@pytest.mark.parametrize("raw, count", [
    ([0.0, 1.0], 3),
    ([0.0, "1"], 2),
    ([0.0, float("nan")], 2),
    ([-1.0, 2.0], 2),
    ([True, 2.0], 2),
    ([3.0, 1.0], 2),
])
def test_frame_times_are_validated(raw, count):
    with pytest.raises(ValueError):
        validate_frame_times(raw, count)


def test_valid_frame_times_are_returned_as_floats():
    assert validate_frame_times([0, 2.5, 7], 3) == [0.0, 2.5, 7.0]
