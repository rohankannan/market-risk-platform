"""Regulatory backtesting statistics: Kupiec POF, Christoffersen, Basel traffic light.

All hand-rolled (whiteboard rule); scipy supplies only the chi-squared survival
function. Every public function returns a typed result the API serializes directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.stats import chi2


def _xlogy(a: float, b: float) -> float:
    """a * log(b) with the 0*log(0) := 0 convention (the edge case that crashes
    naive implementations when a window has zero exceptions or zero 1->1 transitions)."""
    if a == 0.0:
        return 0.0
    return a * np.log(b)


@dataclass(frozen=True)
class LikelihoodRatioTest:
    name: str
    statistic: float
    p_value: float
    df: int
    reject_5pct: bool
    details: dict = field(default_factory=dict)


def kupiec_pof(n_exceptions: int, n_obs: int, p: float = 0.01) -> LikelihoodRatioTest:
    """Kupiec proportion-of-failures coverage test.

    LR = -2 * [ ln L(p) - ln L(x/n) ] ~ chi2(1) under H0: exception rate = p.
    Known-answer (unit-tested): n=250, x=5, p=0.01 -> LR ~ 1.96, p ~ 0.16.
    """
    x, n = int(n_exceptions), int(n_obs)
    if not 0 <= x <= n:
        raise ValueError(f"n_exceptions={x} out of range for n_obs={n}")
    phat = x / n
    ll_h0 = _xlogy(n - x, 1.0 - p) + _xlogy(x, p)
    ll_h1 = _xlogy(n - x, 1.0 - phat) + _xlogy(x, phat)
    lr = float(-2.0 * (ll_h0 - ll_h1))
    p_value = float(chi2.sf(lr, df=1))
    return LikelihoodRatioTest("kupiec_pof", lr, p_value, 1, p_value < 0.05,
                               {"n_obs": n, "n_exceptions": x, "expected": n * p, "p": p})


def christoffersen_independence(exceptions) -> LikelihoodRatioTest:
    """Christoffersen independence test on the exception indicator sequence.

    Compares a first-order Markov alternative against iid exceptions; catches
    *clustered* violations (a vol-regime miss) that Kupiec's coverage test
    cannot see. LR ~ chi2(1).
    """
    e = np.asarray(exceptions, dtype=bool).astype(int)
    if e.size < 2:
        raise ValueError("need at least 2 observations")
    prev, curr = e[:-1], e[1:]
    n00 = int(np.sum((prev == 0) & (curr == 0)))
    n01 = int(np.sum((prev == 0) & (curr == 1)))
    n10 = int(np.sum((prev == 1) & (curr == 0)))
    n11 = int(np.sum((prev == 1) & (curr == 1)))
    pi01 = n01 / (n00 + n01) if (n00 + n01) else 0.0
    pi11 = n11 / (n10 + n11) if (n10 + n11) else 0.0
    pi = (n01 + n11) / (n00 + n01 + n10 + n11)
    ll_h0 = _xlogy(n00 + n10, 1.0 - pi) + _xlogy(n01 + n11, pi)
    ll_h1 = (_xlogy(n00, 1.0 - pi01) + _xlogy(n01, pi01)
             + _xlogy(n10, 1.0 - pi11) + _xlogy(n11, pi11))
    lr = float(max(-2.0 * (ll_h0 - ll_h1), 0.0))
    p_value = float(chi2.sf(lr, df=1))
    return LikelihoodRatioTest("christoffersen_independence", lr, p_value, 1, p_value < 0.05,
                               {"n00": n00, "n01": n01, "n10": n10, "n11": n11})


def christoffersen_conditional_coverage(exceptions, p: float = 0.01) -> LikelihoodRatioTest:
    """Joint coverage + independence: LR_cc = LR_pof + LR_ind ~ chi2(2)."""
    e = np.asarray(exceptions, dtype=bool)
    pof = kupiec_pof(int(e.sum()), int(e.size), p=p)
    ind = christoffersen_independence(e)
    lr = pof.statistic + ind.statistic
    p_value = float(chi2.sf(lr, df=2))
    return LikelihoodRatioTest("christoffersen_cc", lr, p_value, 2, p_value < 0.05,
                               {"lr_pof": pof.statistic, "lr_ind": ind.statistic})


@dataclass(frozen=True)
class TrafficLightResult:
    zone: str            # GREEN / AMBER / RED
    n_exceptions: int
    n_obs: int
    plus_factor: float   # Basel multiplier add-on k
    multiplier: float    # 3.0 + k


_PLUS_FACTOR = {5: 0.40, 6: 0.50, 7: 0.65, 8: 0.75, 9: 0.85}


def basel_traffic_light(n_exceptions: int, n_obs: int = 250) -> TrafficLightResult:
    """Basel traffic-light zones for 99% 1-day VaR over a 250-day window.

    Green 0-4, amber 5-9 (add-on 0.40..0.85), red >=10 (add-on 1.00).
    The regulatory response is not hypothesis testing: the multiplier penalizes
    from 5 exceptions even when Kupiec cannot reject (p ~ 0.16 at x=5).
    Zone boundaries are defined for n=250; other window lengths are reported
    with the same counts and flagged in `details` by the caller.
    """
    x = int(n_exceptions)
    if x <= 4:
        zone, k = "GREEN", 0.0
    elif x <= 9:
        zone, k = "AMBER", _PLUS_FACTOR[x]
    else:
        zone, k = "RED", 1.00
    return TrafficLightResult(zone, x, int(n_obs), k, 3.0 + k)
