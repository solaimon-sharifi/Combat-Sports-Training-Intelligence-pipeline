"""Dashboard checks. Every report is a synthetic fixture defined in test_analytics.py, not real training data."""
import sqlite3

import pytest

import disciplines
from conftest import FakeResponse, valid_report_text
from test_analytics import conn, drill, finding, report, save  # noqa: F401 (conn is a fixture)
from test_report_deletion import make_review, store_video

HEADERS = {"X-Film-Room": "1"}


@pytest.fixture(autouse=True)
def synthetic_codecs_allowed(server, monkeypatch):
    monkeypatch.setattr(server.storage, "UNPLAYABLE_CODECS", set())


def dash(conn, **kwargs):
    import analytics

    return analytics.dashboard(conn, "owner", **kwargs)


# ---- Dashboard math from saved reports -----------------------------------------

def test_sessions_are_counted_per_discipline_and_date_range(conn):
    save(conn, "bjj", "2026-10-01", report("bjj"))
    save(conn, "bjj", "2026-10-09", report("bjj"))
    save(conn, "mma", "2026-10-05", report("mma"))
    everything = dash(conn)
    assert everything["sessions"] == 3
    assert {row["discipline"]: row["sessions"] for row in everything["by_discipline"]} == {"bjj": 2, "mma": 1}
    october_first_week = dash(conn, start="2026-10-01", end="2026-10-07")
    assert october_first_week["sessions"] == 2
    assert october_first_week["first_day"] == "2026-10-01"
    assert october_first_week["last_day"] == "2026-10-05"


def test_empty_database_gives_an_empty_dashboard(conn):
    data = dash(conn)
    assert data["sessions"] == 0
    assert data["recent"] == []
    assert data["recurring"] == []
    assert data["areas_needing_review"] == []
    assert data["drills"] == []
    assert data["first_day"] is None


def test_recent_sessions_are_newest_first_and_limited_to_five(conn):
    for day in range(1, 8):
        save(conn, "bjj", f"2026-10-0{day}", report("bjj"))
    recent = dash(conn)["recent"]
    assert len(recent) == 5
    assert [row["created_at"][:10] for row in recent] == [f"2026-10-0{d}" for d in range(7, 2, -1)]


def test_areas_needing_review_are_only_repeated_or_persistent(conn):
    for day in ("2026-10-01", "2026-10-02"):
        save(conn, "bjj", day, report("bjj", [finding("guard_retention")], ["guard_retention"]))
    save(conn, "bjj", "2026-10-03", report("bjj", [finding("escapes")], ["escapes"]))
    data = dash(conn)
    assert [row["category"] for row in data["areas_needing_review"]] == ["Guard retention"]
    assert {row["category"] for row in data["recurring"]} == {"Guard retention", "Escapes"}


def test_a_drill_is_listed_once_with_the_number_of_sessions_that_recommended_it(conn):
    for day in ("2026-10-01", "2026-10-02"):
        save(conn, "bjj", day, report("bjj", [finding("guard_retention")], ["guard_retention"],
                                      drills=[drill("guard_retention", "Hip escape drill")]))
    drills = dash(conn)["drills"]
    assert len(drills) == 1
    assert drills[0]["name"] == "Hip escape drill"
    assert drills[0]["sessions"] == 2
    assert drills[0]["last_recommended"] == "2026-10-02"


def test_sessions_without_judged_areas_are_reported_as_unknown_coverage(conn):
    save(conn, "bjj", "2026-10-01", report("bjj", [finding("guard_retention")]))
    save(conn, "bjj", "2026-10-02", report("bjj", [], ["guard_retention"]))
    assert dash(conn)["sessions_without_coverage"] == 1


def test_discipline_filter_limits_every_card(conn):
    save(conn, "bjj", "2026-10-01", report("bjj", [finding("guard_retention")], ["guard_retention"],
                                           drills=[drill("guard_retention")]))
    save(conn, "mma", "2026-10-02", report("mma", [finding("takedown_defense")], ["takedown_defense"]))
    data = dash(conn, discipline="mma")
    assert data["sessions"] == 1
    assert {row["discipline"] for row in data["recurring"]} == {"mma"}
    assert data["drills"] == []


# ---- Through the API --------------------------------------------------------------

def test_dashboard_api_matches_the_saved_reports(server, frame, fake_claude):
    fake_claude([FakeResponse(valid_report_text("bjj"))] * 2)
    client = server.app.test_client()
    make_review(client, frame, context="a")
    make_review(client, frame, context="b")
    data = client.get("/api/analytics/dashboard").get_json()
    assert data["sessions"] == 2
    assert data["recent"][0]["discipline_label"] == disciplines.label_for("bjj")
    assert data["recent"][0]["video_available"] is False


def test_dashboard_api_reports_a_stored_video_only_when_it_is_still_stored(server, frame, fake_claude, tmp_path):
    fake_claude([FakeResponse(valid_report_text("bjj"))])
    client = server.app.test_client()
    video_id = store_video(client, tmp_path)
    make_review(client, frame, video_id=video_id)
    assert client.get("/api/analytics/dashboard").get_json()["recent"][0]["video_available"] is True
    client.delete(f"/api/videos/{video_id}", headers=HEADERS)
    assert client.get("/api/analytics/dashboard").get_json()["recent"][0]["video_available"] is False


def test_dashboard_api_has_an_empty_state(server):
    data = server.app.test_client().get("/api/analytics/dashboard").get_json()
    assert data["sessions"] == 0
    assert data["recent"] == []


@pytest.mark.parametrize("query", [
    "discipline=kendo",
    "start=2026-13-01",
    "start=yesterday",
    "start=2026-10-09&end=2026-10-01",
])
def test_dashboard_api_rejects_bad_filters(server, query):
    response = server.app.test_client().get(f"/api/analytics/dashboard?{query}")
    assert response.status_code == 400
    assert response.get_json()["error"]


def test_dashboard_api_requires_login(server):
    anonymous = server.raw_client()
    assert anonymous.get("/api/analytics/dashboard").status_code == 401


def test_dashboard_numbers_disappear_when_a_report_is_deleted(server, frame, fake_claude):
    fake_claude([FakeResponse(valid_report_text("bjj"))] * 2)
    client = server.app.test_client()
    first = make_review(client, frame, context="a")
    make_review(client, frame, context="b")
    client.delete(f"/api/reviews/{first['id']}", headers=HEADERS)
    data = client.get("/api/analytics/dashboard").get_json()
    assert data["sessions"] == 1
    assert data["recurring"][0]["sessions_detected"] == 1
