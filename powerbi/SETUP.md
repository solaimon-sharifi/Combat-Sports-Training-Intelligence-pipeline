# Power BI Dashboard Setup

Power BI Desktop isn't available in this build environment, so the dashboard
itself (`dashboard.pbix`) has to be assembled locally. This doc is the build
recipe — follow it in Power BI Desktop on Windows.

## 1. Get the data in

Run the export script first so the CSVs are fresh:

```bash
python scripts/export_for_powerbi.py
```

This writes to `exports/`:

| File | Grain | Use |
|---|---|---|
| `takedown_trend.csv` | one row per ISO week | trend line |
| `submission_conceded_by_position.csv` | one row per position | bar chart |
| `performance_by_belt.csv` | one row per belt bucket | belt breakdown |
| `session_volume.csv` | one row per ISO week | volume/consistency view |
| `sessions_flat.csv` | one row per session | ad-hoc slicing |
| `exchanges_flat.csv` | one row per exchange, joined to lookups | ad-hoc slicing |

In Power BI Desktop: **Get Data > Text/CSV**, import all six. (If you'd
rather connect live instead of re-exporting each time, use **Get Data >
ODBC** with the SQLite ODBC driver pointed at `db/training.db` — the CSV
route is simpler and is what this project assumes.)

## 2. Relationships

The pre-aggregated CSVs (`takedown_trend`, `submission_conceded_by_position`,
`performance_by_belt`, `session_volume`) are self-contained — no
relationships needed, they already match the SQL query grain 1:1 with each
visual below.

If you also load `sessions_flat` / `exchanges_flat` for ad-hoc exploration,
relate them on `session_id`.

## 3. Build 4-6 visuals on one page

1. **Takedown Success Rate Over Time** — line chart. `week_start` (X) vs.
   `success_rate_pct` (Y), from `takedown_trend`. Add `attempts` as a
   tooltip field so a viewer can see the N behind any point before reading
   too much into a spike.
2. **Submissions Conceded by Position** — bar chart. `position` (axis) vs.
   `finished_against` (value), from `submission_conceded_by_position`, sorted
   descending. Add `share_of_all_subs_conceded_pct` as a data label.
3. **Performance by Opponent Belt Rank** — clustered bar chart. `belt_bucket`
   (axis) vs. `submission_success_rate_pct` and `takedown_success_rate_pct`
   (values), from `performance_by_belt`. Order the axis white → black
   manually (Power BI will otherwise sort alphabetically).
4. **Session Volume/Consistency** — column chart. `week_start` (X) vs.
   `session_count` (Y), from `session_volume`. Optionally add a line for
   `total_minutes` on a secondary axis.
5. *(optional, 5th visual)* — a card/KPI row: total sessions, total
   exchanges, date range covered.
6. *(optional, 6th visual)* — slicer on `belt_bucket` or discipline if you
   loaded `exchanges_flat`, to let a viewer filter the whole page.

Keep it to one page. Title the page **"Combat Sports Training Intelligence"**
and add a text box noting the data is synthetic until real sessions are
logged (see `README.md`).

## 4. Formatting notes

- Use a consistent color for "for" vs "against" if you break any visual out
  by direction (green/red or blue/red both read fine).
- Every visual that shows a rate should also show, or make available via
  tooltip, the raw count it's built on — this mirrors the SQL/Python layer's
  rule that no percentage appears without its N.
- Save as `powerbi/dashboard.pbix` alongside this file once built.
