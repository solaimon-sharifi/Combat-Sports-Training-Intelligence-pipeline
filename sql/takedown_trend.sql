-- Rolling weekly takedown success rate (successful / attempted), for-direction only.
-- "Attempted" = successful + failed (defended does not apply to takedowns in this schema).
-- Sample size (attempts) is returned alongside the rate so a 100% week on 1 attempt
-- doesn't get read the same as a 60% week on 20 attempts.

SELECT
    strftime('%Y-W%W', s.date) AS iso_week,
    MIN(s.date) AS week_start,
    COUNT(*) AS attempts,
    SUM(CASE WHEN e.outcome = 'successful' THEN 1 ELSE 0 END) AS successes,
    ROUND(
        100.0 * SUM(CASE WHEN e.outcome = 'successful' THEN 1 ELSE 0 END) / COUNT(*),
        1
    ) AS success_rate_pct
FROM exchanges e
JOIN sessions s ON s.session_id = e.session_id
JOIN exchange_types et ON et.exchange_type_id = e.exchange_type_id
WHERE et.name = 'takedown'
  AND e.direction = 'for'
GROUP BY iso_week
ORDER BY week_start;
