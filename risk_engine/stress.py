"""Stress module - milestone 3 (Sep 15-28).

Historical replays are DATA-DRIVEN: only the window dates are hardcoded; the
shock vector is computed from stored history (sum of log returns / total bp
move), with anchors pinned against the snapshot in tests/test_stress.py
(GFC window: SPX log ~ -0.50, UST 2Y ~ -118bp, JPY safe-haven ~ +12%).
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

# Replays carry no chosen magnitudes: the window is the input and the shock is
# whatever the market delivered. Both crises are flight-to-quality episodes
# where a long-duration book GAINS on the rates leg, which is exactly why the
# 2022 window is here - the correlated stock-bond selloff (10Y +258bp with SPY
# -17.7% over the window) is this book's genuinely adverse regime, and a
# catalog of only 2008 and 2020 would never show it.
REPLAY_WINDOWS: dict[str, tuple[dt.date, dt.date]] = {
    "GFC_2008": (dt.date(2008, 9, 12), dt.date(2008, 11, 20)),
    "COVID_2020": (dt.date(2020, 2, 19), dt.date(2020, 3, 23)),
    "RATES_2022": (dt.date(2022, 1, 3), dt.date(2022, 10, 31)),
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
