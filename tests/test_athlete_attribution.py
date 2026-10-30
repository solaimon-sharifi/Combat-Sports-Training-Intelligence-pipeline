import json

import pytest

from conftest import valid_report_text
from reports import parse_report

TIMES = [0.0, 6.0, 12.0, 18.0]
NAME = "the fighter wearing white"


def identified(frames, **extra):
    data = json.loads(valid_report_text("bjj"))
    data["athlete_status"] = "identified"
    data["athlete_frames"] = frames
    data.update(extra)
    return data


def parse(data, athlete=NAME):
    return parse_report(json.dumps(data), TIMES, "bjj", athlete=athlete)


def test_identified_summary_counts_come_from_the_frames():
    report = parse(identified([1, 3, 4]))
    assert report["athlete_frame_count"] == 3
    assert report["athlete_total_frames"] == 4
    assert report["athlete_summary"] == "Identified in 3 of 4 sampled frames: F01, F03, F04."
    assert "not continuous tracking" in report["athlete_method"]


def test_partial_identification_shows_an_uncertainty_warning():
    report = parse(identified([1, 2]))
    assert "only 2 of 4" in report["athlete_warning"]
    assert "cite only those frames" in report["athlete_warning"]


def test_full_identification_has_no_warning():
    report = parse(identified([1, 2, 3, 4]))
    assert report["athlete_summary"] == "Identified in all 4 sampled frames."
    assert report["athlete_warning"] == ""


def test_a_single_identified_frame_is_not_enough_and_counts_as_uncertain():
    report = parse(identified([2]))
    assert report["athlete_status"] == "uncertain"
    assert report["observations"] == []
    assert report["athlete_warning"]


def test_identified_athlete_never_keeps_an_observation_without_frames():
    data = identified([1, 2], technical_findings=[])
    data["observations"] = [{"description": "The athlete looks settled.", "frames": [], "confidence": "observed"}]
    report = parse(data)
    assert all("looks settled" not in o["description"] for o in report["observations"])


def test_summary_is_built_by_the_server_not_the_model():
    data = identified([1, 2], evidence_note="The athlete is in most frames.")
    report = parse(data)
    assert "Identified in 2 of 4" in report["athlete_summary"]
    assert "most" not in report["athlete_summary"]


@pytest.mark.parametrize("phrase", [
    "I tracked the athlete across the clip.",
    "The athlete was followed continuously.",
    "The athlete is visible throughout the whole clip.",
])
def test_continuous_tracking_claims_are_removed_from_observations(phrase):
    data = identified([1, 2])
    data["observations"][0]["description"] = phrase
    report = parse(data)
    assert all(phrase not in o["description"] for o in report["observations"])


def test_tracking_claim_in_the_athlete_note_is_replaced():
    report = parse(identified([1, 2], athlete_note="I tracked the athlete throughout."))
    assert "tracked" not in report["athlete_note"].lower()
    assert "frame" in report["athlete_note"].lower()


def test_no_athlete_named_has_no_athlete_summary():
    report = parse(json.loads(valid_report_text("bjj")), athlete="")
    assert report["athlete_summary"] == ""
    assert report["athlete_warning"] == ""


def test_not_found_and_uncertain_each_state_the_outcome():
    not_found = json.loads(valid_report_text("bjj"))
    not_found.update(athlete_status="not_found", athlete_frames=[], athlete_note="Not visible.")
    report = parse(not_found, athlete="the fighter in red")
    assert report["athlete_summary"].startswith("The athlete described was not found")
    assert "No observations" in report["athlete_warning"]

    uncertain = json.loads(valid_report_text("bjj"))
    uncertain.update(athlete_status="uncertain", athlete_frames=[], athlete_note="Two similar people.")
    report = parse(uncertain)
    assert "could not be identified reliably" in report["athlete_summary"]


def test_attributed_content_for_frames_outside_the_identified_set_is_removed():
    data = identified([1, 2])
    data["technical_findings"][0]["frames"] = [4]
    data["top_priorities"][0]["frames"] = [4]
    report = parse(data)
    assert report["technical_findings"] == []
    assert report["top_priorities"] == []


def test_wording_is_consistent_between_the_summary_and_the_counts():
    for frames in ([1, 2], [1, 2, 3], [1, 2, 3, 4]):
        report = parse(identified(frames))
        assert str(len(frames)) in report["athlete_summary"]
        assert report["athlete_frame_count"] == len(frames)
