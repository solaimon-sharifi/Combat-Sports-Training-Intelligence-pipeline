"""Training analytics, computed only from saved reports.

Sessions and counting
- A session is one saved report. A cache hit reuses an existing row and never creates one.
- Findings and drills are copied into their own tables when a report is saved. Migration 5
  backfills them once for reports saved before those tables existed.

Recurrence rules (shown in the interface, see RULES)
- Only observed and inferred findings count. Uncertain findings are never counted.
- Several findings in one category within one session count as one session.
- "Detected across multiple sessions": detected in at least 2 sessions.
- "Detected across multiple sessions and days": detected in at least 3 sessions on at least 2 calendar days.
- "Not detected": nothing detected in a session that judged the area. This is not evidence the area is fixed.
- A session counts as having judged an area only when the report recorded that area as judged.
  Reports saved before judged areas were recorded are never counted as having judged anything.
"""

import json
import re
from collections import defaultdict

import disciplines

COUNTED_CONFIDENCE = ("observed", "inferred")
MULTI_SESSION = 2
MULTI_DAY_SESSIONS = 3
MULTI_DAY_DAYS = 2
_SPACES = re.compile(r"\s+")

RULES = [
    "Only observed and inferred findings count. Uncertain findings are never counted.",
    "Several findings in one category within one session count as one session.",
    "Detected across multiple sessions: detected in at least 2 sessions.",
    "Detected across multiple sessions and days: detected in at least 3 sessions on at least 2 calendar days.",
    "Not detected: nothing was detected in a session that judged the area. This is not evidence the area is fixed.",
]

STATUS_LABELS = {
    "multi_day": "Detected across multiple sessions and days",
    "multi_session": "Detected across multiple sessions",
    "single": "Detected in one session",
    "not_detected": "Not detected in judged sessions",
}
_STATUS_ORDER = {"multi_day": 0, "multi_session": 1, "single": 2, "not_detected": 3}
_NOTES = {
    "multi_day": "Detected in {sessions} sessions on {days} days. This describes the saved reports, not a diagnosis.",
    "multi_session": "Detected in {sessions} sessions. Worth checking against the footage. It is not proof of a fault.",
    "single": "Detected in one session only. Not enough to call it a pattern.",
    "not_detected": "Judged in {judged} sessions and not detected in them. This is not evidence the area is fixed.",
}


def normalize_name(text):
    return _SPACES.sub(" ", str(text or "")).strip().lower()


def drill_key(discipline, category, name):
    """A drill's identity across reports: the same named drill for the same category and sport."""
    return f"{discipline}|{category}|{normalize_name(name)}"


def _items(report, key):
    value = report.get(key)
    return value if isinstance(value, list) else []


def extract(report, discipline=None):
    """Flatten one report into rows. Malformed items are skipped, never guessed at."""
    discipline = report.get("discipline") or discipline or "unspecified"
    findings, drills = [], []
    for item in _items(report, "technical_findings"):
        if not isinstance(item, dict) or not item.get("area"):
            continue
        area = str(item["area"])
        findings.append({
            "discipline": discipline,
            "area": area,
            "category": disciplines.category_for(discipline, area),
            "confidence": str(item.get("confidence") or "uncertain"),
            "finding": str(item.get("finding") or ""),
            "fix": str(item.get("fix") or ""),
            "frames": item.get("frames") if isinstance(item.get("frames"), list) else [],
        })
    for item in _items(report, "drills"):
        if not isinstance(item, dict) or not item.get("addresses"):
            continue
        area = str(item["addresses"])
        category = disciplines.category_for(discipline, area)
        name = str(item.get("name") or "").strip() or "Unnamed drill"
        drills.append({
            "discipline": discipline,
            "area": area,
            "category": category,
            "drill_key": drill_key(discipline, category, name),
            "name": name,
            "instructions": str(item.get("instructions") or ""),
            "objective": str(item.get("objective") or ""),
            "reps_or_duration": str(item.get("reps_or_duration") or ""),
        })
    judged = report.get("judged_areas")
    return {
        "judged_areas": None if not isinstance(judged, list) else sorted({str(a) for a in judged}),
        "findings": findings,
        "drills": drills,
    }


def store_rows(conn, review_id, report, discipline=None):
    """Replace the normalized rows for one report. Runs inside the caller's transaction."""
    rows = extract(report, discipline)
    delete_rows(conn, review_id)
    for position, item in enumerate(rows["findings"], start=1):
        conn.execute(
            "INSERT INTO review_findings (review_id, position, discipline, area, category, confidence, "
            "finding, fix, frames_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (review_id, position, item["discipline"], item["area"], item["category"], item["confidence"],
             item["finding"], item["fix"], json.dumps(item["frames"])),
        )
    for position, item in enumerate(rows["drills"], start=1):
        conn.execute(
            "INSERT INTO review_drills (review_id, position, discipline, area, category, drill_key, name, "
            "instructions, objective, reps_or_duration) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (review_id, position, item["discipline"], item["area"], item["category"], item["drill_key"],
             item["name"], item["instructions"], item["objective"], item["reps_or_duration"]),
        )
    return rows


def delete_rows(conn, review_id):
    conn.execute("DELETE FROM review_findings WHERE review_id = ?", (review_id,))
    conn.execute("DELETE FROM review_drills WHERE review_id = ?", (review_id,))


def backfill(conn):
    """Migration 5. Copies rows from every report saved before the normalized tables existed.
    Rows without a stored report stay as they are and count as unknown coverage."""
    reports = conn.execute(
        "SELECT review_id, discipline, report_json FROM film_reviews WHERE report_json IS NOT NULL"
    ).fetchall()
    for review_id, discipline, report_json in reports:
        try:
            report = json.loads(report_json)
        except (TypeError, ValueError):
            continue
        if not isinstance(report, dict):
            continue
        judged = extract(report, discipline)["judged_areas"]
        conn.execute(
            "UPDATE film_reviews SET judged_areas_json = ? WHERE review_id = ?",
            (None if judged is None else json.dumps(judged), review_id),
        )
        store_rows(conn, review_id, report, discipline)


def integrity_problems(conn):
    """Consistency checks over the normalized tables. An empty list means every check passed."""
    problems = []
    orphan = conn.execute(
        "SELECT COUNT(*) FROM review_findings f WHERE NOT EXISTS "
        "(SELECT 1 FROM film_reviews r WHERE r.review_id = f.review_id)"
    ).fetchone()[0]
    if orphan:
        problems.append(f"{orphan} finding row(s) point to a report that does not exist.")
    orphan = conn.execute(
        "SELECT COUNT(*) FROM review_drills d WHERE NOT EXISTS "
        "(SELECT 1 FROM film_reviews r WHERE r.review_id = d.review_id)"
    ).fetchone()[0]
    if orphan:
        problems.append(f"{orphan} drill row(s) point to a report that does not exist.")
    unsupported = conn.execute(
        "SELECT COUNT(*) FROM review_drills d WHERE NOT EXISTS (SELECT 1 FROM review_findings f "
        "WHERE f.review_id = d.review_id AND f.category = d.category "
        "AND f.confidence IN ('observed', 'inferred'))"
    ).fetchone()[0]
    if unsupported:
        problems.append(f"{unsupported} drill(s) have no supporting observed or inferred finding in their report.")
    wrong_sport = conn.execute(
        "SELECT COUNT(*) FROM review_findings f JOIN film_reviews r ON r.review_id = f.review_id "
        "WHERE r.discipline IS NOT NULL AND f.discipline != r.discipline"
    ).fetchone()[0]
    if wrong_sport:
        problems.append(f"{wrong_sport} finding(s) are filed under a different sport from their report.")
    return problems


def _scope(discipline=None, start=None, end=None):
    """Filters on the film_reviews alias r. Dates are YYYY-MM-DD and compare as text."""
    clauses, params = [], []
    if discipline:
        clauses.append("r.discipline = ?")
        params.append(discipline)
    if start:
        clauses.append("substr(r.created_at, 1, 10) >= ?")
        params.append(start)
    if end:
        clauses.append("substr(r.created_at, 1, 10) <= ?")
        params.append(end)
    return ("WHERE " + " AND ".join(clauses)) if clauses else "", params


def _judged_list(value):
    if value is None:
        return None
    try:
        areas = json.loads(value)
    except (TypeError, ValueError):
        return None
    return areas if isinstance(areas, list) else None


def _status(sessions, days):
    if sessions >= MULTI_DAY_SESSIONS and days >= MULTI_DAY_DAYS:
        return "multi_day"
    if sessions >= MULTI_SESSION:
        return "multi_session"
    if sessions == 1:
        return "single"
    return "not_detected"


def recurring_issues(conn, discipline=None, start=None, end=None):
    """Recurring technique categories for the filtered sessions, most frequent first."""
    where, params = _scope(discipline, start, end)
    sessions = conn.execute(
        f"SELECT r.review_id, r.discipline, r.created_at, r.judged_areas_json FROM film_reviews r {where}",
        params,
    ).fetchall()
    if not sessions:
        return []

    day_of = {}
    judged_in = defaultdict(set)
    for review_id, sport, created_at, judged_json in sessions:
        day_of[review_id] = created_at[:10]
        areas = _judged_list(judged_json)
        if areas is None:
            continue
        for category in {disciplines.category_for(sport, area) for area in areas}:
            judged_in[(sport, category)].add(review_id)

    detected_in = defaultdict(set)
    observed_in = defaultdict(set)
    finding_count = defaultdict(int)
    findings = conn.execute(
        f"SELECT f.review_id, f.discipline, f.category, f.confidence "
        f"FROM review_findings f JOIN film_reviews r ON r.review_id = f.review_id {where}",
        params,
    ).fetchall()
    for review_id, sport, category, confidence in findings:
        if confidence not in COUNTED_CONFIDENCE or review_id not in day_of:
            continue
        key = (sport, category)
        detected_in[key].add(review_id)
        if confidence == "observed":
            observed_in[key].add(review_id)
        finding_count[key] += 1
        # A detected category was judged by definition, even when the report did not list it.
        judged_in[key].add(review_id)

    results = []
    for key in set(detected_in) | set(judged_in):
        sport, category = key
        detected = detected_in.get(key, set())
        days = {day_of[review_id] for review_id in detected}
        status = _status(len(detected), len(days))
        judged = len(judged_in.get(key, set()))
        results.append({
            "discipline": sport,
            "discipline_label": disciplines.label_for(sport),
            "category": category,
            "status": status,
            "status_label": STATUS_LABELS[status],
            "sessions_detected": len(detected),
            "observed_sessions": len(observed_in.get(key, set())),
            "sessions_judged": judged,
            "findings": finding_count.get(key, 0),
            "days_detected": len(days),
            "review_ids": sorted(detected, reverse=True),
            "note": _NOTES[status].format(sessions=len(detected), days=len(days), judged=judged),
        })
    results.sort(key=lambda r: (_STATUS_ORDER[r["status"]], -r["sessions_detected"], r["discipline"], r["category"]))
    return results


def dashboard(conn, owner, discipline=None, start=None, end=None):
    """Everything the training dashboard shows, computed only from saved reports in scope."""
    where, params = _scope(discipline, start, end)
    summary = conn.execute(
        "SELECT COUNT(*), MIN(substr(r.created_at, 1, 10)), MAX(substr(r.created_at, 1, 10)), "
        "COALESCE(SUM(CASE WHEN r.judged_areas_json IS NULL THEN 1 ELSE 0 END), 0) "
        f"FROM film_reviews r {where}",
        params,
    ).fetchone()
    by_discipline = conn.execute(
        f"SELECT r.discipline, COUNT(*) FROM film_reviews r {where} "
        "GROUP BY r.discipline ORDER BY COUNT(*) DESC, r.discipline",
        params,
    ).fetchall()
    recent = conn.execute(
        "SELECT r.review_id AS id, r.created_at, r.discipline, r.frame_count, r.context, "
        "r.athlete_description, r.video_id, (v.video_id IS NOT NULL) AS video_available "
        "FROM film_reviews r LEFT JOIN videos v ON v.video_id = r.video_id AND v.owner = ? "
        f"{where} ORDER BY r.review_id DESC LIMIT 5",
        [owner, *params],
    ).fetchall()
    drills = conn.execute(
        "SELECT d.drill_key, d.name, d.category, d.discipline, COUNT(DISTINCT d.review_id) AS sessions, "
        "MAX(substr(r.created_at, 1, 10)) AS last_recommended "
        "FROM review_drills d JOIN film_reviews r ON r.review_id = d.review_id "
        f"{where} GROUP BY d.drill_key ORDER BY sessions DESC, d.name",
        params,
    ).fetchall()
    recurring = recurring_issues(conn, discipline, start, end)
    return {
        "sessions": summary[0],
        "first_day": summary[1],
        "last_day": summary[2],
        "sessions_without_coverage": summary[3],
        "by_discipline": [
            {"discipline": row[0], "label": disciplines.label_for(row[0]), "sessions": row[1]}
            for row in by_discipline
        ],
        "recent": [
            dict(row, discipline_label=disciplines.label_for(row["discipline"]),
                 video_available=bool(row["video_available"]))
            for row in recent
        ],
        "recurring": recurring,
        "areas_needing_review": [r for r in recurring if r["status"] in ("multi_day", "multi_session")],
        "drills": [dict(row) for row in drills],
        "rules": RULES,
    }


COMPARE_NOTE = (
    "These two sessions show what each report detected in the frames it sampled. They do not show whether "
    "technique improved. Something not detected in the newer session was not detected there, which is not "
    "the same as fixed."
)
COMPARE_LIMITS = [
    "Footage quality is not scored. Check both clips yourself before drawing conclusions.",
    "Each report samples a few frames. Anything between frames is not assessed.",
]

CHANGE_LABELS = {
    "in_both": "Detected in both sessions",
    "earlier_only": "Detected only in the earlier session",
    "newer_only": "Detected only in the newer session",
    "not_detected_both": "Judged in both, not detected in either",
    "cannot_compare": "Cannot be compared",
}
CHANGE_NOTES = {
    "in_both": "Detected in both sessions. Both reports assessed this area.",
    "earlier_only": "Detected in the earlier session only. The newer report assessed this area and did not detect it "
                    "in its frames. That is not evidence the area is fixed.",
    "newer_only": "Detected in the newer session only. The earlier report assessed this area and did not detect it "
                  "in its frames.",
    "not_detected_both": "Both reports assessed this area and did not detect it in their frames. This does not "
                         "show the area is free of problems.",
    "cannot_compare": "Cannot be compared. One report did not assess this area, or its coverage was saved before "
                      "assessed areas were recorded.",
}
_CHANGE_ORDER = {"in_both": 0, "earlier_only": 1, "newer_only": 2, "cannot_compare": 3, "not_detected_both": 4}


def _categories_judged(discipline, judged_json):
    areas = _judged_list(judged_json)
    if areas is None:
        return None
    return {disciplines.category_for(discipline, area) for area in areas}


def _state(category, detected, judged):
    if category in detected:
        return "detected"
    if judged is None:
        return "unknown"
    return "not_detected" if category in judged else "not_judged"


def _change(earlier, newer):
    if earlier == "detected" and newer == "detected":
        return "in_both"
    if earlier == "detected" and newer == "not_detected":
        return "earlier_only"
    if earlier == "not_detected" and newer == "detected":
        return "newer_only"
    if earlier == "not_detected" and newer == "not_detected":
        return "not_detected_both"
    return "cannot_compare"


def _session_view(row, frame_times, video_duration):
    span = round(max(frame_times), 1) if frame_times else None
    try:
        report = json.loads(row["report_json"]) if row["report_json"] else {}
    except (TypeError, ValueError):
        report = {}
    if not isinstance(report, dict):
        report = {}
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "discipline": row["discipline"],
        "discipline_label": disciplines.label_for(row["discipline"]),
        "frame_count": row["frame_count"],
        "frame_times": frame_times,
        "sampled_span_seconds": span,
        "video_seconds": round(video_duration, 1) if video_duration is not None else None,
        "evidence_status": report.get("evidence_status") or "unknown",
        "athlete_status": report.get("athlete_status") or "unknown",
        "athlete_description": row["athlete_description"] or "",
        "video_id": row["video_id"],
        "video_available": bool(row["video_available"]),
    }


def _parse_frame_times(value):
    try:
        times = json.loads(value) if value else []
    except (TypeError, ValueError):
        return []
    return [float(t) for t in times if isinstance(t, (int, float))] if isinstance(times, list) else []


def compare(conn, owner, first_id, second_id):
    """Side-by-side view of two sessions from the same sport. Raises LookupError or ValueError."""
    if first_id == second_id:
        raise ValueError("Choose two different sessions.")
    rows = {row["id"]: row for row in conn.execute(
        "SELECT r.review_id AS id, r.created_at, r.discipline, r.frame_count, r.frame_times_json, "
        "r.report_json, r.judged_areas_json, r.athlete_description, r.video_id, "
        "(v.video_id IS NOT NULL) AS video_available, v.duration_seconds AS video_duration "
        "FROM film_reviews r LEFT JOIN videos v ON v.video_id = r.video_id AND v.owner = ? "
        "WHERE r.review_id IN (?, ?)",
        (owner, first_id, second_id),
    ).fetchall()}
    if len(rows) != 2:
        raise LookupError("One of those sessions was not found.")
    earlier_row, newer_row = sorted(rows.values(), key=lambda r: (r["created_at"], r["id"]))
    if earlier_row["discipline"] != newer_row["discipline"]:
        raise ValueError("Compare two sessions from the same sport.")

    ids = (earlier_row["id"], newer_row["id"])
    findings, drills = defaultdict(list), defaultdict(list)
    for review_id, category, confidence, area, text, fix, frames_json in conn.execute(
        "SELECT review_id, category, confidence, area, finding, fix, frames_json "
        "FROM review_findings WHERE review_id IN (?, ?)", ids,
    ):
        if confidence in COUNTED_CONFIDENCE:
            findings[(review_id, category)].append({
                "area": area, "finding": text, "fix": fix, "confidence": confidence,
                "frames": json.loads(frames_json) if frames_json else [],
            })
    for review_id, category, name, instructions, objective, reps in conn.execute(
        "SELECT review_id, category, name, instructions, objective, reps_or_duration "
        "FROM review_drills WHERE review_id IN (?, ?)", ids,
    ):
        drills[(review_id, category)].append({
            "name": name, "instructions": instructions, "objective": objective, "reps_or_duration": reps,
        })

    sides = {}
    for role, row in (("earlier", earlier_row), ("newer", newer_row)):
        judged = _categories_judged(row["discipline"], row["judged_areas_json"])
        detected = {category for (review_id, category) in findings if review_id == row["id"]}
        view = _session_view(row, _parse_frame_times(row["frame_times_json"]), row["video_duration"])
        sides[role] = {"judged": judged, "detected": detected, "row": row, "view": view}

    categories = set()
    for side in sides.values():
        categories |= side["detected"] | (side["judged"] or set())

    session_caveats = _session_caveats(sides["earlier"]["view"], sides["newer"]["view"])
    entries = []
    for category in categories:
        states, detail = {}, {}
        for role, side in sides.items():
            review_id = side["row"]["id"]
            states[role] = _state(category, side["detected"], side["judged"])
            detail[role] = {
                "state": states[role],
                "findings": findings.get((review_id, category), []),
                "drills": drills.get((review_id, category), []),
            }
        change = _change(states["earlier"], states["newer"])
        caveats = list(session_caveats)
        if change == "cannot_compare":
            caveats.append("One report did not assess this area.")
        earlier_names = sorted(d["name"] for d in detail["earlier"]["drills"])
        newer_names = sorted(d["name"] for d in detail["newer"]["drills"])
        entries.append({
            "category": category,
            "change": change,
            "change_label": CHANGE_LABELS[change],
            "change_note": CHANGE_NOTES[change],
            "reliable": change != "cannot_compare" and not caveats,
            "caveats": caveats,
            "drills_changed": bool(earlier_names and newer_names and earlier_names != newer_names),
            "earlier": detail["earlier"],
            "newer": detail["newer"],
        })
    entries.sort(key=lambda e: (_CHANGE_ORDER[e["change"]], e["category"]))

    return {
        "discipline": earlier_row["discipline"],
        "discipline_label": disciplines.label_for(earlier_row["discipline"]),
        "earlier": sides["earlier"]["view"],
        "newer": sides["newer"]["view"],
        "warnings": _session_warnings(sides["earlier"]["view"], sides["newer"]["view"], sides),
        "limits": COMPARE_LIMITS,
        "categories": entries,
        "note": COMPARE_NOTE,
    }


def _same_athlete_name(a, b):
    return " ".join(a.lower().split()) == " ".join(b.lower().split())


def _session_caveats(earlier, newer):
    """Reasons a comparison of these two sessions needs care. Applied to every category."""
    caveats = []
    for label, session in (("earlier", earlier), ("newer", newer)):
        if session["evidence_status"] != "sufficient":
            caveats.append(f"Evidence was {session['evidence_status']} in the {label} session.")
    if earlier["athlete_status"] != "identified" or newer["athlete_status"] != "identified":
        caveats.append("The athlete was not confirmed in both sessions, so findings may describe different people.")
    if earlier["athlete_description"] and newer["athlete_description"] and not _same_athlete_name(
            earlier["athlete_description"], newer["athlete_description"]):
        caveats.append("The two sessions name different athletes.")
    return caveats


def _session_warnings(earlier, newer, sides):
    warnings = []
    if sides["earlier"]["judged"] is None or sides["newer"]["judged"] is None:
        warnings.append("One session was saved before assessed areas were recorded. An area not detected there "
                        "cannot be told apart from an area that was never assessed.")
    if earlier["frame_count"] != newer["frame_count"]:
        warnings.append(f"The sessions sampled different numbers of frames ({earlier['frame_count']} and "
                        f"{newer['frame_count']}).")
    spans = [s for s in (earlier["sampled_span_seconds"], newer["sampled_span_seconds"]) if s is not None]
    if len(spans) == 2 and abs(spans[0] - spans[1]) > max(2.0, 0.1 * max(spans)):
        warnings.append(f"The sampled spans differ ({spans[0]} s and {spans[1]} s), so the clips may differ in length.")
    if earlier["video_seconds"] is not None and newer["video_seconds"] is not None \
            and abs(earlier["video_seconds"] - newer["video_seconds"]) > 1.0:
        warnings.append(f"The clips differ in length ({earlier['video_seconds']} s and {newer['video_seconds']} s).")
    return warnings


PRACTICE_NOTE = (
    "A practice log records that a drill was done on a given day. It is not evidence that technique improved, "
    "and it does not change what the saved reports show."
)


def drill_library(conn, discipline=None, start=None, end=None):
    """Drills recommended in saved reports in scope, with the findings they address and each practice log.

    A drill appears only while a saved report in scope recommends it. Practice logs are kept when reports are
    deleted, so they remain in the practice history."""
    where, params = _scope(discipline, start, end)
    recommended = conn.execute(
        "SELECT d.drill_key, d.name, d.category, d.discipline, d.instructions, d.objective, "
        "d.reps_or_duration, d.review_id, substr(r.created_at, 1, 10) AS day "
        "FROM review_drills d JOIN film_reviews r ON r.review_id = d.review_id "
        f"{where} ORDER BY r.review_id DESC",
        params,
    ).fetchall()
    status_by_category = {
        (row["discipline"], row["category"]): row["status"]
        for row in recurring_issues(conn, discipline, start, end)
    }
    library = {}
    for row in recommended:
        entry = library.get(row["drill_key"])
        if entry is None:
            entry = library[row["drill_key"]] = {
                "drill_key": row["drill_key"],
                "name": row["name"],
                "discipline": row["discipline"],
                "discipline_label": disciplines.label_for(row["discipline"]),
                "category": row["category"],
                "instructions": row["instructions"],
                "objective": row["objective"],
                "reps_or_duration": row["reps_or_duration"],
                "review_ids": set(),
                "last_recommended": row["day"],
                "category_status": status_by_category.get((row["discipline"], row["category"]), "not_detected"),
                "linked_findings": [],
                "practice": {"count": 0, "last": None, "recent": []},
            }
        entry["review_ids"].add(row["review_id"])
    for entry in library.values():
        entry["sessions"] = len(entry["review_ids"])
        entry["review_ids"] = sorted(entry["review_ids"], reverse=True)
        linked = conn.execute(
            "SELECT DISTINCT f.finding FROM review_findings f WHERE f.review_id IN "
            f"({','.join('?' * len(entry['review_ids']))}) AND f.category = ? "
            "AND f.confidence IN ('observed', 'inferred') AND f.finding != '' ORDER BY f.finding LIMIT 3",
            [*entry["review_ids"], entry["category"]],
        ).fetchall()
        entry["linked_findings"] = [row[0] for row in linked]
        entry["category_status_label"] = STATUS_LABELS[entry["category_status"]]
    for practice in conn.execute(
        "SELECT practice_id, drill_key, practiced_on, note FROM drill_practice "
        "ORDER BY practiced_on DESC, practice_id DESC"
    ).fetchall():
        entry = library.get(practice["drill_key"])
        if entry is None:
            continue
        log = entry["practice"]
        log["count"] += 1
        log["last"] = log["last"] or practice["practiced_on"]
        if len(log["recent"]) < 5:
            log["recent"].append({"id": practice["practice_id"], "date": practice["practiced_on"],
                                  "note": practice["note"]})
    result = sorted(library.values(),
                    key=lambda e: (_STATUS_ORDER[e["category_status"]], -e["sessions"], e["name"]))
    return {"drills": result, "note": PRACTICE_NOTE}


def add_practice(conn, drill_key, practiced_on, note):
    """Logs one practice day for a drill that a saved report recommends. Raises LookupError if unknown."""
    known = conn.execute(
        "SELECT name FROM review_drills WHERE drill_key = ? ORDER BY review_id DESC LIMIT 1", (drill_key,)
    ).fetchone()
    if known is None:
        raise LookupError("That drill is not in any saved report.")
    cur = conn.execute(
        "INSERT INTO drill_practice (drill_key, drill_name, practiced_on, note, created_at) "
        "VALUES (?, ?, ?, ?, datetime('now'))",
        (drill_key, known[0], practiced_on, note),
    )
    return cur.lastrowid


def delete_practice(conn, practice_id):
    return conn.execute("DELETE FROM drill_practice WHERE practice_id = ?", (practice_id,)).rowcount


def practice_history(conn, limit=50, offset=0):
    """Every practice day logged, newest first, including drills no saved report recommends any more."""
    total = conn.execute("SELECT COUNT(*) FROM drill_practice").fetchone()[0]
    rows = conn.execute(
        "SELECT p.practice_id AS id, p.drill_key, p.drill_name, p.practiced_on, p.note, "
        "EXISTS (SELECT 1 FROM review_drills d WHERE d.drill_key = p.drill_key) AS in_library "
        "FROM drill_practice p ORDER BY p.practiced_on DESC, p.practice_id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return {
        "total": total,
        "entries": [dict(row, in_library=bool(row["in_library"])) for row in rows],
        "note": PRACTICE_NOTE,
    }


def _like(text):
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped.lower()}%"


def history(conn, owner, filters, limit=25, offset=0):
    """Saved sessions matching the filters, newest first. Keys: discipline, start, end, text,
    category, recurring (bool), drill. A recurring filter uses the same date and sport filters as the dashboard."""
    clauses, params = [], []
    if filters.get("discipline"):
        clauses.append("r.discipline = ?")
        params.append(filters["discipline"])
    if filters.get("start"):
        clauses.append("substr(r.created_at, 1, 10) >= ?")
        params.append(filters["start"])
    if filters.get("end"):
        clauses.append("substr(r.created_at, 1, 10) <= ?")
        params.append(filters["end"])
    if filters.get("text"):
        pattern = _like(filters["text"])
        clauses.append(
            "(LOWER(COALESCE(r.context, '')) LIKE ? ESCAPE '\\' OR LOWER(r.feedback) LIKE ? ESCAPE '\\' "
            "OR LOWER(COALESCE(r.athlete_description, '')) LIKE ? ESCAPE '\\' "
            "OR EXISTS (SELECT 1 FROM review_findings f WHERE f.review_id = r.review_id "
            "AND LOWER(f.finding) LIKE ? ESCAPE '\\'))"
        )
        params.extend([pattern, pattern, pattern, pattern])
    if filters.get("category"):
        clauses.append(
            "EXISTS (SELECT 1 FROM review_findings f WHERE f.review_id = r.review_id AND f.category = ? "
            "AND f.confidence IN ('observed', 'inferred'))"
        )
        params.append(filters["category"])
    if filters.get("drill"):
        clauses.append(
            "EXISTS (SELECT 1 FROM review_drills d WHERE d.review_id = r.review_id AND LOWER(d.name) LIKE ? "
            "ESCAPE '\\')"
        )
        params.append(_like(filters["drill"]))
    if filters.get("recurring"):
        recurring = [(r["discipline"], r["category"]) for r in recurring_issues(
            conn, filters.get("discipline"), filters.get("start"), filters.get("end"))
            if r["status"] in ("multi_day", "multi_session")]
        if not recurring:
            clauses.append("0 = 1")
        else:
            options = " OR ".join("(f.discipline = ? AND f.category = ?)" for _ in recurring)
            clauses.append(
                "EXISTS (SELECT 1 FROM review_findings f WHERE f.review_id = r.review_id "
                f"AND f.confidence IN ('observed', 'inferred') AND ({options}))"
            )
            for sport, category in recurring:
                params.extend([sport, category])
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    total = conn.execute(f"SELECT COUNT(*) FROM film_reviews r {where}", params).fetchone()[0]
    rows = conn.execute(
        "SELECT r.review_id AS id, r.created_at, r.discipline, r.mode, r.context, r.frame_count, "
        "r.athlete_description, r.video_id, (v.video_id IS NOT NULL) AS video_available, "
        "(SELECT COUNT(*) FROM review_findings f WHERE f.review_id = r.review_id "
        " AND f.confidence IN ('observed', 'inferred')) AS findings "
        "FROM film_reviews r LEFT JOIN videos v ON v.video_id = r.video_id AND v.owner = ? "
        f"{where} ORDER BY r.review_id DESC LIMIT ? OFFSET ?",
        [owner, *params, limit, offset],
    ).fetchall()
    items = [
        dict(row, discipline_label=disciplines.label_for(row["discipline"]),
             video_available=bool(row["video_available"]))
        for row in rows
    ]
    return {"total": total, "items": items}
