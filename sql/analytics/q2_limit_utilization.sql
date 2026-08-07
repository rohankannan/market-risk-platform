-- Current limit utilization with breach ranking (CTE + effective-dated join + RANK).
WITH latest_run AS (
  SELECT run_id, run_date FROM risk_runs
  WHERE run_type = 'EOD' AND status IN ('SUCCESS','PARTIAL')
  ORDER BY run_date DESC LIMIT 1
)
SELECT d.desk_code, r.measure, r.value AS risk_value, l.limit_value,
       ROUND(r.value / l.limit_value, 4) AS utilization,
       CASE WHEN r.value >  l.limit_value                    THEN 'BREACH'
            WHEN r.value >= l.warn_threshold * l.limit_value THEN 'WARN'
            ELSE 'OK' END AS limit_status,
       RANK() OVER (ORDER BY r.value / l.limit_value DESC) AS pressure_rank
FROM risk_results r
JOIN latest_run lr USING (run_id)
JOIN desks  d USING (desk_id)
JOIN limits l ON l.desk_id = r.desk_id AND l.measure = r.measure
             AND lr.run_date >= l.effective_from
             AND (l.effective_to IS NULL OR lr.run_date <= l.effective_to)
WHERE r.horizon_days = 1          -- limits are 1-day; the 10d rows would read as phantom breaches
ORDER BY pressure_rank;
