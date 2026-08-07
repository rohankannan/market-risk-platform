"""Portfolio revaluation and aggregation - milestone 2 (Aug 24 - Sep 14).

The typed surface is fixed now so the batch job and API can be written against
it; implementations land with the position/factor plumbing.
"""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd


def revalue(positions: pd.DataFrame, levels: pd.Series, shocks: pd.DataFrame,
            mode: Literal["full", "delta_gamma"] = "full") -> pd.DataFrame:
    """Scenario P&L matrix (n_scenarios x n_positions).

    `positions`: instrument_id, desk, instrument_type, factor_id, quantity, meta.
    `levels`: current factor levels. `shocks`: scenario shock matrix in each
    factor's return convention. mode="full" is exact revaluation (pricing.py);
    mode="delta_gamma" is the sensitivity-based path used for risk-theoretical
    P&L in the PLA test.
    """
    raise NotImplementedError("milestone 2: wire pricing.py to the position/factor mapping")


def aggregate(pnl_matrix: pd.DataFrame, positions: pd.DataFrame) -> pd.DataFrame:
    """Desk-level and firm-level scenario P&L from the position-level matrix."""
    raise NotImplementedError("milestone 2")


def component_es(desk_pnl: pd.DataFrame, alpha_es: float = 0.975) -> pd.Series:
    """Euler allocation of firm ES: mean of each desk's P&L over the firm's tail scenarios.

    Sums exactly to firm ES; can be negative for hedging desks. Implemented now
    because it is pure arithmetic on a desk-level scenario P&L frame.
    """
    firm = desk_pnl.sum(axis=1)
    q = np.quantile(firm.to_numpy(dtype=float), 1.0 - alpha_es, method="linear")
    tail_idx = firm[firm <= q].index
    return -desk_pnl.loc[tail_idx].mean()
