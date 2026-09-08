"""
Quick exploratory plots (matplotlib/seaborn) for sanity-checking the data
and trends before building the Power BI dashboard. Not the final
deliverable visuals — those live in Power BI — this is the "does the
synthetic data actually look right" check.

Usage:
    python analysis/exploratory_plots.py
"""
from pathlib import Path

import matplotlib.pyplot as plt
import seaborn as sns

from sql_runner import run_sql_file

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "exports" / "plots"

sns.set_theme(style="whitegrid")


def plot_takedown_trend():
    df = run_sql_file("takedown_trend.sql")
    fig, ax = plt.subplots(figsize=(8, 4.5))
    sns.lineplot(data=df, x="week_start", y="success_rate_pct", marker="o", ax=ax)
    ax.set_title("Takedown Success Rate by Week")
    ax.set_ylabel("Success rate (%)")
    ax.set_xlabel("Week")
    plt.xticks(rotation=45, ha="right")
    fig.tight_layout()
    fig.savefig(OUT_DIR / "takedown_trend.png", dpi=150)
    plt.close(fig)


def plot_submissions_by_position():
    df = run_sql_file("submission_conceded_by_position.sql")
    df = df.sort_values("finished_against", ascending=False)
    fig, ax = plt.subplots(figsize=(8, 4.5))
    sns.barplot(data=df, x="position", y="finished_against", ax=ax, color="#c0392b")
    ax.set_title("Submissions Conceded by Position (count)")
    ax.set_ylabel("Submissions conceded")
    ax.set_xlabel("Position")
    plt.xticks(rotation=30, ha="right")
    fig.tight_layout()
    fig.savefig(OUT_DIR / "submissions_by_position.png", dpi=150)
    plt.close(fig)


def plot_performance_by_belt():
    df = run_sql_file("performance_by_belt.sql")
    fig, ax = plt.subplots(figsize=(8, 4.5))
    x = range(len(df))
    width = 0.35
    ax.bar([i - width / 2 for i in x], df["submission_success_rate_pct"], width, label="Submission success %")
    ax.bar([i + width / 2 for i in x], df["takedown_success_rate_pct"], width, label="Takedown success %")
    ax.set_xticks(list(x))
    ax.set_xticklabels(df["belt_bucket"])
    ax.set_title("Performance vs. Opponent Belt Rank")
    ax.set_ylabel("Success rate (%)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(OUT_DIR / "performance_by_belt.png", dpi=150)
    plt.close(fig)


def plot_session_volume():
    df = run_sql_file("session_volume.sql")
    fig, ax = plt.subplots(figsize=(8, 4.5))
    sns.barplot(data=df, x="week_start", y="session_count", ax=ax, color="#2980b9")
    ax.set_title("Sessions per Week")
    ax.set_ylabel("Sessions")
    ax.set_xlabel("Week")
    plt.xticks(rotation=45, ha="right")
    fig.tight_layout()
    fig.savefig(OUT_DIR / "session_volume.png", dpi=150)
    plt.close(fig)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    plot_takedown_trend()
    plot_submissions_by_position()
    plot_performance_by_belt()
    plot_session_volume()
    print(f"Saved 4 plots to {OUT_DIR}/")


if __name__ == "__main__":
    main()
