-- Rolling 250-day Basel traffic-light zone per desk (window frame + CASE).
WITH daily AS (
  SELECT p.desk_id, d.desk_code, p.pnl_date,
         (e.obs_date IS NOT NULL)::int AS is_exception
  FROM pnl p
  JOIN desks d USING (desk_id)
  LEFT JOIN backtest_exceptions e
    ON e.desk_id = p.desk_id AND e.obs_date = p.pnl_date AND e.measure = 'VAR_HS'
  WHERE p.pnl_type = 'HYPOTHETICAL' AND NOT d.is_aggregate
), rolled AS (
  SELECT desk_code, pnl_date,
         SUM(is_exception) OVER (PARTITION BY desk_id ORDER BY pnl_date
                                 ROWS BETWEEN 249 PRECEDING AND CURRENT ROW) AS exc_250d,
         COUNT(*)          OVER (PARTITION BY desk_id ORDER BY pnl_date
                                 ROWS BETWEEN 249 PRECEDING AND CURRENT ROW) AS window_n
  FROM daily
)
SELECT desk_code, pnl_date, exc_250d,
       CASE WHEN exc_250d <= 4 THEN 'GREEN'
            WHEN exc_250d <= 9 THEN 'AMBER'
            ELSE 'RED' END AS basel_zone
FROM rolled
WHERE window_n = 250
ORDER BY desk_code, pnl_date;
