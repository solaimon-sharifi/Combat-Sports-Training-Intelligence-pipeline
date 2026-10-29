import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

from migrations import MIGRATIONS, current_version, migrate  # noqa: E402

SCHEMA = (ROOT / "schema.sql").read_text()
NEW_COLUMNS = {"report_json", "report_version", "request_hash", "frame_times_json", "model", "input_tokens", "output_tokens"}


def columns(conn):
    return {row[1] for row in conn.execute("PRAGMA table_info(film_reviews)")}


def test_fresh_database_reaches_latest_version_with_new_columns(tmp_path):
    conn = sqlite3.connect(tmp_path / "fresh.db")
    conn.executescript(SCHEMA)
    assert migrate(conn) == max(MIGRATIONS)
    assert NEW_COLUMNS <= columns(conn)
    conn.close()


def test_existing_database_upgrades_and_keeps_its_rows(tmp_path):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE film_reviews (review_id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, "
        "mode TEXT NOT NULL, context TEXT, frame_count INTEGER NOT NULL, feedback TEXT NOT NULL);"
    )
    conn.execute(
        "INSERT INTO film_reviews (created_at, mode, context, frame_count, feedback) "
        "VALUES ('2026-09-01T10:00:00', 'upload', 'old note', 8, 'Observations: old plain-text report')"
    )
    conn.commit()

    migrate(conn)
    row = conn.execute("SELECT feedback, report_json FROM film_reviews").fetchone()
    assert row[0].startswith("Observations: old plain-text report")
    assert row[1] is None
    assert NEW_COLUMNS <= columns(conn)
    conn.close()


def test_running_migrations_twice_is_a_no_op(tmp_path):
    conn = sqlite3.connect(tmp_path / "twice.db")
    conn.executescript(SCHEMA)
    migrate(conn)
    version = current_version(conn)
    migrate(conn)
    assert current_version(conn) == version
    conn.close()


def test_failed_migration_rolls_back_and_keeps_the_old_version(tmp_path, monkeypatch):
    conn = sqlite3.connect(tmp_path / "broken.db")
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    monkeypatch.setitem(MIGRATIONS, 1, [
        "ALTER TABLE film_reviews ADD COLUMN report_json TEXT",
        "ALTER TABLE no_such_table ADD COLUMN boom TEXT",
    ])
    with pytest.raises(sqlite3.OperationalError):
        migrate(conn)
    assert current_version(conn) == 0
    assert "report_json" not in columns(conn)
    conn.close()
