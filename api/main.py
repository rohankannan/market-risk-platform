"""RiskDesk API - FastAPI service over the results tables the EOD batch writes.

Full spec-section-6 surface: /healthz plus /api/v1 meta, risk/summary,
risk/history, risk/movers, risk/exposures, desks/{code}/decomposition,
desks/{code}/positions, backtest/summary, backtest/pla, scenarios,
scenarios/results, modeldoc. Every route carries a response_model (the
committed OpenAPI document feeds the dashboard's generated types). Responses
pinned to an explicit as_of are immutable once the batch completes, so they
ship a long-lived Cache-Control; unpinned responses don't cache.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.engine import Connection

from api import queries, sandbox, schemas
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

# reads plus the what-if sandbox POST: no credentials, no cookies - the
# origin allowlist is all CORS needs
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origins,
                   allow_methods=["GET", "POST"], allow_headers=["*"])


def _cache(response: Response, pinned: bool) -> None:
    response.headers["Cache-Control"] = (
        "public, max-age=31536000, immutable" if pinned else "no-cache")


def _run_or_404(conn: Connection, as_of: dt.date | None,
                require_scenarios: bool = False) -> dict:
    run = queries.resolve_run(conn, as_of=as_of, require_scenarios=require_scenarios)
    if run is None:
        what = "scenario run" if require_scenarios else "completed run"
        raise HTTPException(status_code=404, detail=(
            f"no {what} on or before {as_of.isoformat()}" if as_of
            else f"no {what}s yet"))
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


@app.get("/api/v1/factors/latest", response_model=schemas.FactorsLatest)
def factors_latest(response: Response, as_of: dt.date | None = AsOf,
                   conn: Connection = Depends(get_conn)) -> schemas.FactorsLatest:
    """Latest level and day move per active factor - the dashboard's factor
    tape. Moves are reported in each factor's own convention units."""
    run = _run_or_404(conn, as_of)
    _cache(response, pinned=as_of is not None)
    return schemas.build_factors_latest(
        run, queries.factor_latest_rows(conn, run["run_date"]))


@app.get("/api/v1/risk/movers", response_model=schemas.RiskMovers)
def risk_movers(response: Response, as_of: dt.date | None = AsOf,
                window: int = Query(1, ge=1, le=30,
                                    description="Calendar days back for the delta baseline "
                                                "(drivers always describe the as-of day)"),
                conn: Connection = Depends(get_conn)) -> schemas.RiskMovers:
    """Desk VaR moves vs the run `window` days back, with the as-of day's
    largest factor moves as driver strings - the Overview movers table.
    Empty rows on the first run."""
    run = _run_or_404(conn, as_of)
    _cache(response, pinned=as_of is not None)
    prev = queries.resolve_run(conn, as_of=run["run_date"] - dt.timedelta(days=window))
    return schemas.build_risk_movers(
        run, queries.risk_rows(conn, run["run_id"]),
        queries.risk_rows(conn, prev["run_id"]) if prev else [],
        prev["run_date"] if prev else None,
        queries.desk_factor_codes(conn),
        queries.factor_move_rows(conn, run["run_date"]))


def _desk_or_404(conn: Connection, desk_code: str) -> dict:
    desk = queries.desk_row(conn, desk_code.upper())
    if desk is None or desk["is_aggregate"]:
        raise HTTPException(status_code=404, detail=f"unknown desk {desk_code!r}")
    return desk


@app.get("/api/v1/desks/{desk_code}/decomposition", response_model=schemas.DeskDecomposition)
def desk_decomposition(desk_code: str, response: Response, as_of: dt.date | None = AsOf,
                       conn: Connection = Depends(get_conn)) -> schemas.DeskDecomposition:
    """VaR-decomposition waterfall (factor-class buckets + diversification) and
    the desk's factor exposures. Buckets are empty for runs without the position
    step (backfill runs) - the desk VaR itself is still reported."""
    run = _run_or_404(conn, as_of)
    _cache(response, pinned=as_of is not None)
    desk = _desk_or_404(conn, desk_code)
    return schemas.build_desk_decomposition(
        run, desk, queries.risk_rows(conn, run["run_id"]),
        queries.desk_position_rows(conn, run["run_id"], desk["desk_code"]),
        queries.exposure_rows(conn, run["run_id"]))


@app.get("/api/v1/desks/{desk_code}/positions", response_model=schemas.DeskPositions)
def desk_positions(desk_code: str, response: Response, as_of: dt.date | None = AsOf,
                   conn: Connection = Depends(get_conn)) -> schemas.DeskPositions:
    """Per-position standalone/component/marginal VaR for one desk (no
    pagination - the book is under 100 rows by construction)."""
    run = _run_or_404(conn, as_of)
    _cache(response, pinned=as_of is not None)
    desk = _desk_or_404(conn, desk_code)
    return schemas.build_desk_positions(
        run, desk, queries.desk_position_rows(conn, run["run_id"], desk["desk_code"]))


@app.get("/api/v1/risk/exposures", response_model=schemas.KeyRateExposures)
def risk_exposures(response: Response, as_of: dt.date | None = AsOf,
                   conn: Connection = Depends(get_conn)) -> schemas.KeyRateExposures:
    """Key-rate DV01s per desk from the run's bootstrapped par curve - empty
    rows for runs that skip the curve step (backfill runs do)."""
    run = _run_or_404(conn, as_of)
    _cache(response, pinned=as_of is not None)
    return schemas.build_key_rate_exposures(run, queries.exposure_rows(conn, run["run_id"]))


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


@app.get("/api/v1/backtest/pla", response_model=schemas.PlaSummary)
def backtest_pla(response: Response, scope: str = Scope,
                 window: int = Query(250, ge=20, le=1000, description="Trailing P&L days"),
                 as_of: dt.date | None = AsOf,
                 conn: Connection = Depends(get_conn)) -> schemas.PlaSummary:
    """P&L attribution: Spearman and KS between daily hypothetical and
    risk-theoretical P&L with MAR32-style zones. Shipped with the options
    sleeve, which gives the statistic structural content beyond the linear
    legs' log-linearization."""
    run = _run_or_404(conn, as_of)
    _cache(response, pinned=as_of is not None)
    code = _scope_or_404(conn, scope)
    series = queries.pla_series(conn, code, run["run_date"], window)
    try:
        return schemas.build_pla_summary(code, window, series)
    except (ValueError, IndexError) as exc:
        raise HTTPException(status_code=404,
                            detail=f"insufficient paired P&L for {code}: {exc}") from exc


@app.get("/api/v1/scenarios", response_model=schemas.ScenarioCatalog)
def scenarios_catalog(response: Response,
                      conn: Connection = Depends(get_conn)) -> schemas.ScenarioCatalog:
    """Scenario definitions: replay windows and hypothetical shock lists (the
    dashboard formats dominant-move strings from these)."""
    _cache(response, pinned=False)
    return schemas.build_scenario_catalog(queries.scenario_catalog_rows(conn))


@app.post("/api/v1/whatif", response_model=schemas.WhatIfResult)
def whatif(body: schemas.WhatIfRequest, response: Response,
           as_of: dt.date | None = AsOf,
           conn: Connection = Depends(get_conn)) -> schemas.WhatIfResult:
    """Hypothetical risk for a scaled book - the API's documented exception to
    the reads-only rule. Revalues client-edited positions against the resolved
    run's stored HS scenario set; nothing is persisted, every response says
    hypothetical, and the official numbers ride along for the delta. Identity
    check: all scales at 1.0 reproduces the batch's VaR to the cent."""
    run = _run_or_404(conn, as_of)
    response.headers["Cache-Control"] = "no-store"
    scales: dict[str, float] = {}
    for adj in body.adjustments:
        if adj.ticker in scales:
            raise HTTPException(status_code=422,
                                detail=f"duplicate adjustment for {adj.ticker!r}")
        scales[adj.ticker] = adj.scale
    try:
        computed = sandbox.compute_whatif(
            conn, run["run_id"], run["run_date"], scales,
            [s.model_dump() for s in body.shocks])
    except sandbox.WhatIfInputError as exc:
        # only input faults map to 422; a data-integrity failure stays a 500
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return schemas.build_whatif_result(run, computed,
                                       queries.risk_rows(conn, run["run_id"]))


@app.get("/api/v1/scenarios/{code}/shocks", response_model=schemas.ScenarioShockVector)
def scenario_shocks(code: str, response: Response, as_of: dt.date | None = AsOf,
                    conn: Connection = Depends(get_conn)) -> schemas.ScenarioShockVector:
    """A catalog scenario resolved to an explicit shock vector - what the
    sandbox loads when you start from a preset. Replays resolve against the
    run's history, so the returned moves are what the market delivered over
    the window, not stored magnitudes."""
    run = _run_or_404(conn, as_of)
    _cache(response, pinned=as_of is not None)
    try:
        shocks = sandbox.resolve_scenario_shocks(conn, run["run_id"], run["run_date"], code)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown scenario {code!r}") from exc
    except sandbox.WhatIfInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    stype = conn.execute(text("SELECT scenario_type FROM scenarios WHERE scenario_code = :c"),
                         {"c": code}).scalar()
    return schemas.ScenarioShockVector(scenario_code=code, scenario_type=str(stype),
                                       shocks=[schemas.WhatIfShock(**s) for s in shocks])


@app.get("/api/v1/modeldoc", response_model=schemas.ModelDoc)
def modeldoc(response: Response) -> schemas.ModelDoc:
    """The SR 11-7-structured model document, verbatim markdown."""
    _cache(response, pinned=False)
    settings = get_settings()
    try:
        with open(settings.model_doc_path, encoding="utf-8") as f:
            return schemas.ModelDoc(markdown=f.read())
    except OSError as exc:
        raise HTTPException(status_code=404,
                            detail=f"model doc not found at {settings.model_doc_path}") from exc


@app.get("/api/v1/scenarios/results", response_model=schemas.ScenarioResults)
def scenarios_results(response: Response, as_of: dt.date | None = AsOf,
                      conn: Connection = Depends(get_conn)) -> schemas.ScenarioResults:
    """Scenario catalog with per-desk P&L impacts from the latest run that
    executed scenarios (EOD runs do; backfill runs don't), worst first."""
    run = _run_or_404(conn, as_of, require_scenarios=True)
    _cache(response, pinned=as_of is not None)
    return schemas.build_scenario_results(run, queries.scenario_rows(conn, run["run_id"]))
