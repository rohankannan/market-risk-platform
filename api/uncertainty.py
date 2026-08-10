"""Sampling uncertainty on the served risk numbers, computed at read time.

Nothing here is a new fact about a run - the bracketing ranks depend only on
(n, p, confidence), and the power numbers depend only on the window length - so
persisting per-run rows would store the same properties hundreds of times over.
Instead the run's scenario P&L is rebuilt on request through the same engine the
batch used (the sandbox's run_market path, which the what-if identity check
proves reproduces the official VaR to the cent), the statistics are computed
seeded, and the result is cached per run_id: a pinned as_of serves the same
bytes every time.
"""

from __future__ import annotations

import datetime as dt
import threading

import numpy as np
import pandas as pd
from sqlalchemy.engine import Connection

from risk import db
from risk_engine.config import DEFAULT_CONFIG as CFG
from risk_engine.engine import FIRM_SCOPE, aggregate, revalue
from risk_engine.inference import bootstrap_rows, quantile_interval, quantile_se

from . import queries
from .sandbox import run_market

_CACHE: dict[int, dict] = {}
_CACHE_MAX = 4
_LOCK = threading.Lock()


def _var(pnl: np.ndarray) -> float:
    return float(-np.quantile(pnl, 1.0 - CFG.alpha_var, method="linear"))


def _es(pnl: np.ndarray) -> float:
    q = np.quantile(pnl, 1.0 - CFG.alpha_es, method="linear")
    return float(-pnl[pnl <= q].mean())


def uncertainty_report(desk_pnl: pd.DataFrame) -> dict:
    """Interval and standard-error measurements for one scenario P&L set.

    Pure: desk columns plus the FIRM total in, plain dicts out. The exact
    order-statistic interval leads; the asymptotic and bootstrap errors ride
    along as the assumption-bearing checks (the bootstrap percentile interval
    under-covers a tail quantile, which test_inference asserts - so it is
    reported as a spread, never as the headline interval).
    """
    p = round(1.0 - CFG.alpha_var, 10)
    desk_cols = [c for c in desk_pnl.columns if c != FIRM_SCOPE]
    scopes = [FIRM_SCOPE, *sorted(desk_cols)]
    out_scopes = []
    for scope in scopes:
        arr = desk_pnl[scope].to_numpy(dtype=float)
        interval = quantile_interval(arr, p, CFG.interval_confidence)
        var_boot = bootstrap_rows(arr, _var, n_boot=CFG.n_bootstrap, seed=CFG.seed,
                                  confidence=CFG.interval_confidence,
                                  block_length=CFG.bootstrap_block_days)
        es_boot = bootstrap_rows(arr, _es, n_boot=CFG.n_bootstrap, seed=CFG.seed,
                                 confidence=CFG.interval_confidence,
                                 block_length=CFG.bootstrap_block_days)
        out_scopes.append({
            "desk_code": scope,
            "is_aggregate": scope == FIRM_SCOPE,
            "var_hs_1d": interval.estimate,
            "ci_low": interval.low,
            "ci_high": interval.high,
            "rank_low": interval.rank_low,
            "rank_high": interval.rank_high,
            "coverage": interval.coverage,
            "se_asymptotic": quantile_se(arr, p),
            "se_bootstrap": var_boot.se,
            "es_975_1d": es_boot.estimate,
            "es_se_bootstrap": es_boot.se,
        })

    matrix = desk_pnl[desk_cols].to_numpy(dtype=float)

    def _div(rows: np.ndarray) -> float:
        standalone = sum(_var(rows[:, i]) for i in range(rows.shape[1]))
        return 1.0 - _var(rows.sum(axis=1)) / standalone

    div = bootstrap_rows(matrix, _div, n_boot=CFG.n_bootstrap, seed=CFG.seed,
                         confidence=CFG.interval_confidence,
                         block_length=CFG.bootstrap_block_days)
    return {
        "n_scenarios": int(len(desk_pnl)),
        "confidence": CFG.interval_confidence,
        "n_boot": CFG.n_bootstrap,
        "block_days": CFG.bootstrap_block_days,
        "seed": CFG.seed,
        "diversification": {"estimate": div.estimate, "low": div.low,
                            "high": div.high, "se": div.se},
        "desks": out_scopes,
    }


def compute_uncertainty(conn: Connection, run_id: int, run_date: dt.date) -> dict:
    """The report for one resolved run, cached per run_id (a --force restatement
    re-claims under a new run_id, so a stale entry can never serve)."""
    with _LOCK:
        if run_id in _CACHE:
            _CACHE[run_id] = _CACHE.pop(run_id)      # LRU touch, not FIFO
            return _CACHE[run_id]
    market = run_market(conn, run_id, run_date)
    book = db.read_book(conn)
    desk_pnl = aggregate(revalue(book, market.levels, market.scenarios), book)
    report = uncertainty_report(desk_pnl)
    # loud reconciliation, against the number the batch actually PUBLISHED: the
    # stored firm VAR_HS row, to the cent it was stored at. A recompute-vs-
    # recompute check would pass forever while both drifted from the record.
    firm = report["desks"][0]
    stored = next((r["value"] for r in queries.risk_rows(conn, run_id)
                   if r["is_aggregate"] and r["measure"] == "VAR_HS"
                   and r["horizon_days"] == 1), None)
    if stored is None:
        raise RuntimeError(f"run {run_id} has no stored firm VAR_HS to reconcile against")
    if abs(round(firm["var_hs_1d"], 2) - round(float(stored), 2)) > 0.01:
        raise RuntimeError(
            f"uncertainty path diverged from the batch: recomputed "
            f"{firm['var_hs_1d']:.2f} vs stored {float(stored):.2f}")
    with _LOCK:
        if run_id not in _CACHE and len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))           # evict only when growing
        _CACHE[run_id] = report
    return report
