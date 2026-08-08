"""P&L attribution test - the FRTB-style desk-level RTPL-vs-HPL comparison.

Two metrics on the daily series: Spearman rank correlation (does the risk
model's P&L co-move with the official one?) and the two-sample
Kolmogorov-Smirnov statistic (do the two P&Ls have the same distribution?).
They fail differently by design: a risk model that tracks direction but
systematically understates tails passes Spearman and fails KS. Zones follow
the MAR32.41 thresholds. On a purely linear book the test is nearly
degenerate - the only HPL-RTPL gap is the log-linearization of the linear
legs - so it ships with the options sleeve, which gives the statistic
structural content (gamma, vega, the excluded cross terms) to see.

Hand-rolled KS (a sorted-merge sup of ECDF gaps); scipy supplies spearmanr,
an explicitly permitted import.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import spearmanr

SPEARMAN_AMBER = 0.80        # below: amber
SPEARMAN_RED = 0.70          # below: red
KS_AMBER = 0.09              # above: amber
KS_RED = 0.12                # above: red
MIN_OBS = 20                 # rank/ECDF statistics are noise below this


@dataclass(frozen=True)
class PlaResult:
    spearman: float
    ks: float
    zone: str                # GREEN / AMBER / RED
    n_obs: int


def ks_statistic(a, b) -> float:
    """Two-sample KS: sup over x of |ECDF_a(x) - ECDF_b(x)|."""
    a = np.sort(np.asarray(a, dtype=float))
    b = np.sort(np.asarray(b, dtype=float))
    grid = np.concatenate([a, b])
    fa = np.searchsorted(a, grid, side="right") / a.size
    fb = np.searchsorted(b, grid, side="right") / b.size
    return float(np.max(np.abs(fa - fb)))


def pla_zone(rho: float, ks: float) -> str:
    if rho < SPEARMAN_RED or ks > KS_RED:
        return "RED"
    if rho < SPEARMAN_AMBER or ks > KS_AMBER:
        return "AMBER"
    return "GREEN"


def pla_test(hpl, rtpl) -> PlaResult:
    hpl = np.asarray(hpl, dtype=float)
    rtpl = np.asarray(rtpl, dtype=float)
    if hpl.size != rtpl.size:
        raise ValueError(f"series lengths differ: {hpl.size} vs {rtpl.size}")
    if hpl.size < MIN_OBS:
        raise ValueError(f"need >= {MIN_OBS} paired observations, got {hpl.size}")
    rho = float(spearmanr(hpl, rtpl).statistic)
    ks = ks_statistic(hpl, rtpl)
    return PlaResult(spearman=rho, ks=ks, zone=pla_zone(rho, ks), n_obs=int(hpl.size))
