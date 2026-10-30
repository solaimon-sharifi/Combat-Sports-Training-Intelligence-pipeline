"""Analytics checks. All reports here are synthetic fixtures built in this file, not real footage or sessions."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from conftest import FakeResponse, valid_report_text

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

HEADERS = {"X-Film-Room": "1"}
SCHEMA = ROOT / "schema.sql"


def finding(area, confidence="observed", frames=(1,), finding_text="Synthetic finding."):
    return {"area": area, "finding": finding_text, "fix": "Synthetic fix.", "frames": list(frames),
            "confidence": confidence}


def report(discipline, findings=(), judged=None, drills=()):
    data = {"discipline": discipline, "technical_findings": list(findings), "drills": list(drills),
            "evidence_status": "sufficient"}
    if judged is not None:
        data["judged_areas"] = list(judged)
    return data


def drill(area, name="Synthetic drill"):
    return {"name": name, "addresses": area, "instructions": "Synthetic steps.",
            "objective": "Synthetic objective.", "reps_or_duration": "3 x 30 seconds"}


@pytest.fixture
def conn(tmp_path):
    from migrations import migrate

    connection = sqlite3.connect(tmp_path / "analytics.db")
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA.read_text())
    migrate(connection)
    yield connection
    connection.close()


def save(conn, discipline, day, data):
    """Insert one saved report the way the server does, with a fixed date."""
    import analytics

    judged = data.get("judged_areas")
    cur = conn.execute(
        "INSERT INTO film_reviews (created_at, mode, context, frame_count, feedback, report_json, "
        "report_version, discipline, judged_areas_json) VALUES (?, 'upload', NULL, 4, 'x', ?, 6, ?, ?)",
        (f"{day}T10:00:00", json.dumps(data), discipline,
         json.dumps(judged) if judged is not None else None),
    )
    analytics.store_rows(conn, cur.lastrowid, data, discipline)
    conn.commit()
    return cur.lastrowid


def issue(rows, discipline, category):
    return next(r for r in rows if r["discipline"] == discipline and r["category"] == category)


# ---- Category normalization -------------------------------------------------

def test_every_discipline_area_maps_to_a_category():
    import disciplines

    for sport, spec in disciplines.DISCIPLINES.items():
        for area in spec["areas"]:
            assert disciplines.category_for(sport, area) in disciplines.CATEGORIES, (sport, area)


def test_legacy_areas_and_unknown_areas_have_safe_categories():
    import disciplines

    assert disciplines.category_for("bjj", "offense") == "Striking"
    assert disciplines.category_for("bjj", "guard_retention") == "Guard retention"
    assert disciplines.category_for("bjj", "something_new") == "Uncategorised"
    assert disciplines.category_for(None, None) == "Uncategorised"


# ---- Storing rows from a saved report ----------------------------------------

def test_store_copies_findings_and_drills_into_their_own_tables(conn):
    review_id = save(conn, "bjj", "2026-10-01", report(
        "bjj",
        findings=[finding("guard_retention", "inferred")],
        judged=["guard_retention", "escapes"],
        drills=[drill("guard_retention", "Hip escape drill")],
    ))
    findings = conn.execute("SELECT area, category, confidence FROM review_findings WHERE review_id = ?",
                            (review_id,)).fetchall()
    drills = conn.execute("SELECT name, category, drill_key FROM review_drills WHERE review_id = ?",
                          (review_id,)).fetchall()
    assert [tuple(r) for r in findings] == [("guard_retention", "Guard retention", "inferred")]
    assert [tuple(r) for r in drills] == [
        ("Hip escape drill", "Guard retention", "bjj|Guard retention|hip escape drill"),
    ]


def test_store_replaces_rows_rather_than_adding_to_them(conn):
    import analytics

    review_id = save(conn, "bjj", "2026-10-01", report("bjj", [finding("guard_retention")], ["guard_retention"]))
    analytics.store_rows(conn, review_id, report("bjj", [finding("guard_retention")], ["guard_retention"]), "bjj")
    count = conn.execute("SELECT COUNT(*) FROM review_findings WHERE review_id = ?", (review_id,)).fetchone()[0]
    assert count == 1


def test_malformed_items_are_skipped_not_guessed(conn):
    data = report("bjj", findings=[finding("guard_retention"), "not a dict", {"finding": "no area"}],
                  judged=["guard_retention"], drills=[None, {"name": "no target"}])
    review_id = save(conn, "bjj", "2026-10-01", data)
    count = conn.execute("SELECT COUNT(*) FROM review_findings WHERE review_id = ?", (review_id,)).fetchone()[0]
    drills = conn.execute("SELECT COUNT(*) FROM review_drills WHERE review_id = ?", (review_id,)).fetchone()[0]
    assert (count, drills) == (1, 0)


# ---- Recurring issues ---------------------------------------------------------

def test_several_findings_in_one_session_count_once(conn):
    save(conn, "bjj", "2026-10-01", report(
        "bjj", [finding("guard_retention"), finding("guard_retention"), finding("guard_retention")],
        ["guard_retention"]))
    row = issue(_issues(conn), "bjj", "Guard retention")
    assert row["findings"] == 3
    assert row["sessions_detected"] == 1
    assert row["status"] == "single"


def test_uncertain_findings_are_shown_but_never_counted(conn):
    for day in ("2026-10-01", "2026-10-02", "2026-10-03"):
        save(conn, "bjj", day, report("bjj", [finding("guard_retention", "uncertain")], ["guard_retention"]))
    row = issue(_issues(conn), "bjj", "Guard retention")
    assert row["sessions_detected"] == 0
    assert row["status"] == "not_detected"


def test_two_sessions_make_a_repeated_issue_but_not_yet_persistent(conn):
    for day in ("2026-10-01", "2026-10-02"):
        save(conn, "bjj", day, report("bjj", [finding("guard_retention")], ["guard_retention"]))
    row = issue(_issues(conn), "bjj", "Guard retention")
    assert (row["sessions_detected"], row["status"]) == (2, "multi_session")


def test_three_sessions_on_one_day_are_repeated_not_persistent(conn):
    for _ in range(3):
        save(conn, "bjj", "2026-10-01", report("bjj", [finding("guard_retention")], ["guard_retention"]))
    row = issue(_issues(conn), "bjj", "Guard retention")
    assert (row["sessions_detected"], row["days_detected"], row["status"]) == (3, 1, "multi_session")


def test_three_sessions_across_two_days_are_persistent(conn):
    for day in ("2026-10-01", "2026-10-01", "2026-10-02"):
        save(conn, "bjj", day, report("bjj", [finding("guard_retention")], ["guard_retention"]))
    row = issue(_issues(conn), "bjj", "Guard retention")
    assert (row["sessions_detected"], row["days_detected"], row["status"]) == (3, 2, "multi_day")


def test_a_category_judged_without_a_finding_is_not_flagged_not_absent(conn):
    save(conn, "bjj", "2026-10-01", report("bjj", [], ["guard_retention"]))
    row = issue(_issues(conn), "bjj", "Guard retention")
    assert row["sessions_judged"] == 1
    assert row["sessions_detected"] == 0
    assert row["status"] == "not_detected"
    assert "not detected in them" in row["note"]


def test_insufficient_evidence_judges_nothing(conn):
    save(conn, "bjj", "2026-10-01", report("bjj", [], judged=[]))
    assert [r for r in _issues(conn) if r["discipline"] == "bjj"] == []


def test_legacy_session_without_judged_areas_is_never_counted_as_judged(conn):
    save(conn, "bjj", "2026-10-01", report("bjj", [finding("guard_retention")]))
    save(conn, "bjj", "2026-10-02", report("bjj", [finding("escapes")]))
    row = issue(_issues(conn), "bjj", "Guard retention")
    assert row["sessions_detected"] == 1
    # The legacy session detected the area, so it did assess it. The other legacy session did not
    # record an assessment for guard retention and must not count as having judged it.
    assert row["sessions_judged"] == 1
    assert issue(_issues(conn), "bjj", "Escapes")["sessions_judged"] == 1


def test_a_legacy_session_that_did_not_detect_an_area_never_counts_as_judged(conn):
    save(conn, "bjj", "2026-10-01", report("bjj", []))
    save(conn, "bjj", "2026-10-02", report("bjj", [finding("guard_retention")]))
    row = issue(_issues(conn), "bjj", "Guard retention")
    assert row["sessions_detected"] == 1
    assert row["sessions_judged"] == 1


def test_legacy_report_without_judged_field_still_counts_its_findings(conn):
    import analytics

    cur = conn.execute(
        "INSERT INTO film_reviews (created_at, mode, frame_count, feedback, report_json, discipline) "
        "VALUES ('2026-10-01T10:00:00', 'upload', 4, 'x', ?, 'bjj')",
        (json.dumps({"discipline": "bjj", "technical_findings": [finding("offense")]}),),
    )
    conn.commit()
    analytics.backfill(conn)
    row = issue(_issues(conn), "bjj", "Striking")
    assert row["sessions_detected"] == 1


def test_discipline_and_date_filters_narrow_the_results(conn):
    save(conn, "bjj", "2026-10-01", report("bjj", [finding("guard_retention")], ["guard_retention"]))
    save(conn, "mma", "2026-10-05", report("mma", [finding("takedown_defense")], ["takedown_defense"]))
    import analytics

    only_bjj = analytics.recurring_issues(conn, discipline="bjj")
    assert {r["discipline"] for r in only_bjj} == {"bjj"}
    in_october_second_half = analytics.recurring_issues(conn, start="2026-10-04", end="2026-10-31")
    assert {r["discipline"] for r in in_october_second_half} == {"mma"}
    assert analytics.recurring_issues(conn, start="2026-11-01") == []


def test_review_ids_list_only_the_sessions_that_flagged_the_category(conn):
    flagged = save(conn, "bjj", "2026-10-01", report("bjj", [finding("guard_retention")], ["guard_retention"]))
    save(conn, "bjj", "2026-10-02", report("bjj", [], ["guard_retention"]))
    row = issue(_issues(conn), "bjj", "Guard retention")
    assert row["review_ids"] == [flagged]


def test_backfill_is_idempotent(conn):
    import analytics

    save(conn, "bjj", "2026-10-01", report("bjj", [finding("guard_retention")], ["guard_retention"]))
    before = _issues(conn)
    analytics.backfill(conn)
    analytics.backfill(conn)
    assert _issues(conn) == before


def _issues(conn):
    import analytics

    return analytics.recurring_issues(conn)


# ---- Migration 5 upgrade of an existing database -----------------------------

def legacy_database(path):
    """A database as it was at user_version 4: the schema, migrations 1 to 4, and older reports."""
    import migrations

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA.read_text())
    for version in (1, 2, 3, 4):
        for statement in migrations.MIGRATIONS[version]:
            conn.execute(statement)
    conn.execute("PRAGMA user_version = 4")
    return conn


def test_upgrade_preserves_old_reports_and_backfills_their_findings(tmp_path):
    from migrations import migrate

    conn = legacy_database(tmp_path / "old.db")
    conn.execute(
        "INSERT INTO film_reviews (created_at, mode, context, frame_count, feedback, report_json, "
        "report_version, discipline) VALUES ('2026-09-01T10:00:00', 'upload', 'closed guard', 4, "
        "'Old feedback text', ?, 5, 'bjj')",
        (json.dumps(report("bjj", [finding("guard_retention"), finding("guard_retention", "uncertain")],
                           drills=[drill("guard_retention")])),),
    )
    conn.execute(
        "INSERT INTO film_reviews (created_at, mode, frame_count, feedback, discipline) "
        "VALUES ('2026-08-01T10:00:00', 'upload', 4, 'Feedback only, no stored report', 'bjj')"
    )
    conn.execute(
        "INSERT INTO film_reviews (created_at, mode, frame_count, feedback, report_json, discipline) "
        "VALUES ('2026-07-01T10:00:00', 'upload', 4, 'Corrupt', 'not json', 'bjj')"
    )
    conn.commit()

    assert migrate(conn) == 5
    kept = conn.execute("SELECT feedback FROM film_reviews ORDER BY review_id").fetchall()
    assert [r[0] for r in kept] == ["Old feedback text", "Feedback only, no stored report", "Corrupt"]
    findings = conn.execute("SELECT COUNT(*) FROM review_findings").fetchone()[0]
    drills = conn.execute("SELECT COUNT(*) FROM review_drills").fetchone()[0]
    assert (findings, drills) == (2, 1)
    judged = conn.execute("SELECT judged_areas_json FROM film_reviews WHERE review_id = 1").fetchone()[0]
    assert judged is None


def test_upgrade_runs_once_and_keeps_counts_stable(tmp_path):
    from migrations import migrate

    conn = legacy_database(tmp_path / "old.db")
    conn.execute(
        "INSERT INTO film_reviews (created_at, mode, frame_count, feedback, report_json, discipline) "
        "VALUES ('2026-09-01T10:00:00', 'upload', 4, 'x', ?, 'bjj')",
        (json.dumps(report("bjj", [finding("guard_retention")], ["guard_retention"])),),
    )
    conn.commit()
    migrate(conn)
    migrate(conn)
    assert conn.execute("SELECT COUNT(*) FROM review_findings").fetchone()[0] == 1


# ---- Through the server: save, cache, delete ----------------------------------

def make_review(client, frame, context="closed guard"):
    body = {"mode": "upload", "discipline": "bjj", "frames": [frame], "frameTimes": [0.0], "context": context}
    return client.post("/api/review", json=body).get_json()


def row_count(server, table):
    with sqlite3.connect(server.DB_PATH) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_saving_a_review_writes_its_normalized_rows(server, frame, fake_claude):
    fake_claude([FakeResponse(valid_report_text("bjj"))])
    review = make_review(server.app.test_client(), frame)
    assert review["cached"] is False
    assert row_count(server, "review_findings") == 1
    assert row_count(server, "review_drills") == 1
    with sqlite3.connect(server.DB_PATH) as conn:
        judged = conn.execute("SELECT judged_areas_json FROM film_reviews").fetchone()[0]
    assert json.loads(judged) == json.loads(valid_report_text("bjj"))["judged_areas"]


def test_a_cached_repeat_adds_no_rows_and_no_session(server, frame, fake_claude):
    fake_claude([FakeResponse(valid_report_text("bjj"))])
    client = server.app.test_client()
    make_review(client, frame)
    again = make_review(client, frame)
    assert again["cached"] is True
    assert row_count(server, "film_reviews") == 1
    assert row_count(server, "review_findings") == 1
    assert row_count(server, "review_drills") == 1


def test_deleting_one_review_removes_only_its_rows(server, frame, fake_claude):
    fake_claude([FakeResponse(valid_report_text("bjj"))] * 2)
    client = server.app.test_client()
    first = make_review(client, frame, context="one")
    second = make_review(client, frame, context="two")
    client.delete(f"/api/reviews/{first['id']}", headers=HEADERS)
    with sqlite3.connect(server.DB_PATH) as conn:
        remaining = [r[0] for r in conn.execute("SELECT DISTINCT review_id FROM review_findings")]
    assert remaining == [second["id"]]


def test_delete_all_clears_normalized_rows_too(server, frame, fake_claude):
    fake_claude([FakeResponse(valid_report_text("bjj"))] * 2)
    client = server.app.test_client()
    make_review(client, frame, context="a")
    make_review(client, frame, context="b")
    client.delete("/api/reviews", json={"confirm": "DELETE ALL REPORTS"}, headers=HEADERS)
    assert row_count(server, "review_findings") == 0
    assert row_count(server, "review_drills") == 0


def test_deleted_reports_disappear_from_recurring_issues(server, frame, fake_claude):
    import analytics

    fake_claude([FakeResponse(valid_report_text("bjj"))] * 2)
    client = server.app.test_client()
    first = make_review(client, frame, context="a")
    make_review(client, frame, context="b")
    with sqlite3.connect(server.DB_PATH) as conn:
        before = analytics.recurring_issues(conn, discipline="bjj")
    client.delete(f"/api/reviews/{first['id']}", headers=HEADERS)
    with sqlite3.connect(server.DB_PATH) as conn:
        after = analytics.recurring_issues(conn, discipline="bjj")
    assert before[0]["sessions_detected"] == 2
    assert after[0]["sessions_detected"] == 1
