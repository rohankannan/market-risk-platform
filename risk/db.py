"""Database access for the batch jobs and (later) the API.

All engine code stays pure - this module is the only place batch code touches
SQL. Sessions are passed in, never created at module level.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import subprocess

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from risk_engine.config import DEFAULT_CONFIG

DEFAULT_DB_URL = "postgresql+psycopg://riskdesk:riskdesk@localhost:5432/riskdesk"

# serverless Postgres (Neon) suspends idle computes and closes pooled server
# connections from its side; pre-ping revalidates every checkout and recycle
# bounds connection age below typical idle-timeout windows
POOL_RECYCLE_S = 300


def make_engine(url: str | None = None) -> Engine:
    return create_engine(url or os.getenv("DATABASE_URL", DEFAULT_DB_URL),
                         pool_pre_ping=True, pool_recycle=POOL_RECYCLE_S)


def code_version() -> str:
    if sha := os.getenv("GIT_SHA"):
        return sha[:12]
    try:
        # --dirty matters for provenance: a run or report generated from an
        # uncommitted tree must say so, or its SHA claims code it doesn't have
        out = subprocess.run(["git", "describe", "--always", "--dirty"],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def config_hash() -> str:
    return hashlib.sha256(repr(DEFAULT_CONFIG).encode()).hexdigest()[:12]


# ---------------------------------------------------------------- run lifecycle

def claim_run(conn: Connection, run_date: dt.date, run_type: str, force: bool = False) -> int:
    """Claim (run_date, run_type) under an advisory lock; re-claim FAILED/PARTIAL
    (or anything with force) by deleting the old row - ON DELETE CASCADE clears
    child results atomically. Returns run_id; raises if a healthy run exists."""
    conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                 {"k": f"{run_type}_{run_date}"})
    row = conn.execute(text(
        "SELECT run_id, status FROM risk_runs WHERE run_date=:d AND run_type=:t"),
        {"d": run_date, "t": run_type}).first()
    if row is not None:
        if row.status == "SUCCESS" and not force:
            raise RuntimeError(f"{run_type} run for {run_date} already SUCCESS "
                               "(run_id={}) - use --force to redo".format(row.run_id))
        conn.execute(text("DELETE FROM risk_runs WHERE run_id=:r"), {"r": row.run_id})
    run_id = conn.scalar(text("""
        INSERT INTO risk_runs (run_date, run_type, status, started_at, code_version, config_hash)
        VALUES (:d, :t, 'RUNNING', now(), :cv, :ch) RETURNING run_id"""),
        {"d": run_date, "t": run_type, "cv": code_version(), "ch": config_hash()})
    return int(run_id)


def finish_run(conn: Connection, run_id: int, status: str, error_msg: str | None = None) -> None:
    conn.execute(text("""UPDATE risk_runs SET status=:s, finished_at=now(), error_msg=:e
                         WHERE run_id=:r"""), {"s": status, "e": error_msg, "r": run_id})


# ---------------------------------------------------------------- reads

def read_factor_meta(conn: Connection) -> pd.DataFrame:
    return pd.read_sql(text("""SELECT factor_id, factor_code, factor_type, return_conv,
                                      source, source_symbol, fallback_source, fallback_symbol,
                                      invert_on_ingest, ffill_limit_days
                               FROM risk_factors WHERE is_active"""), conn)


def read_levels(conn: Connection, end: dt.date | None = None) -> pd.DataFrame:
    """Wide levels frame (obs_date x factor_code) from market_data."""
    q = """SELECT rf.factor_code, md.obs_date, md.value
           FROM market_data md JOIN risk_factors rf USING (factor_id)"""
    params: dict = {}
    if end is not None:
        q += " WHERE md.obs_date <= :end"
        params["end"] = end
    long = pd.read_sql(text(q), conn, params=params)
    wide = long.pivot(index="obs_date", columns="factor_code", values="value").sort_index()
    wide.index = pd.to_datetime(wide.index)
    return wide.astype(float)


def read_book(conn: Connection) -> pd.DataFrame:
    """The engine's positions-frame contract, straight from the DB (one factor
    per instrument in MVP - identical to seed.to_positions_frame's output)."""
    return pd.read_sql(text("""
        SELECT i.ticker, d.desk_code, rf.factor_code, p.quantity::float AS quantity,
               i.instrument_type, rf.return_conv,
               (i.meta->>'coupon')::float AS coupon,
               (i.meta->>'maturity_years')::float AS maturity_years
        FROM positions p
        JOIN desks d USING (desk_id)
        JOIN instruments i USING (instrument_id)
        JOIN instrument_factors f USING (instrument_id)
        JOIN risk_factors rf ON rf.factor_id = f.factor_id
        ORDER BY d.desk_code, i.ticker"""), conn)


def desk_id_map(conn: Connection) -> dict[str, int]:
    return dict(conn.execute(text("SELECT desk_code, desk_id FROM desks")).all())


def prev_var(conn: Connection, run_date: dt.date, measure: str) -> dict[str, float]:
    """Most recent 1-day VaR per desk_code strictly before run_date (for the
    exception check). Empty dict on the first ever run."""
    rows = conn.execute(text("""
        WITH prev AS (
          SELECT r.run_id FROM risk_runs r
          WHERE r.run_date < :d AND r.status IN ('SUCCESS','PARTIAL')
          ORDER BY r.run_date DESC LIMIT 1)
        SELECT d.desk_code, rr.value::float
        FROM risk_results rr JOIN prev USING (run_id) JOIN desks d USING (desk_id)
        WHERE rr.measure = :m AND rr.horizon_days = 1"""),
        {"d": run_date, "m": measure}).all()
    return dict(rows)


# ---------------------------------------------------------------- writes

def upsert_market_rows(conn: Connection, rows: list[dict]) -> int:
    """rows: factor_id, obs_date, value, source, is_ffilled, ffill_age."""
    if not rows:
        return 0
    conn.execute(text("""
        INSERT INTO market_data (factor_id, obs_date, value, source, is_ffilled, ffill_age)
        VALUES (:factor_id, :obs_date, :value, :source, :is_ffilled, :ffill_age)
        ON CONFLICT (factor_id, obs_date) DO UPDATE
        SET value = EXCLUDED.value, source = EXCLUDED.source,
            is_ffilled = EXCLUDED.is_ffilled, ffill_age = EXCLUDED.ffill_age"""), rows)
    return len(rows)


def write_dq_issues(conn: Connection, run_id: int, issues: list[dict],
                    factor_ids: dict[str, int]) -> None:
    for i in issues:
        conn.execute(text("""
            INSERT INTO dq_issues (run_id, factor_id, obs_date, check_name, severity, detail)
            VALUES (:r, :f, :o, :c, :s, CAST(:d AS jsonb))"""),
            {"r": run_id, "f": factor_ids.get(i.get("factor_code")), "o": i.get("obs_date"),
             "c": i["check_name"], "s": i["severity"], "d": json.dumps(i.get("detail", {}))})


def write_risk_results(conn: Connection, run_id: int, rows: list[dict]) -> None:
    """rows: desk_id, measure, confidence, horizon_days, value."""
    if not rows:
        return
    conn.execute(text("""
        INSERT INTO risk_results (run_id, desk_id, measure, confidence, horizon_days, value)
        VALUES (:run_id, :desk_id, :measure, :confidence, :horizon_days, :value)
        ON CONFLICT (run_id, desk_id, measure, confidence, horizon_days)
        DO UPDATE SET value = EXCLUDED.value"""),
        [{**r, "run_id": run_id} for r in rows])


def write_exposures(conn: Connection, run_id: int, rows: list[dict]) -> None:
    """rows: desk_id, factor_id, measure, value."""
    if not rows:
        return
    conn.execute(text("""
        INSERT INTO risk_exposures (run_id, desk_id, factor_id, measure, value)
        VALUES (:run_id, :desk_id, :factor_id, :measure, :value)
        ON CONFLICT (run_id, desk_id, factor_id, measure)
        DO UPDATE SET value = EXCLUDED.value"""),
        [{**r, "run_id": run_id} for r in rows])


def write_pnl(conn: Connection, rows: list[dict]) -> None:
    """rows: desk_id, pnl_date, pnl_type, amount."""
    if not rows:
        return
    conn.execute(text("""
        INSERT INTO pnl (desk_id, pnl_date, pnl_type, amount)
        VALUES (:desk_id, :pnl_date, :pnl_type, :amount)
        ON CONFLICT (desk_id, pnl_date, pnl_type) DO UPDATE SET amount = EXCLUDED.amount"""),
        rows)


def write_exceptions(conn: Connection, run_id: int, rows: list[dict]) -> None:
    """rows: desk_id, obs_date, measure, var_value, pnl_value."""
    if not rows:
        return
    conn.execute(text("""
        INSERT INTO backtest_exceptions (desk_id, obs_date, measure, var_value, pnl_value, run_id)
        VALUES (:desk_id, :obs_date, :measure, :var_value, :pnl_value, :run_id)
        ON CONFLICT (desk_id, obs_date, measure) DO UPDATE
        SET var_value = EXCLUDED.var_value, pnl_value = EXCLUDED.pnl_value,
            run_id = EXCLUDED.run_id"""),
        [{**r, "run_id": run_id} for r in rows])


def ensure_scenario_catalog(conn: Connection, replays: dict, hypotheticals: list[dict],
                            factor_ids: dict[str, int]) -> dict[str, int]:
    """Upsert the scenario catalog (2 replays + yaml hypotheticals); returns code->id."""
    for code, (start, end) in replays.items():
        conn.execute(text("""
            INSERT INTO scenarios (scenario_code, scenario_name, scenario_type, window_start, window_end)
            VALUES (:c, :n, 'HISTORICAL_REPLAY', :s, :e)
            ON CONFLICT (scenario_code) DO UPDATE SET window_start=:s, window_end=:e"""),
            {"c": code, "n": code.replace("_", " ").title(), "s": start, "e": end})
    for h in hypotheticals:
        sid = conn.scalar(text("""
            INSERT INTO scenarios (scenario_code, scenario_name, scenario_type, description)
            VALUES (:c, :n, :t, :d)
            ON CONFLICT (scenario_code) DO UPDATE SET scenario_name = EXCLUDED.scenario_name
            RETURNING scenario_id"""),
            {"c": h["code"], "n": h["name"], "t": h.get("type", "HYPOTHETICAL"),
             "d": h.get("description")})
        for fcode, s in h["shocks"].items():
            shock_type = {"RELATIVE": "RELATIVE", "ABSOLUTE_BP": "ABSOLUTE_BP",
                          "ABSOLUTE": "ABSOLUTE"}[s["type"]]
            conn.execute(text("""
                INSERT INTO scenario_shocks (scenario_id, factor_id, shock_type, shock_value)
                VALUES (:sid, :fid, :st, :sv)
                ON CONFLICT (scenario_id, factor_id) DO UPDATE
                SET shock_type = EXCLUDED.shock_type, shock_value = EXCLUDED.shock_value"""),
                {"sid": sid, "fid": factor_ids[fcode], "st": shock_type, "sv": s["value"]})
    return dict(conn.execute(text("SELECT scenario_code, scenario_id FROM scenarios")).all())


def write_scenario_results(conn: Connection, run_id: int, rows: list[dict]) -> None:
    """rows: scenario_id, desk_id, pnl_impact."""
    if not rows:
        return
    conn.execute(text("""
        INSERT INTO scenario_results (run_id, scenario_id, desk_id, pnl_impact)
        VALUES (:run_id, :scenario_id, :desk_id, :pnl_impact)"""),
        [{**r, "run_id": run_id} for r in rows])
