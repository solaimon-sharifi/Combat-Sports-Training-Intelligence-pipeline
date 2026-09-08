"""
Export the SQL analysis outputs (plus supporting raw tables) to CSV in
exports/, for Power BI to import directly (Get Data > Text/CSV) — no
ODBC driver setup required.

Usage:
    python scripts/export_for_powerbi.py
"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "analysis"))
from sql_runner import run_all  # noqa: E402

DB_PATH = ROOT / "db" / "training.db"
EXPORT_DIR = ROOT / "exports"

RAW_TABLE_QUERIES = {
    "sessions_flat": """
        SELECT s.session_id, s.date, d.name AS discipline, s.duration_min, s.rounds,
               s.is_synthetic, s.notes
        FROM sessions s JOIN disciplines d ON d.discipline_id = s.discipline_id
    """,
    "exchanges_flat": """
        SELECT e.exchange_id, e.session_id, s.date, et.name AS exchange_type, e.direction,
               t.name AS technique, p.name AS position, e.outcome,
               o.opponent_id, o.belt_rank, o.weight_class
        FROM exchanges e
        JOIN sessions s ON s.session_id = e.session_id
        JOIN exchange_types et ON et.exchange_type_id = e.exchange_type_id
        LEFT JOIN techniques t ON t.technique_id = e.technique_id
        JOIN positions p ON p.position_id = e.position_id
        LEFT JOIN opponents o ON o.opponent_id = e.opponent_id
    """,
}


def main():
    EXPORT_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)

    # Analysis-layer outputs (already aggregated, ready for direct chart binding)
    for name, df in run_all().items():
        out_path = EXPORT_DIR / f"{name}.csv"
        df.to_csv(out_path, index=False)
        print(f"Wrote {out_path} ({len(df)} rows)")

    # Flat raw tables, for any ad-hoc slicing/dicing in Power BI itself
    for name, query in RAW_TABLE_QUERIES.items():
        df = pd.read_sql_query(query, conn)
        out_path = EXPORT_DIR / f"{name}.csv"
        df.to_csv(out_path, index=False)
        print(f"Wrote {out_path} ({len(df)} rows)")

    conn.close()
    print(f"\nAll exports written to {EXPORT_DIR}/. In Power BI: Get Data > Text/CSV.")


if __name__ == "__main__":
    main()
