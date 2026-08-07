"""Factor-level transforms: return conventions, alignment, and scenario construction.

Return conventions are a data-model fact (risk_factors.return_conv), not an
if-statement buried in engine code:
  LOG    - log price relatives (equities, FX)
  ABS_BP - day-over-day change in basis points (yields stored in percent;
           log returns explode when yields sit near zero, as in 2020)
  ABS    - plain difference (vol indices, in points)
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping

import numpy as np
import pandas as pd

LOG = "LOG"
ABS_BP = "ABS_BP"
ABS = "ABS"

_CONVENTIONS = (LOG, ABS_BP, ABS)

AsOf = dt.date | pd.Timestamp | str


def align_levels(levels: pd.DataFrame, ffill_limits: Mapping[str, int]
                 ) -> tuple[pd.DataFrame, pd.Series]:
    """Forward-fill each factor up to its own cap (data-quality policy).

    Filled days produce zero returns downstream - a mild, measured vol damping,
    never a hidden one: the second return value is the per-factor count of
    filled cells, which the batch job records as a data-quality metric.
    Gaps beyond the cap remain NaN (callers drop or BLOCK them loudly).
    """
    out = {}
    fill_counts = {}
    for col in levels.columns:
        limit = int(ffill_limits.get(col, 3))
        filled = levels[col].ffill(limit=limit)
        fill_counts[col] = int((filled.notna() & levels[col].isna()).sum())
        out[col] = filled
    return pd.DataFrame(out, index=levels.index), pd.Series(fill_counts, name="ffilled_cells")


def to_returns(levels: pd.DataFrame, conventions: Mapping[str, str]) -> pd.DataFrame:
    """Per-factor daily returns using each factor's convention.

    `levels` columns are factor codes; `conventions` maps factor code -> convention.
    The first row (all-NaN after differencing) is dropped.
    """
    out: dict[str, pd.Series] = {}
    for col in levels.columns:
        conv = conventions[col]
        s = levels[col].astype(float)
        if conv == LOG:
            out[col] = np.log(s).diff()
        elif conv == ABS_BP:
            out[col] = s.diff() * 100.0  # percent -> basis points
        elif conv == ABS:
            out[col] = s.diff()
        else:
            raise ValueError(f"unknown return convention {conv!r} for {col!r}; expected one of {_CONVENTIONS}")
    return pd.DataFrame(out).dropna(how="all")


def build_scenarios_hs(returns: pd.DataFrame, as_of: AsOf, lookback: int = 500) -> pd.DataFrame:
    """Historical-simulation scenario set: the trailing `lookback` daily return vectors."""
    window = returns.loc[: pd.Timestamp(as_of)].tail(lookback)
    if len(window) < lookback:
        raise ValueError(f"only {len(window)} return observations available before {as_of}; need {lookback}")
    return window


def build_scenarios_fhs(
    returns: pd.DataFrame,
    vols: pd.DataFrame,
    vol_forecast: pd.Series,
    as_of: AsOf,
    lookback: int = 500,
) -> pd.DataFrame:
    """Filtered historical simulation (devol/revol).

    z_s = r_s / sigma_s, then r~_s = z_s * sigma_forecast, per factor.
    Cross-factor correlation survives because scenarios are joint historical
    z-vectors; only the vol scale is updated. `vols` is the per-factor
    conditional vol on each historical date (see var.ewma_volatility);
    `vol_forecast` is the next-day vol per factor.
    """
    window = build_scenarios_hs(returns, as_of, lookback)
    v = vols.loc[window.index, window.columns]
    if (v <= 0).any().any():
        raise ValueError("non-positive volatility in FHS devolatilization window")
    z = window / v
    return z * vol_forecast[window.columns]
