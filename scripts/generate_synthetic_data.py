"""
Generate ~50-80 synthetic training sessions with plausible baked-in trends,
for prototyping the pipeline before real data accumulates.

*** ALL DATA PRODUCED BY THIS SCRIPT IS SYNTHETIC. *** It is flagged with
sessions.is_synthetic = 1 so downstream queries and the README can label it
accordingly. Do not present it as real training history.

Baked-in trends (by design, so the analysis layer has something real to find):
  1. Takedown success rate (for-direction) rises from ~35% to ~65% over the
     26-week window — a "getting better at takedowns" story.
  2. Submissions conceded from the 'back' position are disproportionately
     common (~45% of all conceded submissions) — a specific, fixable leak.
  3. Performance against higher-belt opponents is worse across the board —
     submission/takedown success drops as opponent belt rank rises.

Usage:
    python scripts/generate_synthetic_data.py [--seed 42] [--sessions 65]
"""
import argparse
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db_utils import get_connection  # noqa: E402

WEEKS = 26
GRAPPLING_POSITIONS = [
    "standing", "closed_guard", "open_guard", "half_guard",
    "side_control", "mount", "back", "turtle", "north_south",
]
SUBMISSION_POSITIONS = [p for p in GRAPPLING_POSITIONS if p != "standing"]

DISCIPLINE_WEIGHTS = {"BJJ": 0.55, "No-Gi": 0.25, "Wrestling": 0.10, "MMA": 0.10}
EXCHANGE_TYPE_WEIGHTS = {
    "takedown": 0.28, "submission": 0.25, "sweep": 0.15,
    "escape": 0.15, "pass": 0.12, "reversal": 0.05,
}


def weighted_choice(rng: random.Random, weights: dict):
    items, probs = zip(*weights.items())
    return rng.choices(items, weights=probs, k=1)[0]


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def build_opponent_pool(conn, rng: random.Random, n: int = 18) -> list[int]:
    ids = []
    belt_dist = [0, 0, 1, 1, 1, 2, 2, 2, 3, 3, 4]  # skewed toward blue/purple, a few brown/black
    for i in range(n):
        base_belt = rng.choice(belt_dist)
        stripes = rng.choice([0, 0, 1, 2, 3]) / 10.0
        belt_rank = min(4.9, base_belt + stripes)
        weight_class = rng.choice(["light", "middle", "heavy", None])
        label = f"training_partner_{i+1}"
        cur = conn.execute(
            "INSERT INTO opponents (label, belt_rank, weight_class) VALUES (?, ?, ?)",
            (label, belt_rank, weight_class),
        )
        ids.append((cur.lastrowid, belt_rank))
    return ids


def takedown_trend_prob(week_index: int) -> float:
    progress = week_index / (WEEKS - 1)
    base = 0.35 + progress * 0.30
    return clamp(base + random.gauss(0, 0.05), 0.10, 0.90)


def pick_submission_position(rng: random.Random, direction: str) -> str:
    if direction == "against":
        # Baked-in weakness: back accounts for ~45% of conceded submissions.
        weights = {p: (0.45 if p == "back" else 0.55 / (len(SUBMISSION_POSITIONS) - 1))
                   for p in SUBMISSION_POSITIONS}
    else:
        # Submissions landed are more evenly spread, mount/back slightly favored.
        weights = {p: (0.20 if p in ("mount", "back") else 0.60 / (len(SUBMISSION_POSITIONS) - 2))
                   for p in SUBMISSION_POSITIONS}
    items, probs = zip(*weights.items())
    return rng.choices(items, weights=probs, k=1)[0]


def pick_outcome(rng: random.Random, p_success: float, allow_defended: bool) -> str:
    r = rng.random()
    if r < p_success:
        return "successful"
    remaining = 1 - p_success
    if allow_defended and rng.random() < 0.55:
        return "defended"
    return "failed"


def generate_exchange(conn, rng, session_id, week_index, opponent_id, opponent_belt, type_ids, tech_by_type, pos_ids):
    exchange_type = weighted_choice(rng, EXCHANGE_TYPE_WEIGHTS)
    direction = rng.choices(["for", "against"], weights=[0.55, 0.45], k=1)[0]
    belt_penalty = opponent_belt * 0.06  # higher belt opponent -> worse odds for the user

    if exchange_type == "takedown":
        position = "standing"
        if direction == "for":
            p_success = clamp(takedown_trend_prob(week_index) - belt_penalty, 0.05, 0.95)
        else:
            p_success = clamp(0.40 + belt_penalty, 0.05, 0.95)
        outcome = pick_outcome(rng, p_success, allow_defended=False)

    elif exchange_type == "submission":
        position = pick_submission_position(rng, direction)
        if direction == "for":
            p_success = clamp(0.50 - belt_penalty, 0.05, 0.90)
        else:
            back_bonus = 0.15 if position == "back" else 0.0
            p_success = clamp(0.35 + belt_penalty + back_bonus, 0.05, 0.90)
        outcome = pick_outcome(rng, p_success, allow_defended=True)

    else:
        position = rng.choice([p for p in GRAPPLING_POSITIONS if p != "standing"]) \
            if exchange_type in ("sweep", "escape", "reversal") else rng.choice(GRAPPLING_POSITIONS)
        p_success = clamp(0.50 - (belt_penalty / 2 if direction == "for" else -belt_penalty / 2), 0.10, 0.90)
        outcome = pick_outcome(rng, p_success, allow_defended=True)

    technique_id = None
    candidates = tech_by_type.get(exchange_type)
    if candidates:
        technique_id = rng.choice(candidates)

    conn.execute(
        """INSERT INTO exchanges
           (session_id, opponent_id, exchange_type_id, direction, technique_id, position_id, outcome)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (session_id, opponent_id, type_ids[exchange_type], direction, technique_id, pos_ids[position], outcome),
    )


def generate(conn, rng: random.Random, target_sessions: int) -> int:
    disciplines = {r["name"]: r["discipline_id"] for r in conn.execute("SELECT * FROM disciplines")}
    type_ids = {r["name"]: r["exchange_type_id"] for r in conn.execute("SELECT * FROM exchange_types")}
    pos_ids = {r["name"]: r["position_id"] for r in conn.execute("SELECT * FROM positions")}
    tech_rows = conn.execute(
        """SELECT t.technique_id, et.name AS type_name FROM techniques t
           JOIN exchange_types et ON et.exchange_type_id = t.exchange_type_id"""
    ).fetchall()
    tech_by_type: dict[str, list[int]] = {}
    for row in tech_rows:
        tech_by_type.setdefault(row["type_name"], []).append(row["technique_id"])

    opponents = build_opponent_pool(conn, rng)

    start = date.today() - timedelta(weeks=WEEKS)
    sessions_created = 0

    # Distribute target_sessions roughly evenly across WEEKS with variance,
    # including some skipped weeks (injury/travel/life) for realism.
    per_week_counts = []
    remaining = target_sessions
    for w in range(WEEKS):
        weeks_left = WEEKS - w
        avg = remaining / weeks_left
        if w < WEEKS - 1 and rng.random() < 0.10:  # ~10% chance of a skipped week
            count = 0
        elif w == WEEKS - 1:
            count = max(0, remaining)  # last week absorbs whatever's left
        else:
            count = max(0, min(remaining, round(rng.gauss(avg, 0.8))))
        per_week_counts.append(count)
        remaining -= count

    for week_index, count in enumerate(per_week_counts):
        for _ in range(count):
            day_offset = week_index * 7 + rng.randint(0, 6)
            session_date = start + timedelta(days=day_offset)
            if session_date > date.today():
                session_date = date.today()

            discipline_name = weighted_choice(rng, DISCIPLINE_WEIGHTS)
            duration_min = rng.randint(45, 90)
            rounds = rng.randint(4, 8)

            cur = conn.execute(
                """INSERT INTO sessions (date, discipline_id, duration_min, rounds, notes, is_synthetic)
                   VALUES (?, ?, ?, ?, ?, 1)""",
                (session_date.isoformat(), disciplines[discipline_name], duration_min, rounds,
                 "Synthetic session generated for pipeline prototyping."),
            )
            session_id = cur.lastrowid
            sessions_created += 1

            n_rolls = rng.randint(1, 3)
            session_opponents = rng.sample(opponents, k=min(n_rolls, len(opponents)))
            for opponent_id, opponent_belt in session_opponents:
                n_exchanges = rng.randint(3, 8)
                for _ in range(n_exchanges):
                    generate_exchange(
                        conn, rng, session_id, week_index, opponent_id, opponent_belt,
                        type_ids, tech_by_type, pos_ids,
                    )

    conn.commit()
    return sessions_created


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--sessions", type=int, default=65, help="target number of sessions (50-80 recommended)")
    parser.add_argument("--wipe-synthetic", action="store_true",
                         help="delete existing synthetic sessions before generating new ones")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    random.seed(args.seed)  # takedown_trend_prob uses the module-level `random`

    conn = get_connection()

    if args.wipe_synthetic:
        conn.execute("DELETE FROM sessions WHERE is_synthetic = 1")
        conn.execute(
            """DELETE FROM opponents WHERE opponent_id NOT IN
               (SELECT DISTINCT opponent_id FROM exchanges WHERE opponent_id IS NOT NULL)"""
        )
        conn.commit()
        print("Wiped existing synthetic sessions and orphaned opponents.")

    n = generate(conn, rng, args.sessions)
    n_exchanges = conn.execute("SELECT COUNT(*) c FROM exchanges").fetchone()["c"]
    conn.close()
    print(f"Generated {n} synthetic sessions with {n_exchanges} total exchanges (seed={args.seed}).")
    print("All rows are flagged sessions.is_synthetic = 1.")


if __name__ == "__main__":
    main()
