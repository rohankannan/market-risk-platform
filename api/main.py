"""RiskDesk API - FastAPI service over the results tables the EOD batch writes.

MVP surface (spec section 6; the full 11-endpoint contract lands with the React
dashboard): /healthz plus /api/v1 meta, risk/summary, risk/history,
backtest/summary, scenarios/results. Every route carries a response_model.
Responses pinned to an explicit as_of are immutable once the batch completes,
so they ship a long-lived Cache-Control; unpinned responses don't cache.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from sqlalchemy import text
from sqlalchemy.engine import Connection

from api import queries, schemas
from api.deps import get_conn, get_settings
from risk.db import make_engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.engine = make_engine(get_settings().database_url)
    yield
    app.state.engine.dispose()


app = FastAPI(
    title="RiskDesk API",
    version="0.1.0",
    description="EOD market-risk platform for a mock three-desk book. "
                "VaR/ES reported as positive potential loss, USD; P&L signed.",
    lifespan=lifespan,
)


def _cache(response: Response, pinned: bool) -> None:
    response.headers["Cache-Control"] = (
        "public, max-age=31536000, immutable" if pinned else "no-cache")


def _run_or_404(conn: Connection, as_of: dt.date | None, **kw) -> dict:
    run = queries.resolve_run(conn, as_of=as_of, **kw)
    if run is None:
        raise HTTPException(status_code=404, detail=(
            f"no completed run on or before {as_of.isoformat()}" if as_of
            else "no completed runs yet"))
    return run


def _scope_or_404(conn: Connection, scope: str) -> str:
    code = scope.removeprefix("desk:").upper()
    if not queries.desk_exists(conn, code):
        raise HTTPException(status_code=404, detail=f"unknown scope {scope!r}")
    return code


AsOf = Query(None, description="Pin to a run date (default: latest completed batch)")
Scope = Query("FIRM", description="'FIRM', a desk code, or 'desk:CODE'")


@app.get("/healthz", response_model=schemas.Healthz)
def healthz(request: Request) -> schemas.Healthz:
    """Liveness plus a database round-trip."""
    try:
        with request.app.state.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503,
                            detail=f"database unreachable: {type(exc).__name__}") from exc
    return schemas.Healthz(status="ok", database="ok")


@app.get("/api/v1/meta", response_model=schemas.Meta)
def meta(response: Response, conn: Connection = Depends(get_conn)) -> schemas.Meta:
    """Bootstrap payload: latest completed batch, run-date catalog, desks."""
    _cache(response, pinned=False)
    run = queries.resolve_run(conn)
    return schemas.Meta(
        latest_as_of=run["run_date"] if run else None,
        batch_status=run["status"] if run else "not_yet_run",
        batch_type=run["run_type"] if run else None,
        batch_completed_at=run["finished_at"] if run else None,
        code_version=run["code_version"] if run else None,
        available_dates=queries.available_dates(conn),
        desks=[schemas.Desk(**d) for d in queries.desks(conn)],
    )


@app.get("/api/v1/risk/summary", response_model=schemas.RiskSummary)
def risk_summary(response: Response, as_of: dt.date | None = AsOf,
                 conn: Connection = Depends(get_conn)) -> schemas.RiskSummary:
    """Firm and per-desk VaR/ES with limits, utilization, day-over-day moves
    and the diversification benefit - the dashboard tiles in one call."""
    run = _run_or_404(conn, as_of)
    _cache(response, pinned=as_of is not None)
    prev = queries.resolve_run(conn, as_of=run["run_date"] - dt.timedelta(days=1))
    return schemas.build_risk_summary(
        run, queries.risk_rows(conn, run["run_id"]),
        queries.limits_in_force(conn, run["run_date"]),
        queries.risk_rows(conn, prev["run_id"]) if prev else [])


@app.get("/api/v1/risk/history", response_model=schemas.RiskHistory)
def risk_history(response: Response, scope: str = Scope,
                 window: int = Query(90, ge=1, le=1000, description="Trailing run dates"),
                 as_of: dt.date | None = AsOf,
                 conn: Connection = Depends(get_conn)) -> schemas.RiskHistory:
    """1-day VaR/ES, P&L and exception flags per date for one scope - the
    timeline and sparkline feed."""
    run = _run_or_404(conn, as_of)
    _cache(response, pinned=as_of is not None)
    code = _scope_or_404(conn, scope)
    rows = queries.history_risk_rows(conn, code, run["run_date"], window)
    if not rows:
        raise HTTPException(status_code=404, detail=f"no risk history for {code}")
    start, end = rows[0]["obs_date"], rows[-1]["obs_date"]
    return schemas.build_history(code, rows, queries.pnl_rows(conn, code, start, end),
                                 queries.exception_rows(conn, code, start, end))


@app.get("/api/v1/backtest/summary", response_model=schemas.BacktestSummary)
def backtest_summary(response: Response, scope: str = Scope,
                     model: Literal["HS", "FHS"] = Query("HS"),
                     window: int = Query(250, ge=2, le=1000, description="Trailing P&L days"),
                     as_of: dt.date | None = AsOf,
                     conn: Connection = Depends(get_conn)) -> schemas.BacktestSummary:
    """Kupiec, Christoffersen and the Basel traffic light over the trailing
    exception series (observed P&L vs the prior day's VaR)."""
    run = _run_or_404(conn, as_of)
    _cache(response, pinned=as_of is not None)
    code = _scope_or_404(conn, scope)
    series = queries.backtest_series(conn, code, f"VAR_{model}", run["run_date"], window)
    if len(series) < 2:
        raise HTTPException(status_code=404,
                            detail=f"insufficient P&L history for {code} (need >= 2 days)")
    return schemas.build_backtest_summary(code, model, series, window)


@app.get("/api/v1/scenarios/results", response_model=schemas.ScenarioResults)
def scenarios_results(response: Response, as_of: dt.date | None = AsOf,
                      conn: Connection = Depends(get_conn)) -> schemas.ScenarioResults:
    """Scenario catalog with per-desk P&L impacts from the latest run that
    executed scenarios (EOD runs do; backfill runs don't), worst first."""
    run = _run_or_404(conn, as_of, require_scenarios=True)
    _cache(response, pinned=as_of is not None)
    return schemas.build_scenario_results(run, queries.scenario_rows(conn, run["run_id"]))
