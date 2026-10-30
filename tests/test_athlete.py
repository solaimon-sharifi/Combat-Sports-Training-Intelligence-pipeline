import json
import sqlite3

from conftest import FakeResponse, valid_report_text
from reports import parse_report

TIMES = [0.0, 6.0, 12.0, 18.0]


def report_with(**overrides):
    data = json.loads(valid_report_text("bjj"))
    data.update(overrides)
    return data


def test_no_athlete_named_keeps_every_observation():
    report = parse_report(json.dumps(report_with()), TIMES, "bjj", athlete="")
    assert report["athlete_status"] == "not_requested"
    assert report["observations"]
    assert report["technical_findings"]


def test_uncertain_identity_withholds_all_attributed_content():
    data = report_with(athlete_status="uncertain", athlete_frames=[1],
                       athlete_note="Two people in white look similar in every frame.")
    report = parse_report(json.dumps(data), TIMES, "bjj", athlete="the fighter wearing white")
    assert report["athlete_status"] == "uncertain"
    assert report["observations"] == []
    assert report["technical_findings"] == []
    assert report["drills"] == []
    assert report["top_priorities"] == []
    assert "similar" in report["athlete_note"]


def test_not_found_withholds_all_attributed_content():
    data = report_with(athlete_status="not_found", athlete_frames=[], athlete_note="The described athlete is not visible.")
    report = parse_report(json.dumps(data), TIMES, "bjj", athlete="the fighter in red")
    assert report["athlete_status"] == "not_found"
    assert report["observations"] == [] and report["technical_findings"] == []


def test_identified_athlete_keeps_observations_only_from_their_frames():
    data = json.loads(valid_report_text("bjj"))
    data["athlete_status"] = "identified"
    data["athlete_frames"] = [1, 2]
    data["observations"][1]["frames"] = [3]
    data["observations"][1]["confidence"] = "inferred"
    report = parse_report(json.dumps(data), TIMES, "bjj", athlete="the fighter wearing white")
    assert report["athlete_status"] == "identified"
    assert report["athlete_frames"] == [1, 2]
    descriptions = [o["description"] for o in report["observations"]]
    assert "Weight may sit back in frame 3." not in descriptions
    assert any("head and shoulders" in d for d in descriptions)


def test_identified_without_any_identified_frame_becomes_uncertain():
    data = report_with(athlete_status="identified", athlete_frames=[], athlete_note="Identified in no frame.")
    report = parse_report(json.dumps(data), TIMES, "bjj", athlete="the fighter wearing white")
    assert report["athlete_status"] == "uncertain"
    assert report["observations"] == []


def test_athlete_named_but_status_not_requested_is_treated_as_unattributed():
    report = parse_report(json.dumps(report_with()), TIMES, "bjj", athlete="the fighter wearing white")
    assert report["observations"] == []
    assert report["technical_findings"] == []


def test_unknown_athlete_status_is_rejected():
    import pytest
    import reports

    with pytest.raises(reports.ReportError, match="athlete status"):
        reports.parse_report(json.dumps(report_with(athlete_status="maybe")), TIMES, "bjj", athlete="someone")


def test_athlete_description_reaches_the_prompt_and_is_stored(server, frame, fake_claude):
    client = fake_claude([FakeResponse(valid_report_text("bjj"))])
    response = server.app.test_client().post("/api/review", json={
        "mode": "upload", "discipline": "bjj", "frames": [frame], "frameTimes": [0.0],
        "context": "", "athleteDescription": "the fighter wearing white",
    })
    assert response.status_code == 200
    text = " ".join(b.get("text", "") for b in client.messages.calls[0]["messages"][0]["content"])
    assert "the fighter wearing white" in text
    with sqlite3.connect(server.DB_PATH) as conn:
        stored = conn.execute("SELECT athlete_description FROM film_reviews").fetchone()[0]
    assert stored == "the fighter wearing white"


def test_different_athlete_descriptions_do_not_share_a_cached_report(server, frame, fake_claude):
    client = fake_claude([FakeResponse(valid_report_text("bjj")), FakeResponse(valid_report_text("bjj"))])
    app = server.app.test_client()
    base = {"mode": "upload", "discipline": "bjj", "frames": [frame], "frameTimes": [0.0], "context": ""}
    app.post("/api/review", json=dict(base, athleteDescription="the fighter wearing white"))
    second = app.post("/api/review", json=dict(base, athleteDescription="the fighter wearing red")).get_json()
    assert len(client.messages.calls) == 2
    assert second["cached"] is False


def test_athlete_description_is_length_limited_and_tag_stripped(server, frame, fake_claude):
    client = fake_claude([FakeResponse(valid_report_text("bjj"))])
    app = server.app.test_client()
    app.post("/api/review", json={
        "mode": "upload", "discipline": "bjj", "frames": [frame], "frameTimes": [0.0], "context": "",
        "athleteDescription": "<b>" + "x" * 500,
    })
    text = " ".join(b.get("text", "") for b in client.messages.calls[0]["messages"][0]["content"])
    assert "<b>" not in text
    assert "x" * 201 not in text
