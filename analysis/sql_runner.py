"""Load and execute the .sql files in sql/ against the training database,
returning results as pandas DataFrames. Keeps the SQL itself in plain .sql
files (for readability/portability) rather than embedding query strings
directly in Python."""
import sqlite3
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "db" / "training.db"
SQL_DIR = ROOT / "sql"


def run_sql_file(filename: str, conn: sqlite3.Connection | None = None) -> pd.DataFrame:
    own_conn = conn is None
    if own_conn:
        conn = sqlite3.connect(DB_PATH)
    try:
        query = (SQL_DIR / filename).read_text()
        return pd.read_sql_query(query, conn)
    finally:
        if own_conn:
            conn.close()


def get_connection() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


QUERIES = {
    "takedown_trend": "takedown_trend.sql",
    "submission_conceded_by_position": "submission_conceded_by_position.sql",
    "performance_by_belt": "performance_by_belt.sql",
    "session_volume": "session_volume.sql",
}


def run_all() -> dict[str, pd.DataFrame]:
    conn = get_connection()
    try:
        return {name: run_sql_file(fname, conn) for name, fname in QUERIES.items()}
    finally:
        conn.close()


if __name__ == "__main__":
    results = run_all()
    for name, df in results.items():
        print(f"\n=== {name} ===")
        print(df.to_string(index=False))
