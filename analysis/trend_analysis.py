"""
Pattern-finding on top of the SQL aggregates:
  - linear trend slope on weekly takedown success rate, expressed as a
    plain "increased X% over N weeks" claim WITH the sample size behind it
  - flag positions where the submission-conceded rate is statistically
    notable relative to the overall average (simple proportion comparison —
    deliberately not a fancier model than a ~65-session dataset supports)

Run directly to print the full report, or import compute_takedown_trend /
flag_notable_positions from other scripts (insights.py does this).
"""
from dataclasses import dataclass

import numpy as np

from sql_runner import run_sql_file, get_connection


@dataclass
class TrendResult:
    slope_pct_per_week: float
    total_change_pct: float
    weeks_covered: int
    total_attempts: int
    first_week_rate: float
    last_week_rate: float


def compute_takedown_trend(min_attempts_per_week: int = 1) -> TrendResult | None:
    """Linear regression of weekly takedown success rate against week index.
    Weeks with fewer than `min_attempts_per_week` attempts are still counted
    in total_attempts but the regression itself is weighted by attempts so
    a 1-attempt week doesn't move the line as much as a 15-attempt week."""
    df = run_sql_file("takedown_trend.sql")
    if df.empty or len(df) < 2:
        return None

    x = np.arange(len(df))
    y = df["success_rate_pct"].to_numpy()
    weights = df["attempts"].to_numpy()

    # Weighted least squares, degree 1
    coeffs = np.polyfit(x, y, deg=1, w=np.sqrt(weights))
    slope = coeffs[0]

    total_attempts = int(df["attempts"].sum())
    predicted_first = coeffs[1]
    predicted_last = coeffs[0] * (len(df) - 1) + coeffs[1]

    return TrendResult(
        slope_pct_per_week=round(float(slope), 2),
        total_change_pct=round(float(predicted_last - predicted_first), 1),
        weeks_covered=len(df),
        total_attempts=total_attempts,
        first_week_rate=round(float(df["success_rate_pct"].iloc[0]), 1),
        last_week_rate=round(float(df["success_rate_pct"].iloc[-1]), 1),
    )


@dataclass
class PositionFlag:
    position: str
    attempts_against: int
    finish_rate_pct: float
    overall_avg_finish_rate_pct: float
    delta_pct_points: float
    share_of_all_subs_conceded_pct: float
    notable: bool
    reason: str


def flag_notable_positions(min_sample: int = 5, delta_threshold_pts: float = 15.0) -> list[PositionFlag]:
    """Simple proportion comparison: flag a position if (a) it has at least
    `min_sample` conceded-submission attempts against it, AND (b) its finish
    rate is at least `delta_threshold_pts` percentage points above the
    overall average finish rate across all positions. This is intentionally
    a plain heuristic, not a significance test — with ~5-30 events per
    position a chi-square/z-test would imply more precision than the data
    supports."""
    df = run_sql_file("submission_conceded_by_position.sql")
    if df.empty:
        return []

    overall_finish_rate = 100.0 * df["finished_against"].sum() / df["attempts_against"].sum()

    flags = []
    for _, row in df.iterrows():
        delta = row["finish_rate_pct"] - overall_finish_rate
        notable = row["attempts_against"] >= min_sample and delta >= delta_threshold_pts
        reason = (
            f"finish rate {row['finish_rate_pct']}% vs. {overall_finish_rate:.1f}% average "
            f"(+{delta:.1f} pts), n={int(row['attempts_against'])}"
            if notable else "below sample or delta threshold"
        )
        flags.append(PositionFlag(
            position=row["position"],
            attempts_against=int(row["attempts_against"]),
            finish_rate_pct=float(row["finish_rate_pct"]),
            overall_avg_finish_rate_pct=round(overall_finish_rate, 1),
            delta_pct_points=round(float(delta), 1),
            share_of_all_subs_conceded_pct=float(row["share_of_all_subs_conceded_pct"]),
            notable=notable,
            reason=reason,
        ))
    return sorted(flags, key=lambda f: f.delta_pct_points, reverse=True)


if __name__ == "__main__":
    trend = compute_takedown_trend()
    print("=== Takedown trend ===")
    if trend is None:
        print("Not enough weekly data to compute a trend.")
    else:
        direction = "increased" if trend.total_change_pct >= 0 else "decreased"
        print(
            f"Takedown success rate {direction} {abs(trend.total_change_pct)} percentage points "
            f"over {trend.weeks_covered} weeks (from ~{trend.first_week_rate}% to ~{trend.last_week_rate}%), "
            f"n={trend.total_attempts} attempts total."
        )

    print("\n=== Position flags (submissions conceded) ===")
    for f in flag_notable_positions():
        marker = "[NOTABLE]" if f.notable else "         "
        print(f"{marker} {f.position:14s} n={f.attempts_against:3d}  finish_rate={f.finish_rate_pct:5.1f}%  "
              f"share_of_conceded={f.share_of_all_subs_conceded_pct:5.1f}%  ({f.reason})")
