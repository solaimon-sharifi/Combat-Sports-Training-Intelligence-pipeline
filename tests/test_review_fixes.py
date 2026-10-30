"""Regression tests for the Phase 4 review. Every report is a synthetic fixture (labelled "Synthetic" in the text),
not real footage or real training history."""
import json
import sqlite3

import pytest

from test_analytics import conn, drill, finding, legacy_database, report, save  # noqa: F401 (conn is a fixture)

HEADERS = {"X-Film-Room": "1"}
SCHEMA_TABLES = ("review_findings", "review_drills", "drill_practice")


def issue(rows, discipline, category):
    return next(r for r in rows if r["discipline"] == discipline and r["category"] == category)


def add_session(conn, discipline, day, data, frame_times=None, athlete=None, judged_json="auto"):
    """Save one report with optional frame times and athlete, for comparison tests."""
    import analytics

    judged = data.get("judged_areas")
    cur = conn.execute(
        "INSERT INTO film_reviews (created_at, mode, context, frame_count, feedback, report_json, report_version, "
        "discipline, judged_areas_json, frame_times_json, athlete_description) "
        "VALUES (?, 'upload', 'Synthetic context', ?, 'Synthetic', ?, 6, ?, ?, ?, ?)",
        (f"{day}T10:00:00", len(frame_times or [1]), json.dumps(data), discipline,
         json.dumps(judged) if judged is not None else None,
         json.dumps(frame_times) if frame_times is not None else None, athlete),
    )
    analytics.store_rows(conn, cur.lastrowid, data, discipline)
    conn.commit()
    return cur.lastrowid


# ---- Judged counts stay consistent with detections ---------------------------------

def test_a_legacy_detection_counts_as_judged_so_the_fraction_never_exceeds_one(conn):
    import analytics

    save(conn, "bjj", "2026-10-01", report("bjj", [finding("guard_retention")]))  # no judged list (legacy)
    save(conn, "bjj", "2026-10-02", report("bjj", [], ["guard_retention"]))
    row = issue(analytics.recurring_issues(conn), "bjj", "Guard retention")
    assert row["sessions_detected"] == 1
    assert row["sessions_judged"] == 2
    assert row["sessions_detected"] <= row["sessions_judged"]


# ---- Known expected results ---------------------------------------------------------

def test_dashboard_matches_hand_computed_values(conn):
    """Synthetic: 3 BJJ reports on 2 days. Guard retention is detected in reports 1 and 2 (both on 2026-10-01)
    and judged in all three. Expected: detected 2, judged 3, detected on 1 day -> multi_session (the
    multi-day rule needs 3 detections on 2 days, so it does not apply)."""
    import analytics

    save(conn, "bjj", "2026-10-01", report("bjj", [finding("guard_retention")], ["guard_retention"]))
    save(conn, "bjj", "2026-10-01", report("bjj", [finding("guard_retention")], ["guard_retention"]))
    save(conn, "bjj", "2026-10-02", report("bjj", [], ["guard_retention"]))
    data = analytics.dashboard(conn, "owner")
    row = issue(data["recurring"], "bjj", "Guard retention")
    assert (row["sessions_detected"], row["sessions_judged"], row["days_detected"]) == (2, 3, 1)
    assert row["status"] == "multi_session"
    assert row["status_label"] == "Detected across multiple sessions"
    assert [r["category"] for r in data["areas_needing_review"]] == ["Guard retention"]


def test_multi_day_needs_three_detections_across_two_days(conn):
    import analytics

    for day in ("2026-10-01", "2026-10-02", "2026-10-02"):
        save(conn, "bjj", day, report("bjj", [finding("guard_retention")], ["guard_retention"]))
    row = issue(analytics.recurring_issues(conn), "bjj", "Guard retention")
    assert (row["sessions_detected"], row["days_detected"]) == (3, 2)
    assert row["status"] == "multi_day"


def test_observed_and_inferred_are_split_so_the_interface_can_show_both(conn):
    import analytics

    save(conn, "bjj", "2026-10-01", report("bjj", [finding("guard_retention", "observed")], ["guard_retention"]))
    save(conn, "bjj", "2026-10-02", report("bjj", [finding("guard_retention", "inferred")], ["guard_retention"]))
    row = issue(analytics.recurring_issues(conn), "bjj", "Guard retention")
    assert row["sessions_detected"] == 2
    assert row["observed_sessions"] == 1


def test_uncertain_only_sessions_do_not_create_a_status(conn):
    import analytics

    for day in ("2026-10-01", "2026-10-02", "2026-10-03"):
        save(conn, "bjj", day, report("bjj", [finding("guard_retention", "uncertain")], ["guard_retention"]))
    row = issue(analytics.recurring_issues(conn), "bjj", "Guard retention")
    assert row["sessions_detected"] == 0
    assert row["status"] == "not_detected"
    assert "not evidence the area is fixed" in row["note"]


def test_rules_are_returned_for_the_interface(conn):
    import analytics

    rules = analytics.dashboard(conn, "owner")["rules"]
    assert any("Uncertain findings are never counted" in r for r in rules)
    assert any("not evidence the area is fixed" in r for r in rules)


def test_no_label_claims_a_persistent_weakness(conn):
    import analytics

    for day in ("2026-10-01", "2026-10-02", "2026-10-03"):
        save(conn, "bjj", day, report("bjj", [finding("guard_retention")], ["guard_retention"]))
    labels = " ".join(r["status_label"] for r in analytics.recurring_issues(conn))
    assert "persistent" not in labels.lower()
    assert "weakness" not in labels.lower()


# ---- History respects the active filters ----------------------------------------------

def test_history_recurring_filter_uses_the_active_date_range(conn):
    import analytics

    for day in ("2026-10-01", "2026-10-02", "2026-10-03"):
        save(conn, "bjj", day, report("bjj", [finding("guard_retention")], ["guard_retention"]))
    save(conn, "bjj", "2026-11-01", report("bjj", [], ["guard_retention"]))
    everything = analytics.history(conn, "owner", {"recurring": True})
    assert everything["total"] == 3
    november_only = analytics.history(conn, "owner", {"recurring": True, "start": "2026-11-01"})
    assert november_only["total"] == 0


def test_history_recurring_filter_matches_the_dashboard_for_the_same_sport(conn):
    import analytics

    for day in ("2026-10-01", "2026-10-02", "2026-10-03"):
        save(conn, "bjj", day, report("bjj", [finding("guard_retention")], ["guard_retention"]))
        save(conn, "mma", day, report("mma", [finding("takedown_defense")], ["takedown_defense"]))
    bjj_recurring = analytics.history(conn, "owner", {"recurring": True, "discipline": "bjj"})
    assert {item["discipline"] for item in bjj_recurring["items"]} == {"bjj"}
    assert bjj_recurring["total"] == 3


# ---- Drills link to their own reports' findings ---------------------------------------

def test_a_drill_lists_only_findings_from_the_reports_that_recommend_it(conn):
    import analytics

    save(conn, "bjj", "2026-10-01", report(
        "bjj", [finding("guard_retention", finding_text="Synthetic: hips drop in report one.")],
        ["guard_retention"], drills=[drill("guard_retention", "Hip escape")]))
    save(conn, "bjj", "2026-10-02", report(
        "bjj", [finding("guard_retention", finding_text="Synthetic: knees open in report two.")],
        ["guard_retention"], drills=[]))
    entry = analytics.drill_library(conn)["drills"][0]
    assert entry["linked_findings"] == ["Synthetic: hips drop in report one."]


def test_drill_names_match_across_case_and_spacing(conn):
    import analytics

    save(conn, "bjj", "2026-10-01", report("bjj", [], ["guard_retention"], drills=[drill("guard_retention", "Hip  Escape")]))
    save(conn, "bjj", "2026-10-02", report("bjj", [], ["guard_retention"], drills=[drill("guard_retention", "hip escape")]))
    library = analytics.drill_library(conn)["drills"]
    assert len(library) == 1
    assert library[0]["sessions"] == 2


def test_every_drill_has_a_supporting_detection_in_its_own_report(conn):
    import analytics

    save(conn, "bjj", "2026-10-01", report("bjj", [finding("guard_retention")], ["guard_retention"],
                                           drills=[drill("guard_retention")]))
    assert analytics.integrity_problems(conn) == []


def test_integrity_check_reports_a_drill_without_a_detection(conn):
    import analytics

    review = save(conn, "bjj", "2026-10-01", report("bjj", [], ["guard_retention"], drills=[drill("guard_retention")]))
    problems = analytics.integrity_problems(conn)
    assert any("no supporting observed or inferred finding" in p for p in problems)
    assert review in [r[0] for r in conn.execute("SELECT review_id FROM review_drills")]


def test_integrity_check_reports_rows_pointing_to_missing_reports(conn):
    import analytics

    conn.execute("INSERT INTO review_findings (review_id, position, discipline, area, category, confidence, finding, "
                 "fix, frames_json) VALUES (999, 1, 'bjj', 'guard_defense', 'Guard retention', 'observed', 'x', 'y', '[]')")
    conn.commit()
    assert any("does not exist" in p for p in analytics.integrity_problems(conn))


def test_drills_are_categorised_under_their_own_sport(conn):
    import analytics
    import disciplines

    for sport, spec in disciplines.DISCIPLINES.items():
        area = next(iter(spec["areas"]))
        save(conn, sport, "2026-10-01", report(sport, [finding(area)], [area], drills=[drill(area, f"Drill {sport}")]))
    for entry in analytics.drill_library(conn)["drills"]:
        assert entry["category"] in disciplines.CATEGORIES
        assert entry["drill_key"].startswith(f"{entry['discipline']}|")


# ---- Practice logging and history ------------------------------------------------------

def test_practice_entries_keep_the_drill_name_after_its_report_is_deleted(conn):
    import analytics

    review = save(conn, "bjj", "2026-10-01", report("bjj", [finding("guard_retention")], ["guard_retention"],
                                                    drills=[drill("guard_retention", "Hip escape")]))
    key = analytics.drill_library(conn)["drills"][0]["drill_key"]
    analytics.add_practice(conn, key, "2026-10-02", "Synthetic note")
    conn.commit()
    analytics.delete_rows(conn, review)
    conn.execute("DELETE FROM film_reviews WHERE review_id = ?", (review,))
    conn.commit()
    assert analytics.drill_library(conn)["drills"] == []
    history = analytics.practice_history(conn)
    assert history["total"] == 1
    assert history["entries"][0]["drill_name"] == "Hip escape"
    assert history["entries"][0]["in_library"] is False


def test_practice_history_is_newest_first_and_paged(conn):
    import analytics

    save(conn, "bjj", "2026-10-01", report("bjj", [], ["guard_retention"], drills=[drill("guard_retention", "Hip escape")]))
    key = analytics.drill_library(conn)["drills"][0]["drill_key"]
    for day in ("2026-10-02", "2026-10-03", "2026-10-04"):
        analytics.add_practice(conn, key, day, "")
    conn.commit()
    page = analytics.practice_history(conn, limit=2, offset=0)
    assert page["total"] == 3
    assert [e["practiced_on"] for e in page["entries"]] == ["2026-10-04", "2026-10-03"]


def test_practice_for_a_drill_never_recommended_is_refused(conn):
    import analytics

    with pytest.raises(LookupError):
        analytics.add_practice(conn, "bjj|Striking|nothing", "2026-10-02", "")


def test_removing_a_practice_day_does_not_touch_reports(conn):
    import analytics

    save(conn, "bjj", "2026-10-01", report("bjj", [finding("guard_retention")], ["guard_retention"],
                                           drills=[drill("guard_retention", "Hip escape")]))
    key = analytics.drill_library(conn)["drills"][0]["drill_key"]
    practice_id = analytics.add_practice(conn, key, "2026-10-02", "")
    conn.commit()
    analytics.delete_practice(conn, practice_id)
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM film_reviews").fetchone()[0] == 1
    assert analytics.practice_history(conn)["total"] == 0


def test_practice_endpoints_require_login(server):
    anonymous = server.raw_client()
    assert anonymous.get("/api/drill-practice").status_code == 401
    assert anonymous.post("/api/drill-practice", json={"drill_key": "x"}, headers=HEADERS).status_code == 401


def test_practice_listing_is_served_to_the_owner(server, frame, fake_claude):
    from conftest import FakeResponse, valid_report_text
    from test_report_deletion import make_review

    fake_claude([FakeResponse(valid_report_text("bjj"))])
    client = server.app.test_client()
    make_review(client, frame)
    key = client.get("/api/analytics/drills").get_json()["drills"][0]["drill_key"]
    client.post("/api/drill-practice", json={"drill_key": key, "practiced_on": "2026-10-02"}, headers=HEADERS)
    body = client.get("/api/drill-practice").get_json()
    assert body["total"] == 1
    assert body["entries"][0]["drill_name"]


# ---- Comparison ------------------------------------------------------------------------

def test_comparison_warns_about_different_athletes(conn):
    import analytics

    earlier = add_session(conn, "bjj", "2026-10-01", report("bjj", [finding("guard_retention")], ["guard_retention"]),
                          frame_times=[0.0, 2.0], athlete="the person in white")
    newer = add_session(conn, "bjj", "2026-10-08", report("bjj", [], ["guard_retention"]),
                        frame_times=[0.0, 2.0], athlete="the person in blue")
    result = analytics.compare(conn, "owner", earlier, newer)
    assert any("different athletes" in c for c in result["categories"][0]["caveats"])
    assert result["categories"][0]["reliable"] is False


def test_comparison_warns_when_the_athlete_was_not_confirmed(conn):
    import analytics

    data = report("bjj", [finding("guard_retention")], ["guard_retention"])
    data["athlete_status"] = "uncertain"
    earlier = add_session(conn, "bjj", "2026-10-01", data, frame_times=[0.0, 2.0])
    newer = add_session(conn, "bjj", "2026-10-08", report("bjj", [], ["guard_retention"]), frame_times=[0.0, 2.0])
    result = analytics.compare(conn, "owner", earlier, newer)
    caveats = result["categories"][0]["caveats"]
    assert any("not confirmed" in c for c in caveats)


def test_comparison_flags_limited_evidence_and_is_not_reliable(conn):
    import analytics

    data = report("bjj", [finding("guard_retention")], ["guard_retention"])
    data["evidence_status"] = "limited"
    earlier = add_session(conn, "bjj", "2026-10-01", data, frame_times=[0.0, 2.0])
    newer = add_session(conn, "bjj", "2026-10-08", report("bjj", [finding("guard_retention")], ["guard_retention"]),
                        frame_times=[0.0, 2.0])
    result = analytics.compare(conn, "owner", earlier, newer)
    entry = result["categories"][0]
    assert entry["change"] == "in_both"
    assert any("Evidence was limited" in c for c in entry["caveats"])
    assert entry["reliable"] is False


def test_clean_comparison_is_reliable(conn):
    import analytics

    identified = dict(report("bjj", [finding("guard_retention")], ["guard_retention"]), athlete_status="identified",
                      evidence_status="sufficient")
    earlier = add_session(conn, "bjj", "2026-10-01", dict(identified), frame_times=[0.0, 2.0, 4.0])
    newer = add_session(conn, "bjj", "2026-10-08", dict(identified), frame_times=[0.0, 2.0, 4.0])
    result = analytics.compare(conn, "owner", earlier, newer)
    entry = result["categories"][0]
    assert entry["change"] == "in_both"
    assert entry["reliable"] is True
    assert result["warnings"] == []


def test_comparison_warns_when_sampled_spans_differ(conn):
    import analytics

    earlier = add_session(conn, "bjj", "2026-10-01", report("bjj", [], ["guard_retention"]), frame_times=[0.0, 4.0])
    newer = add_session(conn, "bjj", "2026-10-08", report("bjj", [], ["guard_retention"]), frame_times=[0.0, 40.0])
    result = analytics.compare(conn, "owner", earlier, newer)
    assert any("sampled spans differ" in w for w in result["warnings"])


def test_comparison_states_that_not_detected_is_not_fixed(conn):
    import analytics

    earlier = add_session(conn, "bjj", "2026-10-01", report("bjj", [finding("guard_retention")], ["guard_retention"]),
                          frame_times=[0.0, 2.0])
    newer = add_session(conn, "bjj", "2026-10-08", report("bjj", [], ["guard_retention"]), frame_times=[0.0, 2.0])
    result = analytics.compare(conn, "owner", earlier, newer)
    entry = result["categories"][0]
    assert entry["change"] == "earlier_only"
    assert "not evidence the area is fixed" in entry["change_note"]
    assert "fixed" not in entry["change_label"].lower()


def test_comparison_includes_sessions_and_frame_links_for_the_evidence(conn):
    import analytics

    earlier = add_session(conn, "bjj", "2026-10-01", report("bjj", [finding("guard_retention", frames=(2,))],
                                                         ["guard_retention"]), frame_times=[0.0, 2.5])
    newer = add_session(conn, "bjj", "2026-10-08", report("bjj", [], ["guard_retention"]), frame_times=[0.0, 2.5])
    result = analytics.compare(conn, "owner", earlier, newer)
    assert result["earlier"]["id"] == earlier
    assert result["earlier"]["frame_times"] == [0.0, 2.5]
    assert result["categories"][0]["earlier"]["findings"][0]["frames"] == [2]


def test_comparison_across_sports_and_missing_reports_are_refused(conn):
    import analytics

    bjj = save(conn, "bjj", "2026-10-01", report("bjj"))
    mma = save(conn, "mma", "2026-10-02", report("mma"))
    with pytest.raises(ValueError):
        analytics.compare(conn, "owner", bjj, mma)
    with pytest.raises(LookupError):
        analytics.compare(conn, "owner", bjj, 404)


def test_comparison_of_two_sessions_with_identical_timestamps_is_stable(conn):
    import analytics

    first = save(conn, "bjj", "2026-10-01", report("bjj", [finding("guard_retention")], ["guard_retention"]))
    second = save(conn, "bjj", "2026-10-01", report("bjj", [], ["guard_retention"]))
    result = analytics.compare(conn, "owner", second, first)
    assert result["earlier"]["id"] == first
    assert result["newer"]["id"] == second


# ---- Migration safety --------------------------------------------------------------------

def test_failed_migration_rolls_back_and_leaves_the_old_schema(tmp_path, monkeypatch):
    import analytics
    from migrations import migrate

    conn = legacy_database(tmp_path / "rollback.db")
    for day in ("2026-09-01", "2026-09-02"):
        conn.execute(
            "INSERT INTO film_reviews (created_at, mode, frame_count, feedback, report_json, discipline) "
            "VALUES (?, 'upload', 4, 'x', ?, 'bjj')",
            (f"{day}T10:00:00", json.dumps(report("bjj", [finding("guard_retention")], ["guard_retention"]))),
        )
    conn.commit()
    calls = {"n": 0}
    original = analytics.store_rows

    def failing_store(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated failure on the second report")
        return original(*args, **kwargs)

    monkeypatch.setattr(analytics, "store_rows", failing_store)
    with pytest.raises(RuntimeError):
        migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
    columns = {row[1] for row in conn.execute("PRAGMA table_info(film_reviews)")}
    assert "judged_areas_json" not in columns
    for table in SCHEMA_TABLES:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        assert exists is None, table


def test_migration_on_a_realistic_legacy_copy_keeps_every_report_and_backfills_consistently(tmp_path):
    from migrations import migrate

    conn = legacy_database(tmp_path / "realistic.db")
    samples = [
        ("2026-09-01", report("bjj", [finding("guard_retention")], ["guard_retention", "escapes"],
                              drills=[drill("guard_retention", "Hip escape")])),
        ("2026-09-02", report("bjj", [], ["guard_retention", "escapes"])),
        ("2026-09-03", report("mma", [finding("takedown_defense")], ["takedown_defense"])),
    ]
    for day, data in samples:
        conn.execute(
            "INSERT INTO film_reviews (created_at, mode, context, frame_count, feedback, report_json, discipline) "
            "VALUES (?, 'upload', 'Synthetic context', 4, 'Synthetic feedback', ?, ?)",
            (f"{day}T10:00:00", json.dumps(data), data["discipline"]),
        )
    conn.execute(
        "INSERT INTO film_reviews (created_at, mode, frame_count, feedback, discipline) "
        "VALUES ('2026-08-01T10:00:00', 'upload', 4, 'Synthetic feedback only', 'bjj')"
    )
    conn.commit()
    assert migrate(conn) == 5
    import analytics

    assert conn.execute("SELECT COUNT(*) FROM film_reviews").fetchone()[0] == 4
    feedback = [r[0] for r in conn.execute("SELECT feedback FROM film_reviews ORDER BY review_id")]
    assert feedback[-1] == "Synthetic feedback only"
    assert analytics.integrity_problems(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM review_drills").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM review_findings").fetchone()[0] == 2


def test_migration_is_idempotent_on_a_realistic_copy(tmp_path):
    from migrations import migrate

    conn = legacy_database(tmp_path / "again.db")
    conn.execute(
        "INSERT INTO film_reviews (created_at, mode, frame_count, feedback, report_json, discipline) "
        "VALUES ('2026-09-01T10:00:00', 'upload', 4, 'x', ?, 'bjj')",
        (json.dumps(report("bjj", [finding("guard_retention")], ["guard_retention"])),),
    )
    conn.commit()
    migrate(conn)
    before = conn.execute("SELECT COUNT(*) FROM review_findings").fetchone()[0]
    migrate(conn)
    assert conn.execute("SELECT COUNT(*) FROM review_findings").fetchone()[0] == before
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 5


# ---- Larger dataset (synthetic, in a temporary database) ---------------------------------

def test_larger_history_gives_the_expected_counts(conn):
    """Synthetic: 300 BJJ reports, dated by index modulo 30 (30 days). Guard retention is detected in every third
    report (100 reports) and every report judges it. Multiples of 3 fall on 10 different days, so the expected
    values are 100 detected, 300 judged, 10 days, multi_day."""
    import analytics

    for index in range(300):
        day = f"2026-10-{(index % 30) + 1:02d}"
        detected = [finding("guard_retention")] if index % 3 == 0 else []
        save(conn, "bjj", day, report("bjj", detected, ["guard_retention"]))
    row = issue(analytics.recurring_issues(conn), "bjj", "Guard retention")
    assert (row["sessions_detected"], row["sessions_judged"], row["days_detected"]) == (100, 300, 10)
    assert row["status"] == "multi_day"
    assert analytics.dashboard(conn, "owner")["sessions"] == 300
    assert analytics.history(conn, "owner", {}, limit=25, offset=0)["total"] == 300
    assert analytics.history(conn, "owner", {}, limit=25, offset=275)["items"][-1]["id"] == 1
