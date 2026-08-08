"""Read-only SQL behind the API routes.

Parameterized forms of the sql/analytics queries where one exists (q1's daily
exception series feeds /backtest/summary, q2's effective-dated limit join feeds
/risk/summary). Everything reads the results tables the batch writes - the API
never computes risk.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import text
from sqlalchemy.engine import Connection


def resolve_run(conn: Connection, as_of: dt.date | None = None,
                require_scenarios: bool = False) -> dict | None:
    """Latest completed (SUCCESS/PARTIAL) run at or before as_of, EOD preferred
    over BACKFILL on the same date. require_scenarios narrows to runs that
    wrote scenario_results (backfill runs don't)."""
    extra = ("AND EXISTS (SELECT 1 FROM scenario_results sr WHERE sr.run_id = r.run_id)"
             if require_scenarios else "")
    row = conn.execute(text(f"""
        SELECT r.run_id, r.run_date, r.run_type, r.status, r.finished_at, r.code_version
        FROM risk_runs r
        WHERE r.status IN ('SUCCESS','PARTIAL')
          AND (CAST(:as_of AS date) IS NULL OR r.run_date <= :as_of) {extra}
        ORDER BY r.run_date DESC, (r.run_type = 'EOD') DESC, r.run_id DESC
        LIMIT 1"""), {"as_of": as_of}).mappings().first()
    return dict(row) if row else None


def available_dates(conn: Connection) -> list[dt.date]:
    return [d for (d,) in conn.execute(text("""
        SELECT DISTINCT run_date FROM risk_runs
        WHERE status IN ('SUCCESS','PARTIAL') ORDER BY run_date"""))]


def desks(conn: Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(text("""
        SELECT desk_code, desk_name, is_aggregate FROM desks
        ORDER BY is_aggregate DESC, desk_code""")).mappings()]


def desk_exists(conn: Connection, desk_code: str) -> bool:
    return conn.scalar(text("SELECT 1 FROM desks WHERE desk_code = :c"),
                       {"c": desk_code}) is not None


def desk_row(conn: Connection, desk_code: str) -> dict | None:
    row = conn.execute(text("""
        SELECT desk_code, desk_name, is_aggregate FROM desks WHERE desk_code = :c"""),
        {"c": desk_code}).mappings().first()
    return dict(row) if row else None


def risk_rows(conn: Connection, run_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(text("""
        SELECT d.desk_code, d.desk_name, d.is_aggregate,
               rr.measure, rr.horizon_days, rr.value::float AS value
        FROM risk_results rr JOIN desks d USING (desk_id)
        WHERE rr.run_id = :r"""), {"r": run_id}).mappings()]


def limits_in_force(conn: Connection, on: dt.date) -> list[dict]:
    return [dict(r) for r in conn.execute(text("""
        SELECT d.desk_code, l.measure, l.limit_value::float AS limit_value,
               l.warn_threshold::float AS warn_threshold
        FROM limits l JOIN desks d USING (desk_id)
        WHERE l.effective_from <= :d
          AND (l.effective_to IS NULL OR :d <= l.effective_to)"""), {"d": on}).mappings()]


def history_risk_rows(conn: Connection, scope: str, end: dt.date, window: int) -> list[dict]:
    """1-day measures for the last `window` run dates <= end, one run per date
    (EOD preferred where an EOD and a BACKFILL run share a date)."""
    return [dict(r) for r in conn.execute(text("""
        WITH runs AS (
          SELECT DISTINCT ON (run_date) run_id, run_date
          FROM risk_runs
          WHERE status IN ('SUCCESS','PARTIAL') AND run_date <= :end
          ORDER BY run_date DESC, (run_type = 'EOD') DESC, run_id DESC
          LIMIT :w)
        SELECT ru.run_date AS obs_date, rr.measure, rr.value::float AS value
        FROM runs ru
        JOIN risk_results rr USING (run_id)
        JOIN desks d ON d.desk_id = rr.desk_id
        WHERE d.desk_code = :scope AND rr.horizon_days = 1
        ORDER BY ru.run_date"""), {"end": end, "w": window, "scope": scope}).mappings()]


def pnl_rows(conn: Connection, scope: str, start: dt.date, end: dt.date) -> list[dict]:
    return [dict(r) for r in conn.execute(text("""
        SELECT p.pnl_date, p.amount::float AS amount
        FROM pnl p JOIN desks d USING (desk_id)
        WHERE d.desk_code = :scope AND p.pnl_type = 'HYPOTHETICAL'
          AND p.pnl_date BETWEEN :start AND :end"""),
        {"scope": scope, "start": start, "end": end}).mappings()]


def exception_rows(conn: Connection, scope: str, start: dt.date, end: dt.date) -> list[dict]:
    return [dict(r) for r in conn.execute(text("""
        SELECT e.obs_date, e.measure
        FROM backtest_exceptions e JOIN desks d USING (desk_id)
        WHERE d.desk_code = :scope AND e.obs_date BETWEEN :start AND :end"""),
        {"scope": scope, "start": start, "end": end}).mappings()]


def backtest_series(conn: Connection, scope: str, measure: str, end: dt.date,
                    window: int) -> list[dict]:
    """Date-ordered P&L rows with the exception join (q1_rolling_traffic_light's
    daily CTE, parameterized to one desk and measure). FIRM has its own pnl
    rows, so aggregates need no special-casing."""
    rows = [dict(r) for r in conn.execute(text("""
        SELECT p.pnl_date, p.amount::float AS pnl,
               e.var_value::float AS var_value, e.pnl_value::float AS pnl_value
        FROM pnl p
        JOIN desks d USING (desk_id)
        LEFT JOIN backtest_exceptions e
          ON e.desk_id = p.desk_id AND e.obs_date = p.pnl_date AND e.measure = :m
        WHERE d.desk_code = :scope AND p.pnl_type = 'HYPOTHETICAL' AND p.pnl_date <= :end
        ORDER BY p.pnl_date DESC
        LIMIT :w"""), {"m": measure, "scope": scope, "end": end, "w": window}).mappings()]
    return rows[::-1]


def exposure_rows(conn: Connection, run_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(text("""
        SELECT d.desk_code, rf.factor_code, re.measure, re.value::float AS value
        FROM risk_exposures re
        JOIN desks d USING (desk_id)
        JOIN risk_factors rf USING (factor_id)
        WHERE re.run_id = :r
        ORDER BY re.measure, d.desk_code, rf.factor_code"""), {"r": run_id}).mappings()]


def desk_factor_codes(conn: Connection) -> list[dict]:
    """Distinct (desk_code, factor_code) over booked positions, every mapped
    sensitivity - an option's vol factor counts as its desk's factor too."""
    return [dict(r) for r in conn.execute(text("""
        SELECT DISTINCT d.desk_code, rf.factor_code
        FROM positions p
        JOIN desks d USING (desk_id)
        JOIN instrument_factors f USING (instrument_id)
        JOIN risk_factors rf USING (factor_id)""")).mappings()]


def factor_move_rows(conn: Connection, end: dt.date, lookback: int = 61) -> list[dict]:
    """Trailing `lookback` observations per factor up to end, date-ordered - the
    movers builder turns levels into day moves and a typical-move normalizer."""
    return [dict(r) for r in conn.execute(text("""
        SELECT factor_code, return_conv, obs_date, value FROM (
          SELECT rf.factor_code, rf.return_conv, md.obs_date, md.value::float AS value,
                 row_number() OVER (PARTITION BY md.factor_id ORDER BY md.obs_date DESC) AS rn
          FROM market_data md JOIN risk_factors rf USING (factor_id)
          WHERE md.obs_date <= :end) t
        WHERE rn <= :lb
        ORDER BY factor_code, obs_date"""), {"end": end, "lb": lookback}).mappings()]


def desk_position_rows(conn: Connection, run_id: int, desk_code: str) -> list[dict]:
    """Per-position component rows for one desk (the decomposition waterfall
    and the positions table). Empty for runs that skip the position step
    (backfill runs do). Quantity and instrument type were frozen with the run;
    instruments joins only for option display metadata - a components row whose
    instrument has vanished (book reseeded since the run) must fail loudly, not
    silently lose its option badge."""
    rows = [dict(r) for r in conn.execute(text("""
        SELECT pc.ticker, pc.factor_class, pc.quantity::float AS quantity,
               pc.instrument_type,
               pc.standalone_var::float AS standalone_var,
               pc.component_es::float AS component_es, pc.marginal_var::float AS marginal_var,
               i.ticker IS NULL AS instrument_missing,
               i.meta->>'option_type' AS option_type,
               (i.meta->>'moneyness')::float AS moneyness,
               (i.meta->>'maturity_years')::float AS maturity_years
        FROM position_components pc
        JOIN desks d USING (desk_id)
        LEFT JOIN instruments i ON i.ticker = pc.ticker
        WHERE pc.run_id = :r AND d.desk_code = :c
        ORDER BY pc.ticker"""), {"r": run_id, "c": desk_code}).mappings()]
    dropped = sorted(r["ticker"] for r in rows if r.pop("instrument_missing"))
    if dropped:
        raise RuntimeError("position_components rows without an instruments row "
                           f"(book reseeded since run {run_id}?): {dropped}")
    return rows


def pla_series(conn: Connection, scope: str, end: dt.date, window: int) -> list[dict]:
    """Date-ordered paired daily P&L: hypothetical vs risk-theoretical - the
    two legs of the P&L-attribution test. Only dates carrying both legs count."""
    rows = [dict(r) for r in conn.execute(text("""
        SELECT p.pnl_date,
               max(p.amount::float) FILTER (WHERE p.pnl_type = 'HYPOTHETICAL') AS hpl,
               max(p.amount::float) FILTER (WHERE p.pnl_type = 'RISK_THEORETICAL') AS rtpl
        FROM pnl p JOIN desks d USING (desk_id)
        WHERE d.desk_code = :scope AND p.pnl_date <= :end
        GROUP BY p.pnl_date
        HAVING count(*) FILTER (WHERE p.pnl_type = 'HYPOTHETICAL') > 0
           AND count(*) FILTER (WHERE p.pnl_type = 'RISK_THEORETICAL') > 0
        ORDER BY p.pnl_date DESC
        LIMIT :w"""), {"scope": scope, "end": end, "w": window}).mappings()]
    return rows[::-1]


def scenario_catalog_rows(conn: Connection) -> list[dict]:
    """One row per (scenario, shock); replays carry no shocks (their moves are
    computed from the replay window, not stored) so their factor columns are NULL."""
    return [dict(r) for r in conn.execute(text("""
        SELECT s.scenario_code, s.scenario_name, s.scenario_type, s.window_start,
               s.window_end, s.description, rf.factor_code, ss.shock_type,
               ss.shock_value::float AS shock_value
        FROM scenarios s
        LEFT JOIN scenario_shocks ss USING (scenario_id)
        LEFT JOIN risk_factors rf USING (factor_id)
        ORDER BY s.scenario_code, rf.factor_code""")).mappings()]


def scenario_rows(conn: Connection, run_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(text("""
        SELECT s.scenario_code, s.scenario_name, s.scenario_type, s.window_start,
               s.window_end, s.description, d.desk_code, d.is_aggregate,
               sr.pnl_impact::float AS pnl_impact
        FROM scenario_results sr
        JOIN scenarios s USING (scenario_id)
        JOIN desks d USING (desk_id)
        WHERE sr.run_id = :r"""), {"r": run_id}).mappings()]
