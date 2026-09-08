-- Submissions landed and takedowns successful, segmented by opponent belt rank.
-- Belt rank is bucketed to the whole-belt level (stripes collapsed) so each
-- bucket has enough sample size to be meaningful; the exact decimal scale
-- (white=0 ... black=4, + 0.1-0.4 per stripe) lives in opponents.belt_rank.

WITH belted AS (
    SELECT
        e.*,
        CASE
            WHEN o.belt_rank < 1 THEN 'white'
            WHEN o.belt_rank < 2 THEN 'blue'
            WHEN o.belt_rank < 3 THEN 'purple'
            WHEN o.belt_rank < 4 THEN 'brown'
            ELSE 'black'
        END AS belt_bucket
    FROM exchanges e
    JOIN opponents o ON o.opponent_id = e.opponent_id
    JOIN exchange_types et ON et.exchange_type_id = e.exchange_type_id
    WHERE et.name IN ('submission', 'takedown')
)
SELECT
    belt_bucket,
    SUM(CASE WHEN exchange_type_id = (SELECT exchange_type_id FROM exchange_types WHERE name = 'submission')
              AND direction = 'for' THEN 1 ELSE 0 END) AS submission_attempts_for,
    SUM(CASE WHEN exchange_type_id = (SELECT exchange_type_id FROM exchange_types WHERE name = 'submission')
              AND direction = 'for' AND outcome = 'successful' THEN 1 ELSE 0 END) AS submissions_landed,
    SUM(CASE WHEN exchange_type_id = (SELECT exchange_type_id FROM exchange_types WHERE name = 'takedown')
              AND direction = 'for' THEN 1 ELSE 0 END) AS takedown_attempts_for,
    SUM(CASE WHEN exchange_type_id = (SELECT exchange_type_id FROM exchange_types WHERE name = 'takedown')
              AND direction = 'for' AND outcome = 'successful' THEN 1 ELSE 0 END) AS takedowns_successful,
    ROUND(100.0 * SUM(CASE WHEN exchange_type_id = (SELECT exchange_type_id FROM exchange_types WHERE name = 'submission')
              AND direction = 'for' AND outcome = 'successful' THEN 1 ELSE 0 END)
        / NULLIF(SUM(CASE WHEN exchange_type_id = (SELECT exchange_type_id FROM exchange_types WHERE name = 'submission')
              AND direction = 'for' THEN 1 ELSE 0 END), 0), 1) AS submission_success_rate_pct,
    ROUND(100.0 * SUM(CASE WHEN exchange_type_id = (SELECT exchange_type_id FROM exchange_types WHERE name = 'takedown')
              AND direction = 'for' AND outcome = 'successful' THEN 1 ELSE 0 END)
        / NULLIF(SUM(CASE WHEN exchange_type_id = (SELECT exchange_type_id FROM exchange_types WHERE name = 'takedown')
              AND direction = 'for' THEN 1 ELSE 0 END), 0), 1) AS takedown_success_rate_pct
FROM belted
GROUP BY belt_bucket
ORDER BY
    CASE belt_bucket
        WHEN 'white' THEN 0 WHEN 'blue' THEN 1 WHEN 'purple' THEN 2
        WHEN 'brown' THEN 3 WHEN 'black' THEN 4
    END;
