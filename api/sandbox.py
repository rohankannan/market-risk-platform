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

import pandas as pd
from sqlalchemy.engine import Connection

from risk import db
from risk_engine.config import DEFAULT_CONFIG as CFG
from risk_engine.engine import aggregate, position_components, revalue
from risk_engine.factors import align_levels, build_scenarios_hs, to_returns
from risk_engine.var import var_es_from_pnl

MAX_SCALE = 10.0  # |scale| beyond this is a fat finger, not a hedge


class WhatIfInputError(ValueError):
    """Client-fault input problems - the route maps only these to 422."""


# per-run scenario cache keyed by run_id: a same-date --force restatement
# re-claims under a NEW run_id (claim_run deletes and re-inserts), so stale
# levels can never serve. Lock because uvicorn runs sync routes in a threadpool.
_SCEN_CACHE: dict[int, tuple[pd.Series, pd.DataFrame]] = {}
_SCEN_CACHE_MAX = 4
_SCEN_LOCK = threading.Lock()


def _scenario_set(conn: Connection, run_id: int,
                  run_date: dt.date) -> tuple[pd.Series, pd.DataFrame]:
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
    lvl_t = levels.loc[ts_run]
    scen = build_scenarios_hs(returns, ts_run, CFG.lookback_days)
    with _SCEN_LOCK:
        if len(_SCEN_CACHE) >= _SCEN_CACHE_MAX:
            _SCEN_CACHE.pop(next(iter(_SCEN_CACHE)))
        _SCEN_CACHE[run_id] = (lvl_t, scen)
    return lvl_t, scen


def compute_whatif(conn: Connection, run_id: int, run_date: dt.date,
                   scales: dict[str, float]) -> dict:
    """Scaled-book risk on the resolved run's HS scenario set.

    scales: ticker -> multiplier on the booked quantity (0 removes, negative
    flips). Unknown tickers and out-of-range scales raise WhatIfInputError -
    a sandbox must be loud about inputs it ignores. Returns desk+firm VaR/ES
    and per-position rows, all round(2) like the batch writes.
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

    lvl_t, scen = _scenario_set(conn, run_id, run_date)
    pos_pnl = revalue(live, lvl_t, scen)
    desk_pnl = aggregate(pos_pnl, live)

    desks = []
    for scope in desk_pnl.columns:
        r = var_es_from_pnl(desk_pnl[scope], CFG.alpha_var, CFG.alpha_es)
        desks.append({"desk_code": scope, "is_aggregate": scope == "FIRM",
                      "var_hs_1d": round(r.var, 2), "es_975_1d": round(r.es, 2)})

    comp = position_components(live, pos_pnl, CFG.alpha_var, CFG.alpha_es)
    positions = [{"ticker": r.ticker, "desk_code": r.desk_code,
                  "factor_class": r.factor_class, "quantity": round(r.quantity, 4),
                  "scale": scales.get(r.ticker, 1.0),
                  "standalone_var": round(r.standalone_var, 2),
                  "component_es": round(r.component_es, 2),
                  "marginal_var": round(r.marginal_var, 2)}
                 for r in comp.itertuples(index=False)]

    zeroed = sorted(t for t, s in scales.items() if s == 0.0)
    return {"desks": desks, "positions": positions, "zeroed": zeroed}
