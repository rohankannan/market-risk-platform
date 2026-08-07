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


def apply_scenario(positions: pd.DataFrame, levels: pd.Series, shock: pd.Series) -> pd.DataFrame:
    """Instantaneous full-reval P&L under a single shock vector, per desk and total."""
    raise NotImplementedError("milestone 3: reuses engine.revalue with a 1-row shock matrix")
