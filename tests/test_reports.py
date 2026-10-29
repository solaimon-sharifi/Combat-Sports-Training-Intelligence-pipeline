import json

import pytest

from conftest import valid_report_text
from disciplines import DISCIPLINES, MAX_PRIORITIES, report_schema
from reports import ReportError, parse_report, to_plain_text, validate_frame_times

TIMES = [0.0, 6.5, 13.0, 19.5]
DISCIPLINE_KEYS = list(DISCIPLINES)


def load(data):
    return json.dumps(data)


@pytest.mark.parametrize("discipline", DISCIPLINE_KEYS)
def test_valid_report_is_accepted_for_every_discipline(discipline):
    report = parse_report(valid_report_text(discipline), TIMES, discipline)
    assert report["discipline"] == discipline
    assert report["discipline_label"] == DISCIPLINES[discipline]["label"]
    assert report["technical_findings"][0]["area"] in DISCIPLINES[discipline]["areas"]
    assert report["frames"][2] == {"number": 3, "time_seconds": 13.0, "label": "00:13.0"}


@pytest.mark.parametrize("discipline", DISCIPLINE_KEYS)
def test_schema_enums_list_exactly_this_disciplines_areas(discipline):
    schema = report_schema(discipline)
    areas = schema["properties"]["technical_findings"]["items"]["properties"]["area"]["enum"]
    drill_areas = schema["properties"]["drills"]["items"]["properties"]["addresses"]["enum"]
    assert set(areas) == set(DISCIPLINES[discipline]["areas"]) == set(drill_areas)


@pytest.mark.parametrize("discipline", DISCIPLINE_KEYS)
def test_finding_from_another_sport_is_rejected(discipline):
    other = next(d for d in DISCIPLINE_KEYS if d != discipline)
    foreign_area = next(a for a in DISCIPLINES[other]["areas"] if a not in DISCIPLINES[discipline]["areas"])
    data = json.loads(valid_report_text(discipline))
    data["technical_findings"][0]["area"] = foreign_area
    with pytest.raises(ReportError, match="not a"):
        parse_report(load(data), TIMES, discipline)


def test_observed_claim_without_frames_is_downgraded_to_uncertain():
    report = parse_report(valid_report_text("boxing"), TIMES, "boxing")
    hidden = next(o for o in report["observations"] if "partner is partly hidden" in o["description"])
    assert hidden["confidence"] == "uncertain"


def test_frame_references_outside_the_sample_are_dropped():
    data = json.loads(valid_report_text("mma"))
    data["observations"][0]["frames"] = [0, 2, 9, 2, "3", True]
    report = parse_report(load(data), TIMES, "mma")
    assert report["observations"][0]["frames"] == [2]


def test_drill_without_a_supporting_finding_is_dropped():
    data = json.loads(valid_report_text("kickboxing"))
    data["drills"][0]["addresses"] = "stance_guard"
    report = parse_report(load(data), TIMES, "kickboxing")
    assert report["drills"] == []


def test_uncertain_finding_does_not_support_a_drill():
    data = json.loads(valid_report_text("bjj"))
    data["technical_findings"][0]["confidence"] = "uncertain"
    report = parse_report(load(data), TIMES, "bjj")
    assert report["drills"] == []


def test_insufficient_evidence_clears_findings_priorities_and_drills():
    text = valid_report_text("muay_thai", evidence_status="insufficient")
    report = parse_report(text, TIMES, "muay_thai")
    assert report["evidence_status"] == "insufficient"
    assert report["technical_findings"] == []
    assert report["drills"] == []
    assert report["top_priorities"] == []
    assert report["observations"]


def test_limited_evidence_keeps_findings_and_states_the_limit():
    report = parse_report(valid_report_text("boxing", evidence_status="limited"), TIMES, "boxing")
    assert report["evidence_status"] == "limited"
    assert report["evidence_note"]
    assert report["technical_findings"]


def test_unknown_evidence_status_is_rejected():
    data = json.loads(valid_report_text("bjj"))
    data["evidence_status"] = "certain"
    with pytest.raises(ReportError, match="evidence status"):
        parse_report(load(data), TIMES, "bjj")


def test_more_than_three_priorities_are_trimmed():
    data = json.loads(valid_report_text("boxing"))
    data["top_priorities"] = [{"title": f"Priority {i}", "reason": "Visible.", "frames": [1]} for i in range(5)]
    report = parse_report(load(data), TIMES, "boxing")
    assert len(report["top_priorities"]) == MAX_PRIORITIES
    assert report["top_priorities"][0]["title"] == "Priority 0"


def test_unknown_confidence_is_rejected():
    data = json.loads(valid_report_text("boxing"))
    data["observations"][0]["confidence"] = "certain"
    with pytest.raises(ReportError, match="confidence"):
        parse_report(load(data), TIMES, "boxing")


def test_missing_required_key_is_rejected():
    data = json.loads(valid_report_text("boxing"))
    del data["evidence_status"]
    with pytest.raises(ReportError, match="evidence_status"):
        parse_report(load(data), TIMES, "boxing")


def test_invalid_json_is_rejected():
    with pytest.raises(ReportError, match="not valid JSON"):
        parse_report("Observations: plain text", TIMES, "boxing")


def test_plain_text_export_names_the_discipline_and_keeps_the_caveat():
    report = parse_report(valid_report_text("mma"), TIMES, "mma")
    text = to_plain_text(report)
    assert text.startswith("MMA report")
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


@pytest.mark.parametrize("phrase", [
    "The athlete always drops the hands after punching.",
    "Power is lost when the stance is narrow.",
    "This is a habit that repeats across the clip.",
    "The athlete never keeps the guard up.",
])
def test_habit_and_power_claims_are_removed_from_observations_and_findings(phrase):
    data = json.loads(valid_report_text("boxing"))
    data["observations"][0]["description"] = phrase
    data["technical_findings"][0]["finding"] = phrase
    report = parse_report(load(data), TIMES, "boxing")
    assert all(phrase not in o["description"] for o in report["observations"])
    assert report["technical_findings"] == []


def test_drill_and_fix_text_are_not_filtered_for_wording():
    data = json.loads(valid_report_text("boxing"))
    data["technical_findings"][0]["fix"] = "Keep the hands up at all times for more power."
    report = parse_report(load(data), TIMES, "boxing")
    assert report["technical_findings"][0]["fix"].startswith("Keep the hands up")
