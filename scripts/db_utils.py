"""Shared helpers for connecting to the training database and resolving
controlled-vocabulary lookups by name instead of hardcoding IDs."""
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "db" / "training.db"


def get_connection() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"No database at {DB_PATH}. Run `python scripts/init_db.py` first."
        )
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.row_factory = sqlite3.Row
    return conn


def lookup_id(conn: sqlite3.Connection, table: str, name: str) -> int:
    """Resolve a controlled-vocabulary name (e.g. 'half_guard') to its ID.
    Raises ValueError with the valid options if the name isn't recognized —
    this is the enforcement point that keeps position/technique from
    degrading into free text."""
    id_col = f"{table[:-1]}_id" if table.endswith("s") else f"{table}_id"
    row = conn.execute(f"SELECT {id_col} FROM {table} WHERE name = ?", (name,)).fetchone()
    if row is None:
        valid = [r["name"] for r in conn.execute(f"SELECT name FROM {table} ORDER BY name")]
        raise ValueError(f"Unknown {table[:-1]} '{name}'. Valid values: {valid}")
    return row[id_col]


def list_vocab(conn: sqlite3.Connection, table: str) -> list:
    return [r["name"] for r in conn.execute(f"SELECT name FROM {table} ORDER BY name")]
