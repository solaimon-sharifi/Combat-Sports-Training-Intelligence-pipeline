"""
Generate a small set of plain-language insight strings from the SQL +
trend-analysis layers below. Every insight states the sample size (N)
behind it — a percentage with no N is not an insight, it's a guess.

Usage:
    python analysis/insights.py
"""
from sql_runner import run_sql_file
from trend_analysis import compute_takedown_trend, flag_notable_positions


def takedown_trend_insight() -> str | None:
    trend = compute_takedown_trend()
    if trend is None:
        return None
    direction = "increased" if trend.total_change_pct >= 0 else "decreased"
    return (
        f"Takedown success rate {direction} by {abs(trend.total_change_pct)} percentage points "
        f"over {trend.weeks_covered} weeks (~{trend.first_week_rate}% -> ~{trend.last_week_rate}%), "
        f"based on {trend.total_attempts} takedown attempts."
    )


def position_weakness_insights(max_items: int = 2) -> list[str]:
    flags = [f for f in flag_notable_positions() if f.notable]
    out = []
    for f in flags[:max_items]:
        out.append(
            f"Submissions conceded from {f.position.replace('_', ' ')} are finished at a "
            f"{f.finish_rate_pct}% rate, {f.delta_pct_points:+.1f} points above the "
            f"{f.overall_avg_finish_rate_pct}% average across all positions (n={f.attempts_against})."
        )
    return out


def position_share_insight() -> str | None:
    df = run_sql_file("submission_conceded_by_position.sql")
    if df.empty:
        return None
    df = df.sort_values("finished_against", ascending=False)
    top = df.iloc[0]
    total_finished = int(df["finished_against"].sum())
    if total_finished == 0:
        return None
    return (
        f"{top['position'].replace('_', ' ').title()} accounts for "
        f"{top['share_of_all_subs_conceded_pct']}% of submissions conceded "
        f"({int(top['finished_against'])} of {total_finished} total)."
    )


def belt_performance_insight(min_attempts: int = 8) -> str | None:
    """Restricted to belt buckets with at least `min_attempts` submission
    attempts — a thinner bucket (e.g. n=5 black belts) can swing 20+ points
    on a single roll and would misrepresent the belt-rank trend."""
    df = run_sql_file("performance_by_belt.sql")
    df = df[df["submission_attempts_for"] >= min_attempts]
    if len(df) < 2:
        return None
    df = df.sort_values("submission_success_rate_pct")
    worst = df.iloc[0]
    best = df.iloc[-1]
    if worst["belt_bucket"] == best["belt_bucket"]:
        return None
    return (
        f"Submission success rate against {best['belt_bucket']} belts is {best['submission_success_rate_pct']}% "
        f"(n={int(best['submission_attempts_for'])} attempts) vs. {worst['submission_success_rate_pct']}% "
        f"against {worst['belt_bucket']} belts (n={int(worst['submission_attempts_for'])} attempts)."
    )


def session_volume_insight() -> str | None:
    df = run_sql_file("session_volume.sql")
    if df.empty:
        return None
    total_sessions = int(df["session_count"].sum())
    total_weeks = len(df)
    avg_per_week = round(total_sessions / total_weeks, 1)
    return (
        f"Averaged {avg_per_week} sessions/week over {total_weeks} weeks "
        f"({total_sessions} sessions total)."
    )


def generate_all_insights() -> list[str]:
    insights = []
    for fn in (takedown_trend_insight, position_share_insight, belt_performance_insight, session_volume_insight):
        result = fn()
        if result:
            insights.append(result)
    insights.extend(position_weakness_insights())
    return insights


if __name__ == "__main__":
    print("=== Training Intelligence: Insights ===\n")
    for i, insight in enumerate(generate_all_insights(), 1):
        print(f"{i}. {insight}")
