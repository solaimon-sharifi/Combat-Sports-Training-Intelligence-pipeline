"""Versioned SQLite migrations. PRAGMA user_version records how far a database has been upgraded.

schema.sql stays the baseline for fresh databases. Each change to an existing
table goes here as a numbered step, so an existing database upgrades once and
keeps its rows.
"""

import analytics

MIGRATIONS = {
    1: [
        "ALTER TABLE film_reviews ADD COLUMN report_json TEXT",
        "ALTER TABLE film_reviews ADD COLUMN report_version INTEGER",
        "ALTER TABLE film_reviews ADD COLUMN request_hash TEXT",
        "ALTER TABLE film_reviews ADD COLUMN frame_times_json TEXT",
        "ALTER TABLE film_reviews ADD COLUMN model TEXT",
        "ALTER TABLE film_reviews ADD COLUMN input_tokens INTEGER",
        "ALTER TABLE film_reviews ADD COLUMN output_tokens INTEGER",
        "CREATE INDEX IF NOT EXISTS idx_film_reviews_request ON film_reviews(request_hash)",
    ],
    2: [
        "ALTER TABLE film_reviews ADD COLUMN discipline TEXT",
        "ALTER TABLE film_reviews ADD COLUMN prompt_version TEXT",
        "CREATE INDEX IF NOT EXISTS idx_film_reviews_cache "
        "ON film_reviews(request_hash, discipline, model, report_version, prompt_version)",
    ],
    3: [
        "CREATE TABLE IF NOT EXISTS videos ("
        " video_id INTEGER PRIMARY KEY,"
        " owner TEXT NOT NULL,"
        " storage_name TEXT NOT NULL UNIQUE,"
        " display_name TEXT NOT NULL,"
        " mime TEXT NOT NULL,"
        " size_bytes INTEGER NOT NULL,"
        " duration_seconds REAL NOT NULL,"
        " sha256 TEXT NOT NULL,"
        " created_at TEXT NOT NULL,"
        " expires_at TEXT NOT NULL)",
        "CREATE INDEX IF NOT EXISTS idx_videos_expiry ON videos(expires_at)",
        "CREATE TABLE IF NOT EXISTS auth_sessions ("
        " session_hash TEXT PRIMARY KEY,"
        " owner TEXT NOT NULL,"
        " created_at TEXT NOT NULL,"
        " expires_at TEXT NOT NULL)",
        "ALTER TABLE film_reviews ADD COLUMN video_id INTEGER",
        "ALTER TABLE film_reviews ADD COLUMN athlete_description TEXT",
        "CREATE INDEX IF NOT EXISTS idx_film_reviews_video ON film_reviews(video_id)",
    ],
    4: [
        "ALTER TABLE videos ADD COLUMN codec TEXT",
    ],
    5: [
        "ALTER TABLE film_reviews ADD COLUMN judged_areas_json TEXT",
        "CREATE TABLE IF NOT EXISTS review_findings ("
        " finding_id INTEGER PRIMARY KEY,"
        " review_id INTEGER NOT NULL,"
        " position INTEGER NOT NULL,"
        " discipline TEXT NOT NULL,"
        " area TEXT NOT NULL,"
        " category TEXT NOT NULL,"
        " confidence TEXT NOT NULL,"
        " finding TEXT NOT NULL,"
        " fix TEXT NOT NULL,"
        " frames_json TEXT NOT NULL)",
        "CREATE INDEX IF NOT EXISTS idx_review_findings_review ON review_findings(review_id)",
        "CREATE INDEX IF NOT EXISTS idx_review_findings_category ON review_findings(discipline, category)",
        "CREATE TABLE IF NOT EXISTS review_drills ("
        " drill_id INTEGER PRIMARY KEY,"
        " review_id INTEGER NOT NULL,"
        " position INTEGER NOT NULL,"
        " discipline TEXT NOT NULL,"
        " area TEXT NOT NULL,"
        " category TEXT NOT NULL,"
        " drill_key TEXT NOT NULL,"
        " name TEXT NOT NULL,"
        " instructions TEXT NOT NULL,"
        " objective TEXT NOT NULL,"
        " reps_or_duration TEXT NOT NULL)",
        "CREATE INDEX IF NOT EXISTS idx_review_drills_review ON review_drills(review_id)",
        "CREATE INDEX IF NOT EXISTS idx_review_drills_key ON review_drills(drill_key)",
        "CREATE TABLE IF NOT EXISTS drill_practice ("
        " practice_id INTEGER PRIMARY KEY,"
        " drill_key TEXT NOT NULL,"
        " drill_name TEXT NOT NULL DEFAULT '',"
        " practiced_on TEXT NOT NULL,"
        " note TEXT NOT NULL DEFAULT '',"
        " created_at TEXT NOT NULL)",
        "CREATE INDEX IF NOT EXISTS idx_drill_practice_key ON drill_practice(drill_key, practiced_on)",
        analytics.backfill,
    ],
}


def current_version(conn):
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrate(conn):
    """Apply every pending migration in order. Each step runs in its own transaction."""
    previous_isolation = conn.isolation_level
    conn.isolation_level = None
    try:
        for target in sorted(MIGRATIONS):
            if target <= current_version(conn):
                continue
            conn.execute("BEGIN")
            try:
                for statement in MIGRATIONS[target]:
                    if callable(statement):
                        statement(conn)
                    else:
                        conn.execute(statement)
                conn.execute(f"PRAGMA user_version = {int(target)}")
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
    finally:
        conn.isolation_level = previous_isolation
    return current_version(conn)
