"""Sampling uncertainty on the risk numbers.

Every measure this engine reports is a statistic of a few hundred scenarios, and
each has been published as a point estimate to the cent. A 99% VaR from 500
draws is an order statistic - var_es_from_pnl's own docstring notes it
interpolates between the 5th and 6th worst losses - and its sampling error is
large next to the precision it is printed with. This module quantifies that
error three ways, in increasing order of what they assume:

  - `quantile_interval`: distribution-free and EXACT. The number of observations
    at or below the true p-quantile is Binomial(n, p), so a pair of order
    statistics brackets it with a coverage probability computed in closed form -
    no asymptotics, no resampling, no distributional assumption beyond
    continuity. This is the honest headline for a VaR interval.
  - `quantile_se`: the asymptotic standard error sqrt(p(1-p)/n) / f(q_p). Needs a
    density at the quantile, supplied by a hand-rolled Gaussian kernel with
    Silverman's bandwidth. Cheap, and it degrades exactly where the density
    estimate does - far out in the tail.
  - `bootstrap_rows`: resamples scenario ROWS, so any statistic of the joint P&L
    matrix - VaR, ES, the diversification benefit, a desk's ES share - gets an
    interval from one primitive. Rows, never columns independently: the
    cross-factor dependence the whole book rests on lives in the rows, and
    resampling factors separately would destroy it and report a smaller number.

A deliberate finding, asserted in the tests rather than assumed: the bootstrap
PERCENTILE interval under-covers a tail quantile. Its endpoints are themselves
observed order statistics, so it cannot reach past the sample extremes, which is
precisely where the true quantile plausibly sits. That is why `quantile_interval`
leads and the bootstrap is reported beside it rather than instead of it.

Everything stochastic takes a seed, so a reported interval is reproducible.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from scipy.stats import binom

GAUSSIAN_NORM = 1.0 / np.sqrt(2.0 * np.pi)
SILVERMAN_IQR_SCALE = 1.349      # IQR of the standard normal
MIN_OBS = 20                     # below this an interval is theatre


@dataclass(frozen=True)
class QuantileInterval:
    """Exact distribution-free interval for a quantile, as loss dollars.

    rank_low / rank_high are 1-based order statistics of the LOSS series, so
    "1 to 10" reads as "between the worst and the 10th-worst observation" and can
    be quoted directly.
    """

    estimate: float
    low: float
    high: float
    rank_low: int
    rank_high: int
    coverage: float
    confidence: float
    n: int

    @property
    def width_pct(self) -> float:
        return (self.high - self.low) / self.estimate if self.estimate else float("nan")


@dataclass(frozen=True)
class BootstrapResult:
    estimate: float
    se: float
    low: float
    high: float
    confidence: float
    n_boot: int
    block_length: int | None

    @property
    def se_pct(self) -> float:
        return self.se / self.estimate if self.estimate else float("nan")


def _clean(sample) -> np.ndarray:
    arr = np.asarray(sample, dtype=float).ravel()
    arr = arr[~np.isnan(arr)]
    if arr.size < MIN_OBS:
        raise ValueError(f"need >={MIN_OBS} observations for an interval, got {arr.size}")
    return arr


def quantile_interval(pnl, p: float, confidence: float = 0.95) -> QuantileInterval:
    """Narrowest pair of order statistics bracketing the p-quantile loss.

    With q the true p-quantile of the P&L distribution and B the count of
    observations at or below it, B ~ Binomial(n, p), and

        P(X_(i) <= q < X_(j)) = P(i <= B <= j-1) = C(j-1) - C(i-1)

    for C the binomial CDF. Coverage is therefore exact and discrete: the
    achieved level is reported rather than the requested one, because with p near
    zero the attainable levels are coarse.

    Returned as positive loss dollars, so `low`/`high` bracket the reported VaR
    and the ranks count inward from the worst loss.
    """
    arr = _clean(pnl)
    n = arr.size
    if not 0.0 < p < 1.0:
        raise ValueError(f"p={p} must lie in (0, 1)")
    if not 0.0 < confidence < 1.0:
        raise ValueError(f"confidence={confidence} must lie in (0, 1)")

    # cdf[m] = P(B <= m-1), so coverage(i, j) = cdf[j] - cdf[i]
    cdf = np.empty(n + 2)
    cdf[0] = 0.0
    cdf[1:] = binom.cdf(np.arange(n + 1), n, p)

    best: tuple[int, int, float] | None = None
    for i in range(1, n + 1):
        target = confidence + cdf[i]
        if target > cdf[n]:
            break                                  # even j=n cannot reach the level
        j = max(int(np.searchsorted(cdf, target, side="left")), i + 1)
        if j > n:
            continue
        if best is None or (j - i) < (best[1] - best[0]):
            best = (i, j, float(cdf[j] - cdf[i]))
    if best is None:
        raise ValueError(f"n={n} is too small for {confidence:.0%} coverage at p={p}")

    i, j, coverage = best
    losses = np.sort(-arr)[::-1]                   # worst loss first
    return QuantileInterval(
        estimate=float(-np.quantile(arr, p, method="linear")),
        low=float(losses[j - 1]),                  # the inner (jth-worst) endpoint
        high=float(losses[i - 1]),                 # the outer (ith-worst) endpoint
        rank_low=i,
        rank_high=j,
        coverage=coverage,
        confidence=confidence,
        n=n,
    )


def silverman_bandwidth(sample) -> float:
    """Silverman's rule of thumb, robustified by the IQR against a fat tail."""
    arr = _clean(sample)
    sd = float(np.std(arr, ddof=1))
    iqr = float(np.percentile(arr, 75) - np.percentile(arr, 25))
    scale = min(sd, iqr / SILVERMAN_IQR_SCALE) if iqr > 0.0 else sd
    if scale <= 0.0:
        raise ValueError("sample has no spread - bandwidth undefined")
    return 0.9 * scale * arr.size ** (-0.2)


def kde_density_at(sample, x: float, bandwidth: float | None = None) -> float:
    """Gaussian kernel density estimate at one point. Hand-rolled: the whole
    content is the kernel sum, and importing it would hide the bandwidth choice
    that actually drives the answer."""
    arr = _clean(sample)
    h = silverman_bandwidth(arr) if bandwidth is None else float(bandwidth)
    if h <= 0.0:
        raise ValueError(f"bandwidth={h} must be positive")
    z = (x - arr) / h
    return float(np.mean(GAUSSIAN_NORM * np.exp(-0.5 * z * z)) / h)


def quantile_se(pnl, p: float, bandwidth: float | None = None) -> float:
    """Asymptotic standard error of the p-quantile, in loss dollars.

    sqrt(p(1-p)/n) / f(q_p). The density in the denominator is the weak link: in
    a thin tail f is small, so the same sampling noise buys a much wider dollar
    interval, which is the intuition the exact interval makes concrete.
    """
    arr = _clean(pnl)
    q = float(np.quantile(arr, p, method="linear"))
    density = kde_density_at(arr, q, bandwidth)
    if density <= 0.0:
        raise ValueError("density estimate vanished at the quantile - widen the bandwidth")
    return float(np.sqrt(p * (1.0 - p) / arr.size) / density)


def _row_indices(n: int, n_boot: int, block_length: int | None,
                 rng: np.random.Generator) -> np.ndarray:
    if block_length is None:
        return rng.integers(0, n, size=(n_boot, n))
    if not 1 <= block_length <= n:
        raise ValueError(f"block_length={block_length} must lie in [1, {n}]")
    n_blocks = int(np.ceil(n / block_length))
    starts = rng.integers(0, n - block_length + 1, size=(n_boot, n_blocks))
    offsets = np.arange(block_length)
    return (starts[:, :, None] + offsets[None, None, :]).reshape(n_boot, -1)[:, :n]


def bootstrap_rows(data, statistic: Callable[[np.ndarray], float],
                   n_boot: int, seed: int, confidence: float = 0.95,
                   block_length: int | None = None) -> BootstrapResult:
    """Bootstrap any statistic of the scenario set by resampling whole rows.

    `data` is (n,) for a single P&L vector or (n, k) for the per-desk matrix;
    `statistic` consumes a resampled array of the same shape and returns one
    number. Resampling rows preserves the cross-sectional dependence between
    desks, which is what makes a diversification-benefit interval meaningful.

    block_length=None is the iid resample; an integer selects the moving-block
    variant, which keeps runs of adjacent days together and so admits the serial
    dependence an iid resample assumes away.
    """
    arr = np.asarray(data, dtype=float)
    if arr.ndim not in (1, 2):
        raise ValueError(f"data must be 1- or 2-dimensional, got {arr.ndim}")
    n = arr.shape[0]
    if n < MIN_OBS:
        raise ValueError(f"need >={MIN_OBS} rows to bootstrap, got {n}")
    if n_boot < 2:
        raise ValueError(f"n_boot={n_boot} must be at least 2")

    rng = np.random.default_rng(seed)
    idx = _row_indices(n, n_boot, block_length, rng)
    replicates = np.array([statistic(arr[row]) for row in idx], dtype=float)
    tail = (1.0 - confidence) / 2.0
    return BootstrapResult(
        estimate=float(statistic(arr)),
        se=float(np.std(replicates, ddof=1)),
        low=float(np.quantile(replicates, tail, method="linear")),
        high=float(np.quantile(replicates, 1.0 - tail, method="linear")),
        confidence=confidence,
        n_boot=int(n_boot),
        block_length=block_length,
    )
