-- Worst rolling 250-day window of firm hypothetical P&L (stressed-window search in SQL).
WITH firm_pnl AS (
  SELECT p.pnl_date, SUM(p.amount) AS amt
  FROM pnl p JOIN desks d USING (desk_id)
  WHERE p.pnl_type = 'HYPOTHETICAL' AND NOT d.is_aggregate
  GROUP BY p.pnl_date
), rolled AS (
  SELECT pnl_date AS window_end,
         SUM(amt)   OVER w AS cum_pnl_250d,
         MIN(amt)   OVER w AS worst_day,
         COUNT(*)   OVER w AS n,
         FIRST_VALUE(pnl_date) OVER w AS window_start
  FROM firm_pnl
  WINDOW w AS (ORDER BY pnl_date ROWS BETWEEN 249 PRECEDING AND CURRENT ROW)
)
SELECT window_start, window_end, cum_pnl_250d, worst_day
FROM rolled WHERE n = 250
ORDER BY cum_pnl_250d ASC LIMIT 1;
