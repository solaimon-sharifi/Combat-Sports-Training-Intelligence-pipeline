-- Submission-conceded rate by position, with raw counts alongside the
-- percentage so a small-sample position isn't mistaken for a real trend.
-- "Conceded" = direction = 'against' on a submission-type exchange.
-- share_of_all_conceded_pct answers "which position bleeds the most subs";
-- outcome_successful_pct (successful/attempted-from-that-position) answers
-- "how dangerous is it once we're actually there."

WITH conceded AS (
    SELECT
        p.name AS position,
        e.outcome
    FROM exchanges e
    JOIN exchange_types et ON et.exchange_type_id = e.exchange_type_id
    JOIN positions p ON p.position_id = e.position_id
    WHERE et.name = 'submission'
      AND e.direction = 'against'
),
totals AS (
    SELECT COUNT(*) AS total_conceded FROM conceded
)
SELECT
    c.position,
    COUNT(*) AS attempts_against,
    SUM(CASE WHEN c.outcome = 'successful' THEN 1 ELSE 0 END) AS finished_against,
    ROUND(100.0 * SUM(CASE WHEN c.outcome = 'successful' THEN 1 ELSE 0 END) / COUNT(*), 1) AS finish_rate_pct,
    ROUND(100.0 * SUM(CASE WHEN c.outcome = 'successful' THEN 1 ELSE 0 END) / t.total_conceded, 1) AS share_of_all_subs_conceded_pct
FROM conceded c
CROSS JOIN totals t
GROUP BY c.position
ORDER BY finished_against DESC;
