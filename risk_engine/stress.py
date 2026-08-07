"""Stress module - milestone 3 (Sep 15-28).

Historical replays are DATA-DRIVEN: only the window dates are hardcoded; the
shock vector is computed from stored history (sum of log returns / total bp
move), with sanity assertions in tests (e.g. GFC window: SPX log ~ -0.51,
UST 2Y ~ -115bp, JPY safe-haven ~ +12%).
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

REPLAY_WINDOWS: dict[str, tuple[dt.date, dt.date]] = {
    "GFC_2008": (dt.date(2008, 9, 12), dt.date(2008, 11, 20)),
    "COVID_2020": (dt.date(2020, 2, 19), dt.date(2020, 3, 23)),
}


def compute_replay_shock(returns: pd.DataFrame, start: dt.date, end: dt.date) -> pd.Series:
    """Cumulative factor move over [start, end]: sum of daily returns per factor
    (log returns for LOG factors, bp for ABS_BP - additivity holds for both)."""
    window = returns.loc[pd.Timestamp(start): pd.Timestamp(end)]
    if window.empty:
        raise ValueError(f"no return data in replay window {start}..{end}")
    return window.sum()


def apply_scenario(positions: pd.DataFrame, levels: pd.Series, shock: pd.Series) -> pd.Series:
    """Instantaneous full-reval P&L under one shock vector, per desk plus FIRM.

    Factors absent from `shock` are shocked by zero (the documented fill rule);
    shock values are in each factor's own return convention.
    """
    from .engine import aggregate, revalue

    full = pd.Series(0.0, index=levels.index)
    full[shock.index] = shock.astype(float)
    one_row = pd.DataFrame([full], index=[0])
    return aggregate(revalue(positions, levels, one_row), positions).iloc[0]
