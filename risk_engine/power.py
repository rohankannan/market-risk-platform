"""Power and realized size of the backtests this engine already runs.

A backtest that cannot reject is not evidence of a good model, and the tests in
backtest.py are quoted daily without any statement of what they are capable of
detecting. This module answers that, for the implementations as shipped rather
than for their textbook idealizations - it calls kupiec_pof and
christoffersen_independence directly, so any quirk of those functions shows up in
the curve instead of being smoothed away.

Kupiec needs no simulation. Its statistic is a function of the integer exception
count alone, so the rejection region is a finite set of counts, found by
enumeration, and power is the binomial mass that region carries under the
alternative. Exact, and it makes the discreteness visible: realized size is not
the nominal 5% and does not approach it monotonically in n, because adding an
observation shuffles which counts happen to fall inside the region.

Christoffersen's independence statistic depends on the transition counts, not on
the total, so it is simulated from a two-state Markov chain. Every path takes a
seed.

The headline for this repository: at the published 750-day window, Kupiec's power
against an exception rate 20% above nominal - 1.2% against the 1% target - is on
the order of its own size. Keep the units straight, because they change the
story's shape: that alternative is a RATE error, and under normality it
corresponds to a VaR understated by only about 3% in dollars, so the errors the
test cannot see are the day-to-day-sized ones. A 20% dollar understatement pushes
the rate to 3.1%, where power at the same window is near one. The test catches
gross miscalibration and is blind to material-but-ordinary error - which is why
Basel penalizes on an exception count rather than on a hypothesis test.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.stats import binom

from .backtest import christoffersen_independence, kupiec_pof

ALPHA = 0.05                     # test level everything below is quoted at
MAX_OBS_SEARCH = 100_000         # cap on the smallest-n search


@dataclass(frozen=True)
class PowerCurvePoint:
    n_obs: int
    power: float
    size: float                  # rejection rate under the null, i.e. realized level
    n_reject: int                # how many exception counts fall in the region


def kupiec_rejection_counts(n_obs: int, p_null: float,
                            alpha: float = ALPHA) -> np.ndarray:
    """Exception counts at which the shipped Kupiec test rejects.

    Enumerated, not derived: the region is whatever kupiec_pof actually returns,
    including its 0*log(0) convention at x=0. Both tails reject - too few
    exceptions is also a failure of coverage, which is the part practitioners
    forget.
    """
    if n_obs < 1:
        raise ValueError(f"n_obs={n_obs} must be positive")
    if not 0.0 < p_null < 1.0:
        raise ValueError(f"p_null={p_null} must lie in (0, 1)")
    return np.array([x for x in range(n_obs + 1)
                     if kupiec_pof(x, n_obs, p_null).p_value < alpha], dtype=int)


def kupiec_acceptance_band(n_obs: int, p_null: float,
                           alpha: float = ALPHA) -> tuple[int, int]:
    """Widest run of exception counts the test does NOT reject, as (low, high).

    More useful than the rejection set for reading a result: at n=750 and p=1% the
    band is 3 to 13 against 7.5 expected, so a realized rate anywhere from 0.4% to
    1.7% survives. That interval is the test's resolution, stated in the units the
    exception count is reported in.

    LR_pof is unimodal in x - it is zero at x = n*p and rises as the observed rate
    departs from the null in either direction - so the accepting counts form one
    contiguous run and are found by walking outward from the null expectation. That
    keeps this O(band width) rather than O(n), which matters because the
    smallest-window search evaluates it at n in the tens of thousands. The
    unimodality is not assumed on faith: a test checks this against brute-force
    enumeration.
    """
    if n_obs < 1:
        raise ValueError(f"n_obs={n_obs} must be positive")
    if not 0.0 < p_null < 1.0:
        raise ValueError(f"p_null={p_null} must lie in (0, 1)")

    def rejects(x: int) -> bool:
        return kupiec_pof(x, n_obs, p_null).p_value < alpha

    centre = min(max(int(round(n_obs * p_null)), 0), n_obs)
    if rejects(centre):
        raise ValueError(f"every count rejects at n_obs={n_obs}, p_null={p_null}")
    low = centre
    while low - 1 >= 0 and not rejects(low - 1):
        low -= 1
    high = centre
    while high + 1 <= n_obs and not rejects(high + 1):
        high += 1
    return low, high


def kupiec_power(n_obs: int, p_null: float, p_true: float,
                 alpha: float = ALPHA) -> float:
    """Exact probability the test rejects when the true exception rate is p_true.

    Under the alternative the count is Binomial(n, p_true), so power is the mass
    that distribution puts outside the acceptance band - computed from two CDF
    evaluations rather than by summing the region. Passing p_true = p_null gives
    the realized size.
    """
    if not 0.0 < p_true < 1.0:
        raise ValueError(f"p_true={p_true} must lie in (0, 1)")
    low, high = kupiec_acceptance_band(n_obs, p_null, alpha)
    accept = float(binom.cdf(high, n_obs, p_true))
    if low > 0:
        accept -= float(binom.cdf(low - 1, n_obs, p_true))
    return float(1.0 - accept)


def kupiec_power_curve(windows, p_null: float, p_true: float,
                       alpha: float = ALPHA) -> pd.DataFrame:
    """Power and realized size across window lengths, as a tidy frame."""
    rows = []
    for n in windows:
        counts = kupiec_rejection_counts(int(n), p_null, alpha)
        rows.append(PowerCurvePoint(
            n_obs=int(n),
            power=kupiec_power(int(n), p_null, p_true, alpha),
            size=kupiec_power(int(n), p_null, p_null, alpha),
            n_reject=int(counts.size)))
    return pd.DataFrame(rows)


def min_obs_for_power(p_null: float, p_true: float, target_power: float,
                      alpha: float = ALPHA, cap: int = MAX_OBS_SEARCH) -> int | None:
    """Smallest window at which power reaches target_power, or None within `cap`.

    Power is not monotone in n - discreteness makes it saw-tooth - so this is a
    scan for the first crossing rather than a bisection, coarse then refined.
    """
    if not 0.0 < target_power < 1.0:
        raise ValueError(f"target_power={target_power} must lie in (0, 1)")
    step = 250
    n = step
    while n <= cap:
        if kupiec_power(n, p_null, p_true, alpha) >= target_power:
            lo = max(1, n - step + 1)
            for candidate in range(lo, n + 1):
                if kupiec_power(candidate, p_null, p_true, alpha) >= target_power:
                    return candidate
            return n
        n += step
    return None


def simulate_markov_exceptions(n_obs: int, p_uncond: float, p11: float,
                               rng: np.random.Generator) -> np.ndarray:
    """One exception path from a two-state chain with a target unconditional rate.

    Given P(exception | exception) = p11 and a stationary rate p_uncond, the
    complementary transition follows from stationarity:

        p_uncond = p01 / (1 + p01 - p11)   =>   p01 = p_uncond(1 - p11)/(1 - p_uncond)

    p11 = p_uncond recovers the iid case, which is what makes this usable for both
    the size and the power leg.
    """
    if not 0.0 < p_uncond < 1.0:
        raise ValueError(f"p_uncond={p_uncond} must lie in (0, 1)")
    if not 0.0 <= p11 < 1.0:
        raise ValueError(f"p11={p11} must lie in [0, 1)")
    p01 = p_uncond * (1.0 - p11) / (1.0 - p_uncond)
    if not 0.0 <= p01 <= 1.0:
        raise ValueError(f"p11={p11} and p_uncond={p_uncond} imply p01={p01:.4f}")
    draws = rng.random(n_obs)
    out = np.zeros(n_obs, dtype=bool)
    out[0] = draws[0] < p_uncond
    for t in range(1, n_obs):
        out[t] = draws[t] < (p11 if out[t - 1] else p01)
    return out


def christoffersen_power(n_obs: int, p_uncond: float, p11: float, n_sim: int,
                         seed: int, alpha: float = ALPHA) -> float:
    """Simulated rejection rate of the independence test under clustering p11.

    p11 = p_uncond is the null, so calling it that way returns realized size.
    """
    if n_sim < 1:
        raise ValueError(f"n_sim={n_sim} must be positive")
    rng = np.random.default_rng(seed)
    rejects = 0
    for _ in range(n_sim):
        path = simulate_markov_exceptions(n_obs, p_uncond, p11, rng)
        if christoffersen_independence(path).p_value < alpha:
            rejects += 1
    return rejects / n_sim


def conditioning_observations(n_obs: int, p_uncond: float, n_sim: int,
                              seed: int) -> dict[str, float]:
    """How much data the independence test actually has to work with.

    The test estimates two transition probabilities, and everything it knows about
    P(exception | exception) comes from the days that FOLLOW an exception. At a 1%
    rate over 250 days that is about two and a half observations. A departure from
    independence cannot be detected from a sample of two, which is the mechanism
    behind the test's realized size sitting far below nominal - reported here
    beside the size so the number has an explanation rather than a shrug.

    Note what this is NOT: the statistic is not identically zero on these paths. A
    path with no exception-to-exception transition still carries information - an
    exactly alternating sequence is evidence AGAINST independence, and scores
    close to rejection. The statistic is exactly zero only when no day follows an
    exception at all, which is a much smaller share, so both are returned.
    """
    rng = np.random.default_rng(seed)
    following = np.empty(n_sim)
    no_repeat = 0
    for i in range(n_sim):
        path = simulate_markov_exceptions(n_obs, p_uncond, p_uncond, rng)
        prev, cur = path[:-1], path[1:]
        following[i] = int(prev.sum())          # n10 + n11: days after an exception
        no_repeat += not np.any(prev & cur)     # n11 == 0
    return {"mean_following": float(following.mean()),
            "median_following": float(np.median(following)),
            "share_none_following": float((following == 0).mean()),
            "share_no_repeat": no_repeat / n_sim}
