"""Out-of-sample VaR backfill: re-run the engine as-of each historical business day.

For every date t in the window:
  - scenarios = trailing `lookback` return vectors ending at t (HS), or the same
    vectors devol/revol-rescaled to the vol forecast for t+1 (FHS);
  - VaR/ES computed on the STATIC book at date-t factor levels;
  - hypothetical P&L for t+1 = full reval of the frozen book under the actual
    t -> t+1 factor moves (the clean-P&L convention of regulatory backtesting);
  - exception if HPL_{t+1} < -VaR_t.

This is what makes October's backtest chart possible: without a daily
out-of-sample VaR history there is nothing to Kupiec-test.
"""

from __future__ import annotations

import pandas as pd

from .config import DEFAULT_CONFIG, RiskConfig
from .engine import aggregate, revalue
from .factors import build_scenarios_fhs, build_scenarios_hs
from .var import ewma_vol_forecast_series, ewma_volatility, var_es_from_pnl


def run_backfill(book: pd.DataFrame, levels: pd.DataFrame, returns: pd.DataFrame,
                 n_days: int = 750, cfg: RiskConfig = DEFAULT_CONFIG,
                 methods: tuple[str, ...] = ("HS", "FHS"),
                 vol_models: dict[str, tuple[pd.DataFrame, pd.DataFrame]] | None = None
                 ) -> pd.DataFrame:
    """Tidy frame: one row per (as_of, method, scope) with var, es, hpl_next, is_exception.

    `levels`/`returns` must already be aligned (align_levels + to_returns).
    The last date in the window has no next-day P&L; its hpl_next is NaN.
    Every method except "HS" is a filtered run driven by (conditional vols,
    forecast series) frames: the default "FHS" pair is the champion EWMA,
    computed here; challengers supply theirs via `vol_models` keyed by method
    label (how the parallel-run harness injects GARCH).
    """
    vol_models = dict(vol_models or {})
    if "FHS" in methods and "FHS" not in vol_models:
        vol_models["FHS"] = (
            ewma_volatility(returns, lam=cfg.lambda_ewma, seed_window=cfg.ewma_seed_window),
            ewma_vol_forecast_series(returns, lam=cfg.lambda_ewma,
                                     seed_window=cfg.ewma_seed_window))
    unknown = set(methods) - {"HS"} - set(vol_models)
    if unknown:
        raise ValueError(f"filtered methods without vol_models frames: {sorted(unknown)}")

    dates = returns.index[-(n_days + 1):]          # +1 so the last as-of still gets an HPL
    if len(returns.loc[:dates[0]]) < cfg.lookback_days:
        raise ValueError(f"need {cfg.lookback_days} returns before {dates[0].date()}; "
                         "shorten n_days or extend history")

    rows: list[dict] = []
    for i, t in enumerate(dates[:-1]):
        lvl_t = levels.loc[t]
        nxt = dates[i + 1]
        day_move = returns.loc[[nxt]]
        hpl = aggregate(revalue(book, lvl_t, day_move), book).iloc[0]
        # risk-theoretical P&L: the same realized move through the linearized
        # path - the PLA test's other leg
        rtpl = aggregate(revalue(book, lvl_t, day_move, mode="delta_gamma"), book).iloc[0]

        for method in methods:
            if method == "HS":
                scen = build_scenarios_hs(returns, t, cfg.lookback_days)
            else:
                vols, fc = vol_models[method]
                scen = build_scenarios_fhs(returns, vols, fc.loc[t], t, cfg.lookback_days)
            desk = aggregate(revalue(book, lvl_t, scen), book)
            for scope in desk.columns:          # booked desks + FIRM
                r = var_es_from_pnl(desk[scope], cfg.alpha_var, cfg.alpha_es, method=method)
                rows.append({
                    "as_of": t, "method": method, "scope": scope,
                    "var": r.var, "es": r.es,
                    "hpl_next": float(hpl[scope]),
                    "rtpl_next": float(rtpl[scope]),
                    "is_exception": bool(hpl[scope] < -r.var),
                })
    return pd.DataFrame(rows)
