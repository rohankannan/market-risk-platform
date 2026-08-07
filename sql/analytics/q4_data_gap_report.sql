-- Data-gap report, gaps-and-islands (generate_series calendar + row_number trick).
WITH cal AS (
  SELECT gs::date AS d
  FROM generate_series(date '2021-08-01', CURRENT_DATE, interval '1 day') gs
  WHERE EXTRACT(isodow FROM gs) < 6
), missing AS (
  SELECT rf.factor_id, rf.factor_code, c.d,
         c.d - (ROW_NUMBER() OVER (PARTITION BY rf.factor_id ORDER BY c.d))::int AS grp
  FROM risk_factors rf CROSS JOIN cal c
  LEFT JOIN market_data m ON m.factor_id = rf.factor_id AND m.obs_date = c.d
  WHERE rf.is_active AND m.factor_id IS NULL
)
SELECT factor_code, MIN(d) AS gap_start, MAX(d) AS gap_end, COUNT(*) AS gap_days
FROM missing
GROUP BY factor_code, grp
HAVING COUNT(*) > 3          -- ignore ordinary holiday singletons
ORDER BY gap_days DESC, factor_code;
