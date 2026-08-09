"""Known-answer tests for the sampling-uncertainty primitives.

Two of these assert results that are the point of the module rather than
incidental to it: that the exact interval's realized coverage matches the
coverage it claims, and that the bootstrap percentile interval does NOT reach
nominal for a tail quantile.
"""

import numpy as np
import pytest
from scipy.stats import binom, norm

from risk_engine.inference import (
    QuantileInterval,
    bootstrap_rows,
    kde_density_at,
    quantile_interval,
    quantile_se,
    silverman_bandwidth,
)


def _normal(n, seed, sigma=1.0):
    return np.random.default_rng(seed).normal(0.0, sigma, n)


# ------------------------------------------------------------------ exact interval

def test_shipped_var_interval_is_the_first_to_tenth_worst_loss():
    """The published configuration: 500 scenarios, 99% VaR, 95% requested.

    The ranks depend only on (n, p, confidence) - not on the data - so this is a
    closed-form known answer. Coverage overshoots the request because the
    attainable levels are discrete this far into the tail.
    """
    qi = quantile_interval(_normal(500, 0), p=0.01, confidence=0.95)
    assert (qi.rank_low, qi.rank_high) == (1, 10)
    assert qi.coverage == pytest.approx(0.9623, abs=5e-5)
    assert qi.coverage >= 0.95                      # never quietly under-deliver
    assert qi.low < qi.estimate < qi.high


def test_interval_estimate_matches_the_engine_quantile_convention():
    """The interval has to bracket the number actually reported, so it uses
    var_es_from_pnl's linear-interpolation convention, not a nearest-rank one."""
    from risk_engine.var import var_es_from_pnl

    pnl = _normal(500, 7, sigma=250_000.0)
    assert quantile_interval(pnl, 0.01).estimate == pytest.approx(
        var_es_from_pnl(pnl).var, rel=1e-12)


def test_exact_interval_realizes_the_coverage_it_claims():
    """Realized coverage over many samples must land on the computed coverage.

    The ranks are data-independent, so they are solved once and then applied to
    every replication - which is also the cheapest possible demonstration that
    the guarantee is distribution-free.
    """
    n, p, reps = 200, 0.05, 4000
    ranks = quantile_interval(_normal(n, 1), p, 0.95)
    truth = norm.ppf(p)                             # true p-quantile of N(0,1)
    rng = np.random.default_rng(99)
    hits = 0
    for _ in range(reps):
        losses = np.sort(-rng.normal(0.0, 1.0, n))[::-1]
        low, high = losses[ranks.rank_high - 1], losses[ranks.rank_low - 1]
        hits += low <= -truth < high
    realized = hits / reps
    assert realized == pytest.approx(ranks.coverage, abs=0.02)


def test_interval_widens_as_the_tail_thins():
    """Same n, deeper tail: fewer observations inform the quantile, so the
    interval must widen relative to the estimate."""
    pnl = _normal(2000, 3)
    shallow = quantile_interval(pnl, 0.10)
    deep = quantile_interval(pnl, 0.01)
    assert deep.width_pct > shallow.width_pct


def test_interval_rejects_a_sample_too_small_for_the_level():
    with pytest.raises(ValueError, match="too small"):
        quantile_interval(_normal(40, 4), p=0.001, confidence=0.99)


def test_basel_minimum_window_cannot_support_a_95pct_interval_on_a_99pct_var():
    """A 250-day window admits at most ~91.9% distribution-free coverage for the
    99% quantile, so a 95% interval does not exist at any width - the widest
    possible interval, ranks 1 to n, already falls short. The shipped 500-day
    lookback admits ~99.3%. That is the sampling-uncertainty case for the longer
    window, and it is a stronger one than "it stabilizes the quantile": at
    Basel's own minimum you cannot put a 95% interval on the number being
    regulated. The crossover is n=299.
    """
    p = 0.01

    def widest(n):                       # coverage of ranks 1..n, the widest pair
        return binom.cdf(n - 1, n, p) - binom.cdf(0, n, p)

    assert widest(250) == pytest.approx(0.9189, abs=5e-4)
    assert widest(500) == pytest.approx(0.9934, abs=5e-4)
    assert widest(298) < 0.95 <= widest(299)

    with pytest.raises(ValueError, match="too small"):
        quantile_interval(_normal(250, 31), p=p, confidence=0.95)
    assert quantile_interval(_normal(500, 31), p=p, confidence=0.95).coverage >= 0.95


# --------------------------------------------------------------- density and SE

def test_kde_recovers_the_normal_density():
    sample = _normal(20_000, 5)
    for x in (-1.5, 0.0, 0.8):
        assert kde_density_at(sample, x) == pytest.approx(norm.pdf(x), rel=0.06)


def test_silverman_bandwidth_scales_with_spread_and_shrinks_with_n():
    assert silverman_bandwidth(_normal(1000, 6, sigma=10.0)) == pytest.approx(
        10.0 * silverman_bandwidth(_normal(1000, 6, sigma=1.0)), rel=1e-12)
    assert silverman_bandwidth(_normal(10_000, 6)) < silverman_bandwidth(_normal(500, 6))


def test_asymptotic_quantile_se_matches_the_closed_form_for_a_normal():
    """SE = sqrt(p(1-p)/n) / f(q_p); for N(0,1) the density is known, so the only
    error is the kernel estimate of it."""
    n, p = 5000, 0.05
    sample = _normal(n, 8)
    want = np.sqrt(p * (1 - p) / n) / norm.pdf(norm.ppf(p))
    assert quantile_se(sample, p) == pytest.approx(want, rel=0.10)


# ------------------------------------------------------------------- bootstrap

def _var99(pnl):
    return float(-np.quantile(pnl, 0.01, method="linear"))


def test_bootstrap_is_reproducible_under_a_seed():
    pnl = _normal(500, 11)
    a = bootstrap_rows(pnl, _var99, n_boot=300, seed=42)
    b = bootstrap_rows(pnl, _var99, n_boot=300, seed=42)
    assert (a.estimate, a.se, a.low, a.high) == (b.estimate, b.se, b.low, b.high)
    assert bootstrap_rows(pnl, _var99, n_boot=300, seed=43).se != a.se


def test_bootstrap_se_of_a_quantile_tracks_the_asymptotic_formula():
    n, p = 4000, 0.05
    sample = _normal(n, 12)
    boot = bootstrap_rows(sample, lambda x: float(-np.quantile(x, p, method="linear")),
                          n_boot=600, seed=1)
    assert boot.se == pytest.approx(quantile_se(sample, p), rel=0.20)


def test_block_bootstrap_widens_on_serially_correlated_data():
    """For an AR(1), the variance of the sample mean inflates by (1+rho)/(1-rho).
    An iid resample cannot see that; the moving-block variant must."""
    rho, n = 0.6, 2000
    rng = np.random.default_rng(21)
    x = np.empty(n)
    x[0] = rng.normal()
    for t in range(1, n):
        x[t] = rho * x[t - 1] + rng.normal(0.0, np.sqrt(1 - rho**2))

    mean = np.mean
    iid = bootstrap_rows(x, mean, n_boot=800, seed=2)
    block = bootstrap_rows(x, mean, n_boot=800, seed=2, block_length=50)
    assert block.se > iid.se
    assert block.se / iid.se == pytest.approx(np.sqrt((1 + rho) / (1 - rho)), rel=0.25)


def test_bootstrap_percentile_interval_undercovers_a_tail_quantile():
    """The finding, not an accident: percentile endpoints are themselves observed
    order statistics, so the interval cannot reach past the sample extremes -
    exactly where the true tail quantile plausibly sits. Realized coverage lands
    well under nominal, while the exact interval (asserted above) does not. This
    is why quantile_interval is the headline and the bootstrap sits beside it.
    """
    n, p, reps = 250, 0.01, 300
    truth = -norm.ppf(p)
    rng = np.random.default_rng(7)
    hits = 0
    for _ in range(reps):
        sample = rng.normal(0.0, 1.0, n)
        b = bootstrap_rows(sample, _var99, n_boot=200, seed=int(rng.integers(1 << 30)))
        hits += b.low <= truth <= b.high
    realized = hits / reps
    assert realized < 0.93, f"expected under-coverage at 95% nominal, got {realized:.3f}"


def test_bootstrap_rows_keeps_desks_paired():
    """Rows, never columns independently. Two perfectly anti-correlated desks net
    to a constant, so a firm-total statistic must have zero bootstrap spread -
    which only holds if each replicate keeps the two legs on the same day."""
    rng = np.random.default_rng(13)
    a = rng.normal(0.0, 1.0, 400)
    matrix = np.column_stack([a, -a])
    boot = bootstrap_rows(matrix, lambda m: float(np.std(m.sum(axis=1))),
                          n_boot=200, seed=3)
    assert boot.se == pytest.approx(0.0, abs=1e-12)
    assert boot.estimate == pytest.approx(0.0, abs=1e-12)


def test_bootstrap_rejects_bad_shapes_and_blocks():
    pnl = _normal(200, 15)
    with pytest.raises(ValueError, match="1- or 2-dimensional"):
        bootstrap_rows(np.zeros((5, 5, 5)), _var99, n_boot=10, seed=0)
    with pytest.raises(ValueError, match="block_length"):
        bootstrap_rows(pnl, _var99, n_boot=10, seed=0, block_length=999)
    with pytest.raises(ValueError, match="n_boot"):
        bootstrap_rows(pnl, _var99, n_boot=1, seed=0)


def test_interval_is_frozen():
    qi = quantile_interval(_normal(500, 17), 0.01)
    assert isinstance(qi, QuantileInterval)
    with pytest.raises(AttributeError):
        qi.estimate = 1.0        # type: ignore[misc]
