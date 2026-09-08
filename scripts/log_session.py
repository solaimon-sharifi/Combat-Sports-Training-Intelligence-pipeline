"""
Interactive CLI for logging a real training session and its exchanges.

Usage:
    python scripts/log_session.py

Walks through: session metadata -> repeated exchange entry -> commit.
All position/technique/discipline input is validated against the lookup
tables in schema.sql; typos are rejected immediately instead of silently
becoming a new free-text value.
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db_utils import get_connection, lookup_id, list_vocab  # noqa: E402


def prompt_choice(conn: sqlite3.Connection, table: str, label: str, allow_blank: bool = False) -> int | None:
    options = list_vocab(conn, table)
    while True:
        raw = input(f"{label} [{', '.join(options)}]{' (blank to skip)' if allow_blank else ''}: ").strip()
        if allow_blank and raw == "":
            return None
        try:
            return lookup_id(conn, table, raw)
        except ValueError as e:
            print(f"  {e}")


def prompt_int(label: str, default: int | None = None) -> int:
    suffix = f" [{default}]" if default is not None else ""
    while True:
        raw = input(f"{label}{suffix}: ").strip()
        if raw == "" and default is not None:
            return default
        try:
            return int(raw)
        except ValueError:
            print("  Enter a whole number.")


def maybe_lookup_opponent(conn: sqlite3.Connection) -> int | None:
    raw = input("Opponent ID (blank if unknown/not tracked, 'new' to add one): ").strip()
    if raw == "":
        return None
    if raw.lower() == "new":
        belt = float(input("  Belt rank (white=0, blue=1, purple=2, brown=3, black=4, + stripes as .1-.4): ").strip())
        weight = input("  Weight class (optional): ").strip() or None
        label = input("  Label/nickname (optional): ").strip() or None
        cur = conn.execute(
            "INSERT INTO opponents (label, belt_rank, weight_class) VALUES (?, ?, ?)",
            (label, belt, weight),
        )
        print(f"  Created opponent_id={cur.lastrowid}")
        return cur.lastrowid
    return int(raw)


def log_exchange(conn: sqlite3.Connection, session_id: int) -> None:
    opponent_id = maybe_lookup_opponent(conn)
    exchange_type_id = prompt_choice(conn, "exchange_types", "Exchange type")
    direction = ""
    while direction not in ("for", "against"):
        direction = input("Direction [for/against]: ").strip().lower()
    technique_id = prompt_choice(conn, "techniques", "Technique", allow_blank=True)
    position_id = prompt_choice(conn, "positions", "Position")
    outcome = ""
    while outcome not in ("successful", "failed", "defended"):
        outcome = input("Outcome [successful/failed/defended]: ").strip().lower()

    conn.execute(
        """INSERT INTO exchanges
           (session_id, opponent_id, exchange_type_id, direction, technique_id, position_id, outcome)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (session_id, opponent_id, exchange_type_id, direction, technique_id, position_id, outcome),
    )


def main() -> None:
    conn = get_connection()

    date = input("Date (YYYY-MM-DD): ").strip()
    discipline_id = prompt_choice(conn, "disciplines", "Discipline")
    duration_min = prompt_int("Duration (minutes)")
    rounds = prompt_int("Rounds", default=0)
    notes = input("Notes (optional): ").strip() or None

    cur = conn.execute(
        """INSERT INTO sessions (date, discipline_id, duration_min, rounds, notes, is_synthetic)
           VALUES (?, ?, ?, ?, ?, 0)""",
        (date, discipline_id, duration_min, rounds, notes),
    )
    session_id = cur.lastrowid
    print(f"\nSession {session_id} created. Now log exchanges (Ctrl+C or blank type to stop).\n")

    count = 0
    while True:
        print(f"--- Exchange {count + 1} ---")
        try:
            log_exchange(conn, session_id)
            count += 1
        except (KeyboardInterrupt, EOFError):
            break
        cont = input("Log another exchange? [y/N]: ").strip().lower()
        if cont != "y":
            break

    conn.commit()
    conn.close()
    print(f"\nSaved session {session_id} with {count} exchange(s).")


if __name__ == "__main__":
    main()
