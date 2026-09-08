-- Session frequency/volume over time: sessions and total mat time per ISO week.
-- Basis for a consistency view — irregular training shows up as gaps here
-- even if the overall averages look fine.

SELECT
    strftime('%Y-W%W', date) AS iso_week,
    MIN(date) AS week_start,
    COUNT(*) AS session_count,
    SUM(duration_min) AS total_minutes,
    ROUND(AVG(duration_min), 1) AS avg_duration_min,
    SUM(rounds) AS total_rounds
FROM sessions
GROUP BY iso_week
ORDER BY week_start;
