"""Empirical calibration of hypothetical stress magnitudes.

Every hypothetical shock in the catalog is a measured quantile of this book's
own history rather than a round number. Two rules, and only two:

SENSITIVITY rows - one factor class moved uniformly - take the p99 of
overlapping HORIZON-day moves for the class's representative factor, in that
factor's own return convention. A uniform move across a class is a
deliberate ladder, not a claim about co-movement.

SCENARIO rows - many classes moving together - take the CONDITIONAL TAIL
MEAN: the mean move of every factor over the windows where a named driver sat
at or below its 1st percentile. Co-movement then comes from the data instead
of from one magnitude reused across factors.

Overlapping windows are used deliberately: they keep every observed episode in
the sample at the cost of correlated draws, which is the right trade for
locating a tail magnitude (the RNIV note on overlapping k-day shocks makes the
same trade). The quantile is reported, not the return period.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# 20 business days ~ one month: long enough to contain an episode, short
# enough that the book's positions would plausibly still be on
HORIZON_DAYS = 20
SEVERITY_Q = 0.99          # sensitivity magnitudes
DRIVER_TAIL_Q = 0.01       # conditional-tail-mean selector


@dataclass(frozen=True)
class Calibration:
    horizon_days: int
    severity_q: float
    driver_tail_q: float
    n_windows: int
    start: str
    end: str


def horizon_moves(returns: pd.DataFrame, horizon: int = HORIZON_DAYS) -> pd.DataFrame:
    """Overlapping horizon-day moves, each factor in its own convention.

    Returns are additive in every convention this platform uses (log returns
    for LOG factors, basis points for ABS_BP, points for ABS), so a k-day move
    is the rolling sum - no compounding step, no unit mixing.
    """
    if horizon < 1:
        raise ValueError(f"horizon must be >= 1, got {horizon}")
    return returns.rolling(horizon).sum().dropna()


def severity(returns: pd.DataFrame, factor: str, *, horizon: int = HORIZON_DAYS,
             q: float = SEVERITY_Q) -> float:
    """Magnitude of the q-quantile |horizon-day move| for one factor.

    Unsigned: the catalog decides which direction hurts this book.
    """
    moves = horizon_moves(returns[[factor]], horizon)[factor]
    return float(np.quantile(moves.abs().to_numpy(), q))


def quantile_of(returns: pd.DataFrame, factor: str, magnitude: float, *,
                horizon: int = HORIZON_DAYS) -> tuple[float, int]:
    """Where a chosen magnitude sits in one factor's |horizon-day| move
    distribution: (quantile, count of windows at or beyond it). This is what
    turns a round supervisory number into a documented severity."""
    moves = horizon_moves(returns[[factor]], horizon)[factor].abs()
    m = abs(magnitude)
    return float((moves <= m).mean()), int((moves >= m).sum())


def conditional_tail_mean(returns: pd.DataFrame, driver: str, *,
                          horizon: int = HORIZON_DAYS,
                          tail_q: float = DRIVER_TAIL_Q) -> pd.Series:
    """Mean move of every factor over the windows where `driver` sat at or
    below its tail quantile - the co-movement the data actually delivered
    when the driver sold off, signs included."""
    moves = horizon_moves(returns, horizon)
    if driver not in moves.columns:
        raise ValueError(f"driver {driver!r} is not a calibrated factor")
    cut = float(np.quantile(moves[driver].to_numpy(), tail_q))
    tail = moves[moves[driver] <= cut]
    if tail.empty:
        raise ValueError(f"no windows at or below the {tail_q:.0%} quantile of {driver}")
    return tail.mean()


def calibration_meta(returns: pd.DataFrame, *, horizon: int = HORIZON_DAYS) -> Calibration:
    moves = horizon_moves(returns, horizon)
    return Calibration(horizon_days=horizon, severity_q=SEVERITY_Q,
                       driver_tail_q=DRIVER_TAIL_Q, n_windows=len(moves),
                       start=str(moves.index[0].date()), end=str(moves.index[-1].date()))
