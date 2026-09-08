"""
Create (or reset) the SQLite database from schema.sql.

Usage:
    python scripts/init_db.py            # create db/training.db if missing
    python scripts/init_db.py --reset    # drop all tables and rebuild
"""
import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "db" / "training.db"
SCHEMA_PATH = ROOT / "schema.sql"


def init_db(reset: bool = False) -> None:
    DB_PATH.parent.mkdir(exist_ok=True)

    if reset and DB_PATH.exists():
        DB_PATH.unlink()
        print(f"Removed existing database at {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(SCHEMA_PATH.read_text())
        conn.commit()
        print(f"Database ready at {DB_PATH}")
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="drop and rebuild all tables")
    args = parser.parse_args()

    if args.reset:
        confirm = input(f"This will delete {DB_PATH} and all data in it. Type 'yes' to continue: ")
        if confirm.strip().lower() != "yes":
            print("Aborted.")
            sys.exit(0)

    init_db(reset=args.reset)
