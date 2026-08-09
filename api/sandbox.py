"""What-if book revaluation - the API's one documented exception to the
reads-only rule.

The nightly batch remains the sole writer of official numbers. This module
computes HYPOTHETICAL risk for a client-edited book: it rebuilds the resolved
run's HS scenario set from stored market data (cached per run - levels are
immutable once a run resolves), scales the booked positions, and runs the same
engine the batch runs. Nothing is persisted; every response is labeled
what-if; official rows come back alongside so the delta is explicit.
"""

from __future__ import annotations

import datetime as dt
import threading
from dataclasses import dataclass

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Connection

from risk import db
from risk_engine.config import DEFAULT_CONFIG as CFG
from risk_engine.engine import aggregate, position_components, revalue
from risk_engine.factors import align_levels, build_scenarios_hs, to_returns
from risk_engine.stress import REPLAY_WINDOWS, apply_scenario, compute_replay_shock
from risk_engine.var import var_es_from_pnl

MAX_SCALE = 10.0  # |scale| beyond this is a fat finger, not a hedge

# A shock past these is a unit error, not a stress. The worst 20-day moves in
# the committed snapshot are 190bp, 0.69 in log terms and 69 vol points, so
# each bound clears the observed extreme by roughly an order of magnitude -
# wide enough to price any defensible hypothetical, narrow enough that a
# convention mistake (bp posted as a log return) cannot pass.
SHOCK_BOUNDS = {"ABSOLUTE_BP": 2000.0, "RELATIVE": 1.5, "ABSOLUTE": 150.0}
CONV_OF_TYPE = {"RELATIVE": "LOG", "ABSOLUTE_BP": "ABS_BP", "ABSOLUTE": "ABS"}


class WhatIfInputError(ValueError):
    """Client-fault input problems - the route maps only these to 422."""


@dataclass(frozen=True)
class RunMarket:
    """Everything a sandbox request needs from one resolved run's market data."""

    levels: pd.Series          # today's factor levels
    scenarios: pd.DataFrame    # the run's HS scenario set
    returns: pd.DataFrame      # full aligned history, for replay resolution
    convs: dict[str, str]      # factor_code -> return convention


# per-run cache keyed by run_id: a same-date --force restatement re-claims
# under a NEW run_id (claim_run deletes and re-inserts), so stale levels can
# never serve. Lock because uvicorn runs sync routes in a threadpool.
_SCEN_CACHE: dict[int, RunMarket] = {}
_SCEN_CACHE_MAX = 4
_SCEN_LOCK = threading.Lock()


def _scenario_set(conn: Connection, run_id: int, run_date: dt.date) -> RunMarket:
    with _SCEN_LOCK:
        if run_id in _SCEN_CACHE:
            return _SCEN_CACHE[run_id]
    meta = db.read_factor_meta(conn)
    convs = dict(zip(meta["factor_code"], meta["return_conv"]))
    limits = dict(zip(meta["factor_code"], meta["ffill_limit_days"]))
    levels, _ = align_levels(db.read_levels(conn, end=run_date), limits)
    returns = to_returns(levels, convs).dropna()
    levels = levels.loc[returns.index]
    ts_run = pd.Timestamp(run_date)
    if ts_run not in returns.index:
        raise RuntimeError(f"no aligned market data for {run_date}")
    market = RunMarket(levels=levels.loc[ts_run],
                       scenarios=build_scenarios_hs(returns, ts_run, CFG.lookback_days),
                       returns=returns, convs=convs)
    with _SCEN_LOCK:
        if len(_SCEN_CACHE) >= _SCEN_CACHE_MAX:
            _SCEN_CACHE.pop(next(iter(_SCEN_CACHE)))
        _SCEN_CACHE[run_id] = market
    return market


def resolve_scenario_shocks(conn: Connection, run_id: int, run_date: dt.date,
                            code: str) -> list[dict]:
    """A catalog scenario as an explicit shock vector the sandbox can load and
    edit. Replays resolve from history (the window is the input, the shock is
    whatever the market delivered); hypotheticals come from the stored
    catalog rows."""
    row = conn.execute(text("""
        SELECT scenario_id, scenario_type FROM scenarios WHERE scenario_code = :c"""),
        {"c": code}).mappings().first()
    if row is None:
        raise KeyError(code)
    market = _scenario_set(conn, run_id, run_date)
    if row["scenario_type"] == "HISTORICAL_REPLAY":
        if code not in REPLAY_WINDOWS:
            raise WhatIfInputError(f"{code} has no replay window in the engine")
        start, end = REPLAY_WINDOWS[code]
        shock = compute_replay_shock(market.returns, start, end)
        items = shock.items()
    else:
        items = ((r["factor_code"], r["shock_value"]) for r in conn.execute(text("""
            SELECT rf.factor_code, ss.shock_value::float AS shock_value
            FROM scenario_shocks ss JOIN risk_factors rf USING (factor_id)
            WHERE ss.scenario_id = :s ORDER BY rf.factor_code"""),
            {"s": row["scenario_id"]}).mappings())
    # full precision, deliberately: a preset posted back unedited must
    # reproduce the batch's scenario P&L to the cent, and a replay's shock is a
    # sum of daily returns where 6dp already costs dollars on a $22M leg. The
    # editor rounds for display and keeps this value until the user edits it.
    conv_type = {v: k for k, v in CONV_OF_TYPE.items()}
    return [{"factor_code": f, "shock_type": conv_type[market.convs[f]], "value": float(v)}
            for f, v in items if f in market.convs]


def _validate_shocks(shocks: list[dict], convs: dict[str, str]) -> pd.Series:
    """Shocks arrive in each factor's own convention; a mislabeled type would
    apply basis points as a log return. Validate before anything is priced."""
    seen: dict[str, float] = {}
    for s in shocks:
        factor, stype, value = s["factor_code"], s["shock_type"], float(s["value"])
        if factor not in convs:
            raise WhatIfInputError(f"unknown factor {factor!r}")
        if factor in seen:
            raise WhatIfInputError(f"duplicate shock for {factor}")
        want = {v: k for k, v in CONV_OF_TYPE.items()}[convs[factor]]
        if stype != want:
            raise WhatIfInputError(
                f"{factor} takes {want} (its convention is {convs[factor]}), got {stype}")
        if abs(value) > SHOCK_BOUNDS[stype]:
            raise WhatIfInputError(
                f"{factor} shock {value:g} beyond the {SHOCK_BOUNDS[stype]:g} bound "
                f"for {stype} - check the units")
        seen[factor] = value
    return pd.Series(seen, dtype=float)


def compute_whatif(conn: Connection, run_id: int, run_date: dt.date,
                   scales: dict[str, float], shocks: list[dict] | None = None) -> dict:
    """Scaled-book risk on the resolved run's HS scenario set, plus the
    instantaneous P&L of an optional shock vector on that same book.

    scales: ticker -> multiplier on the booked quantity (0 removes, negative
    flips). shocks: factor moves in each factor's own convention. Unknown
    tickers, out-of-range scales and mislabeled shocks raise WhatIfInputError -
    a sandbox must be loud about inputs it ignores.

    The two answers are deliberately kept apart. VaR/ES is the risk of the
    EDITED BOOK at today's levels; shock P&L is a full revaluation of that book
    under the shock. What this does NOT report is "VaR of the shocked book":
    re-marking levels while reusing the unshocked return vectors makes equity
    VaR fall as the mark falls, which is an artifact, not a risk measure.
    """
    book = db.read_book(conn)
    booked = set(book["ticker"])
    unknown = sorted(set(scales) - booked)
    if unknown:
        raise WhatIfInputError(f"tickers not in the book: {unknown}")
    bad = {t: s for t, s in scales.items() if not (-MAX_SCALE <= s <= MAX_SCALE)}
    if bad:
        raise WhatIfInputError(f"scales outside +-{MAX_SCALE:g}: {bad}")

    scaled = book.copy()
    factor = scaled["ticker"].map(lambda t: scales.get(t, 1.0))
    scaled["quantity"] = scaled["quantity"] * factor
    live = scaled[scaled["quantity"] != 0.0].reset_index(drop=True)
    if live.empty:
        raise WhatIfInputError("every position scaled to zero - nothing to revalue")

    market = _scenario_set(conn, run_id, run_date)
    shock_vector = _validate_shocks(shocks or [], market.convs)
    pos_pnl = revalue(live, market.levels, market.scenarios)
    desk_pnl = aggregate(pos_pnl, live)

    # instantaneous full reval of the same edited book under the shock
    shock_pnl: dict[str, float] = {}
    if len(shock_vector):
        pnl = apply_scenario(live, market.levels, shock_vector)
        shock_pnl = {scope: round(float(pnl[scope]), 2) for scope in pnl.index}

    desks = []
    for scope in desk_pnl.columns:
        r = var_es_from_pnl(desk_pnl[scope], CFG.alpha_var, CFG.alpha_es)
        desks.append({"desk_code": scope, "is_aggregate": scope == "FIRM",
                      "var_hs_1d": round(r.var, 2), "es_975_1d": round(r.es, 2),
                      "shock_pnl": shock_pnl.get(scope)})

    comp = position_components(live, pos_pnl, CFG.alpha_var, CFG.alpha_es)
    positions = [{"ticker": r.ticker, "desk_code": r.desk_code,
                  "factor_class": r.factor_class, "quantity": round(r.quantity, 4),
                  "scale": scales.get(r.ticker, 1.0),
                  "standalone_var": round(r.standalone_var, 2),
                  "component_es": round(r.component_es, 2),
                  "marginal_var": round(r.marginal_var, 2)}
                 for r in comp.itertuples(index=False)]

    zeroed = sorted(t for t, s in scales.items() if s == 0.0)
    return {"desks": desks, "positions": positions, "zeroed": zeroed,
            "shocked_factors": sorted(shock_vector.index)}
