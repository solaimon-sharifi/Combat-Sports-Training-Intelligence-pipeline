"""Comparison, drill library, practice log and history checks. Every report is a synthetic fixture."""
import json
import sqlite3

import pytest

import disciplines
from conftest import FakeResponse, TEST_TOKEN, valid_report_text
from test_analytics import conn, drill, finding, report, save  # noqa: F401 (conn is a fixture)
from test_report_deletion import make_review

HEADERS = {"X-Film-Room": "1"}


@pytest.fixture(autouse=True)
def synthetic_codecs_allowed(server, monkeypatch):
    monkeypatch.setattr(server.storage, "UNPLAYABLE_CODECS", set())


# ---- Stage 3: recurring issues -------------------------------------------------

def test_issues_endpoint_filters_by_category_and_lists_every_category_by_default(server, frame, fake_claude):
    fake_claude([FakeResponse(valid_report_text("bjj"))] * 2)
    client = server.app.test_client()
    make_review(client, frame, context="a")
    make_review(client, frame, context="b")
    everything = client.get("/api/analytics/issues").get_json()
    assert everything["categories"] == disciplines.CATEGORIES
    assert len(everything["issues"]) >= 1
    category = everything["issues"][0]["category"]
    narrowed = client.get(f"/api/analytics/issues?category={category}").get_json()["issues"]
    assert narrowed and {item["category"] for item in narrowed} == {category}


def test_issues_endpoint_rejects_an_unknown_category(server):
    assert server.app.test_client().get("/api/analytics/issues?category=Juggling").status_code == 400


def test_uncertainty_note_explains_why_a_pattern_is_not_a_diagnosis(conn):
    import analytics

    for day in ("2026-10-01", "2026-10-02", "2026-10-03"):
        save(conn, "bjj", day, report("bjj", [finding("guard_retention")], ["guard_retention"]))
    row = next(r for r in analytics.recurring_issues(conn) if r["category"] == "Guard retention")
    assert row["status"] == "multi_day"
    assert "not a diagnosis" in row["note"]


# ---- Stage 4: comparison ----------------------------------------------------------

def make_pair(conn):
    earlier = save(conn, "bjj", "2026-10-01", report(
        "bjj", [finding("guard_retention", "observed", frames=(2,), finding_text="Hips drop in frame 2.")],
        ["guard_retention", "escapes", "transitions"], drills=[drill("guard_retention", "Hip escape")]))
    newer = save(conn, "bjj", "2026-10-08", report(
        "bjj", [finding("transitions", "inferred", frames=(1,), finding_text="Weight shifts early.")],
        ["guard_retention", "escapes", "transitions"], drills=[drill("guard_retention", "Knee shield")]))
    return earlier, newer


def test_comparison_sorts_categories_into_earlier_only_newer_only_and_in_both(conn):
    import analytics

    earlier, newer = make_pair(conn)
    result = analytics.compare(conn, "owner", earlier, newer)
    changes = {item["category"]: item["change"] for item in result["categories"]}
    assert changes["Guard retention"] == "earlier_only"
    assert changes["Transitions"] == "newer_only"
    assert changes["Escapes"] == "not_detected_both"


def test_comparison_never_claims_improvement_from_an_absent_flag(conn):
    import analytics

    earlier, newer = make_pair(conn)
    result = analytics.compare(conn, "owner", earlier, newer)
    earlier_only = next(item for item in result["categories"] if item["change"] == "earlier_only")
    assert "not evidence the area is fixed" in earlier_only["change_note"]
    assert "do not show whether technique improved" in result["note"]
    assert "improved" not in earlier_only["change_note"].replace("not evidence of improvement", "")


def test_comparison_marks_areas_that_cannot_be_compared(conn):
    import analytics

    earlier = save(conn, "bjj", "2026-10-01", report("bjj", [], ["escapes"]))
    newer = save(conn, "bjj", "2026-10-02", report("bjj", [finding("guard_retention")]))
    result = analytics.compare(conn, "owner", earlier, newer)
    change = {item["category"]: item["change"] for item in result["categories"]}
    assert change["Escapes"] == "cannot_compare"
    assert change["Guard retention"] == "cannot_compare"


def test_comparison_warns_when_frame_counts_differ_and_shows_drill_differences(conn):
    import analytics

    earlier, newer = make_pair(conn)
    conn.execute("UPDATE film_reviews SET frame_count = 9 WHERE review_id = ?", (newer,))
    conn.commit()
    result = analytics.compare(conn, "owner", earlier, newer)
    assert any("sampled different numbers of frames" in w for w in result["warnings"])
    guard = next(item for item in result["categories"] if item["category"] == "Guard retention")
    assert guard["drills_changed"] is True


def test_comparison_refuses_different_sports_and_the_same_session(conn):
    import analytics

    bjj = save(conn, "bjj", "2026-10-01", report("bjj"))
    mma = save(conn, "mma", "2026-10-02", report("mma"))
    with pytest.raises(ValueError):
        analytics.compare(conn, "owner", bjj, mma)
    with pytest.raises(ValueError):
        analytics.compare(conn, "owner", bjj, bjj)
    with pytest.raises(LookupError):
        analytics.compare(conn, "owner", bjj, 999)


def test_comparison_endpoint_returns_video_links_only_for_stored_videos(server, frame, fake_claude):
    fake_claude([FakeResponse(valid_report_text("bjj"))] * 2)
    client = server.app.test_client()
    first = make_review(client, frame, context="a")
    second = make_review(client, frame, context="b")
    data = client.get(f"/api/analytics/compare?first={first['id']}&second={second['id']}").get_json()
    assert data["earlier"]["video_available"] is False
    assert data["newer"]["video_id"] is None


def test_comparison_endpoint_validates_its_input(server):
    client = server.app.test_client()
    assert client.get("/api/analytics/compare?first=a&second=2").status_code == 400
    assert client.get("/api/analytics/compare?first=1&second=1").status_code == 400
    assert client.get("/api/analytics/compare?first=1&second=2").status_code == 404


# ---- Stage 5: drill library and practice log ---------------------------------------

def test_drill_library_links_each_drill_to_its_category_status_and_findings(conn):
    import analytics

    for day in ("2026-10-01", "2026-10-02", "2026-10-03"):
        save(conn, "bjj", day, report("bjj", [finding("guard_retention", finding_text="Hips drop.")],
                                      ["guard_retention"], drills=[drill("guard_retention", "Hip escape")]))
    library = analytics.drill_library(conn)["drills"]
    assert len(library) == 1
    entry = library[0]
    assert entry["sessions"] == 3
    assert entry["category_status"] == "multi_day"
    assert entry["linked_findings"] == ["Hips drop."]
    assert entry["practice"] == {"count": 0, "last": None, "recent": []}


def test_a_drill_repeated_within_one_report_counts_as_one_session(conn):
    import analytics

    twice = [drill("guard_retention", "Hip escape"), drill("guard_retention", "Hip escape")]
    save(conn, "bjj", "2026-10-01", report("bjj", [], ["guard_retention"], drills=twice))
    entry = analytics.drill_library(conn)["drills"][0]
    assert entry["sessions"] == 1


def test_practice_log_is_stored_and_shown_without_claiming_improvement(conn):
    import analytics

    save(conn, "bjj", "2026-10-01", report("bjj", [], ["guard_retention"], drills=[drill("guard_retention", "Hip escape")]))
    key = analytics.drill_library(conn)["drills"][0]["drill_key"]
    analytics.add_practice(conn, key, "2026-10-02", "Felt smoother")
    conn.commit()
    entry = analytics.drill_library(conn)["drills"][0]
    assert entry["practice"]["count"] == 1
    assert entry["practice"]["last"] == "2026-10-02"
    assert "not evidence that technique improved" in analytics.drill_library(conn)["note"]


def test_practice_for_an_unknown_drill_is_refused(conn):
    import analytics

    with pytest.raises(LookupError):
        analytics.add_practice(conn, "bjj|Striking|never recommended", "2026-10-02", "")


def test_practice_endpoint_validates_date_note_and_drill(server, frame, fake_claude):
    fake_claude([FakeResponse(valid_report_text("bjj"))])
    client = server.app.test_client()
    make_review(client, frame)
    drill_key = client.get("/api/analytics/drills").get_json()["drills"][0]["drill_key"]
    assert client.post("/api/drill-practice", json={"drill_key": "nope"}, headers=HEADERS).status_code == 404
    assert client.post("/api/drill-practice", json={"drill_key": drill_key, "practiced_on": "2999-01-01"},
                       headers=HEADERS).status_code == 400
    assert client.post("/api/drill-practice", json={"drill_key": drill_key, "practiced_on": "soon"},
                       headers=HEADERS).status_code == 400
    assert client.post("/api/drill-practice", json={"drill_key": drill_key, "note": "x" * 301},
                       headers=HEADERS).status_code == 400
    ok = client.post("/api/drill-practice", json={"drill_key": drill_key, "practiced_on": "2026-10-02"},
                     headers=HEADERS)
    assert ok.status_code == 201


def test_practice_entries_can_be_removed(server, frame, fake_claude):
    fake_claude([FakeResponse(valid_report_text("bjj"))])
    client = server.app.test_client()
    make_review(client, frame)
    drill_key = client.get("/api/analytics/drills").get_json()["drills"][0]["drill_key"]
    practice_id = client.post("/api/drill-practice", json={"drill_key": drill_key, "practiced_on": "2026-10-02"},
                              headers=HEADERS).get_json()["id"]
    assert client.delete(f"/api/drill-practice/{practice_id}", headers=HEADERS).status_code == 200
    assert client.delete(f"/api/drill-practice/{practice_id}", headers=HEADERS).status_code == 404


def test_deleting_the_last_report_removes_the_drill_from_the_library_but_keeps_the_log(server, frame, fake_claude):
    fake_claude([FakeResponse(valid_report_text("bjj"))])
    client = server.app.test_client()
    review = make_review(client, frame)
    drill_key = client.get("/api/analytics/drills").get_json()["drills"][0]["drill_key"]
    client.post("/api/drill-practice", json={"drill_key": drill_key, "practiced_on": "2026-10-02"}, headers=HEADERS)
    client.delete(f"/api/reviews/{review['id']}", headers=HEADERS)
    assert client.get("/api/analytics/drills").get_json()["drills"] == []
    with sqlite3.connect(server.DB_PATH) as conn:
        assert conn.execute("SELECT COUNT(*) FROM drill_practice").fetchone()[0] == 1


# ---- Stage 6: history --------------------------------------------------------------

def test_history_filters_by_category_drill_text_and_date(server, frame, fake_claude):
    fake_claude([FakeResponse(valid_report_text("bjj"))] * 2)
    client = server.app.test_client()
    make_review(client, frame, context="closed guard practice")
    make_review(client, frame, context="standing drills")
    all_rows = client.get("/api/history").get_json()
    assert all_rows["total"] == 2
    area = json.loads(valid_report_text("bjj"))["technical_findings"][0]["area"]
    category = disciplines.category_for("bjj", area)
    by_category = client.get(f"/api/history?category={category}").get_json()
    assert by_category["total"] == 2
    by_text = client.get("/api/history?q=closed").get_json()
    assert [item["context"] for item in by_text["items"]] == ["closed guard practice"]
    assert client.get("/api/history?q=%25").get_json()["total"] == 0
    assert client.get("/api/history?start=2999-01-01").get_json()["total"] == 0


def test_history_recurring_filter_only_returns_reports_with_recurring_categories(conn):
    import analytics

    for day in ("2026-10-01", "2026-10-02"):
        save(conn, "bjj", day, report("bjj", [finding("guard_retention")], ["guard_retention"]))
    save(conn, "bjj", "2026-10-03", report("bjj", [finding("escapes")], ["escapes"]))
    result = analytics.history(conn, "owner", {"recurring": True})
    assert result["total"] == 2
    assert all(item["findings"] == 1 for item in result["items"])


def test_history_pages_and_reports_a_total(conn):
    import analytics

    for day in range(1, 4):
        save(conn, "bjj", f"2026-10-0{day}", report("bjj"))
    page = analytics.history(conn, "owner", {}, limit=2, offset=0)
    assert page["total"] == 3
    assert len(page["items"]) == 2


def test_history_escapes_wildcards_in_search_text(conn):
    import analytics

    save(conn, "bjj", "2026-10-01", report("bjj"))
    assert analytics.history(conn, "owner", {"text": "_"})["total"] == 0
    assert analytics.history(conn, "owner", {"text": "%"})["total"] == 0


def test_review_detail_returns_the_full_saved_report(server, frame, fake_claude):
    fake_claude([FakeResponse(valid_report_text("bjj"))])
    client = server.app.test_client()
    review = make_review(client, frame)
    detail = client.get(f"/api/reviews/{review['id']}").get_json()
    assert detail["report"]["discipline"] == "bjj"
    assert detail["video_available"] is False
    assert client.get("/api/reviews/999").status_code == 404


def test_history_and_detail_render_legacy_rows_without_a_report(server):
    with sqlite3.connect(server.DB_PATH) as conn:
        conn.execute(
            "INSERT INTO film_reviews (created_at, mode, frame_count, feedback, discipline) "
            "VALUES ('2026-07-01T10:00:00', 'upload', 4, 'Feedback only', 'bjj')"
        )
        conn.commit()
    client = server.app.test_client()
    items = client.get("/api/history").get_json()["items"]
    assert items[0]["findings"] == 0
    detail = client.get(f"/api/reviews/{items[0]['id']}").get_json()
    assert detail["report"] is None
    assert detail["feedback"] == "Feedback only"


@pytest.mark.parametrize("path", [
    "/api/analytics/dashboard",
    "/api/analytics/issues",
    "/api/analytics/compare?first=1&second=2",
    "/api/analytics/drills",
    "/api/history",
    "/api/reviews/1",
])
def test_new_read_routes_require_login(server, path):
    assert server.raw_client().get(path).status_code == 401


def test_practice_writes_require_login_and_the_custom_header(server):
    anonymous = server.raw_client()
    assert anonymous.post("/api/drill-practice", json={"drill_key": "x"}, headers=HEADERS).status_code == 401
    assert anonymous.delete("/api/drill-practice/1", headers=HEADERS).status_code == 401
    session = server.raw_client()
    session.post("/api/login", json={"code": TEST_TOKEN}, headers=HEADERS)
    assert session.post("/api/drill-practice", json={"drill_key": "x"}).status_code == 403
