"""Known-answer tests for backtest power and realized size.

Several of these assert things that are uncomfortable rather than reassuring -
that realized size is not the nominal level, that power is not monotone in the
window, that the independence test is badly under-sized. Those are the results;
a test suite that only encoded the comfortable ones would be measuring the
textbook rather than the code.
"""

import numpy as np
import pytest
from scipy.stats import norm

from risk_engine.backtest import christoffersen_independence, kupiec_pof
from risk_engine.power import (
    christoffersen_power,
    conditioning_observations,
    kupiec_acceptance_band,
    kupiec_power,
    kupiec_power_curve,
    kupiec_rejection_counts,
    min_obs_for_power,
    simulate_markov_exceptions,
)

P0 = 0.01
P_RATE_UP_20 = 0.012         # an exception rate 20% above nominal; under normality
                             # this is a VaR understated by only ~3% in dollars


# ------------------------------------------------------------------ Kupiec, exact

def test_the_documented_five_of_250_case_is_inside_the_acceptance_band():
    """backtest.py's own known answer, restated as resolution: n=250, x=5 gives
    p ~ 0.16, and the band shows why - the test tolerates 1 through 6."""
    assert kupiec_pof(5, 250, P0).p_value == pytest.approx(0.16, abs=0.01)
    assert kupiec_acceptance_band(250, P0) == (1, 6)
    assert 5 not in set(kupiec_rejection_counts(250, P0).tolist())


def test_acceptance_band_at_the_published_window_is_wide():
    """750 days is the window the README and model doc quote. The test accepts 3
    to 13 exceptions against 7.5 expected - a realized rate from 0.40% to 1.73% -
    which is the honest statement of what that GREEN result rules out."""
    low, high = kupiec_acceptance_band(750, P0)
    assert (low, high) == (3, 13)
    assert low / 750 == pytest.approx(0.0040, abs=1e-4)
    assert high / 750 == pytest.approx(0.0173, abs=1e-4)
    assert low <= 6 <= high            # the published HS count survives, as reported


def test_fast_band_agrees_with_brute_force_enumeration():
    """The band is found by walking outward from the null expectation, which is only
    valid because LR_pof is unimodal in the count. Checked against testing every
    count, at sizes small enough to enumerate."""
    for n in (60, 137, 250, 400):
        for p in (0.01, 0.025, 0.05):
            rejected = set(kupiec_rejection_counts(n, p).tolist())
            accepted = [x for x in range(n + 1) if x not in rejected]
            assert kupiec_acceptance_band(n, p) == (accepted[0], accepted[-1])
            # contiguous: no accepting count sits inside the rejection region
            assert accepted == list(range(accepted[0], accepted[-1] + 1))


def test_both_tails_reject_not_just_too_many_exceptions():
    """Too FEW exceptions is also a coverage failure - an over-conservative model
    wastes capital. Zero exceptions rejects at every window here."""
    counts = set(kupiec_rejection_counts(750, P0).tolist())
    assert 0 in counts and 1 in counts and 2 in counts
    assert max(counts) == 750


def test_power_against_a_20pct_higher_exception_rate_is_near_its_own_size():
    """The headline. At the published window the test rejects a model whose
    exception rate runs 20% hot about as often as it rejects a correct one -
    and in dollar terms that alternative is only a ~3% understatement, so the
    blindness covers exactly the ordinary-sized errors."""
    power = kupiec_power(750, P0, P_RATE_UP_20)
    size = kupiec_power(750, P0, P0)
    assert power == pytest.approx(0.0787, abs=5e-4)
    assert size == pytest.approx(0.0408, abs=5e-4)
    assert power < 2.0 * size          # not remotely a usable detector


def test_realized_size_is_not_the_nominal_level():
    """Discreteness: the region is a set of integer counts, so its binomial mass
    lands wherever it lands. At 250 days the test is nearly twice as likely to
    reject a correct model as advertised."""
    assert kupiec_power(250, P0, P0) == pytest.approx(0.0948, abs=5e-4)
    assert kupiec_power(750, P0, P0) == pytest.approx(0.0408, abs=5e-4)


def test_power_is_not_monotone_in_the_window():
    """Adding observations reshuffles which counts fall inside the region, so
    power saw-tooths. 750 days has LESS power than 500 against the same
    alternative - worth knowing before reading a longer backtest as a stronger
    one."""
    assert kupiec_power(750, P0, P_RATE_UP_20) < kupiec_power(500, P0, P_RATE_UP_20)


def test_power_still_rises_with_the_window_in_the_long_run():
    curve = kupiec_power_curve([500, 2000, 5000], P0, P_RATE_UP_20)
    assert curve["power"].is_monotonic_increasing
    assert curve.loc[curve["n_obs"] == 5000, "power"].item() > 0.25


def test_power_at_the_null_is_exactly_the_size_column():
    curve = kupiec_power_curve([250, 750], P0, P0)
    assert np.allclose(curve["power"], curve["size"])


def test_eighty_percent_power_needs_decades():
    """~83 years of daily observations to resolve a 1.2% exception rate from the
    1% target at 80% power. This is why Basel penalizes on an exception count instead of running a
    hypothesis test."""
    n = min_obs_for_power(P0, P_RATE_UP_20, 0.80)
    assert n is not None
    assert 20_000 <= n <= 22_000
    assert kupiec_power(n, P0, P_RATE_UP_20) >= 0.80
    assert kupiec_power(n - 1, P0, P_RATE_UP_20) < 0.80     # first crossing


def test_the_rate_to_dollar_translation_is_kept_straight():
    """A first draft called the 1.2% alternative 'a VaR understated by 20%'. It is
    not, and the difference changes the story's shape, so the translation is
    pinned: under normality a 1.2% rate is a ~3% dollar understatement, while a
    genuine 20% dollar understatement produces a 3.1% rate - against which the
    test's power at the published window is near one. Kupiec catches gross
    miscalibration; what it cannot see is ordinary-sized error."""
    z99 = norm.ppf(0.99)
    # the alternative used throughout, translated to dollars
    z_alt = norm.ppf(1.0 - P_RATE_UP_20)
    assert 1.0 - z_alt / z99 == pytest.approx(0.030, abs=0.005)
    # a genuine 20% dollar understatement, translated to a rate
    rate_20pct_short = float(norm.sf(0.8 * z99))
    assert rate_20pct_short == pytest.approx(0.0314, abs=5e-4)
    assert kupiec_power(750, P0, rate_20pct_short) > 0.95


def test_min_obs_returns_none_when_unreachable_within_the_cap():
    assert min_obs_for_power(P0, P_RATE_UP_20, 0.99, cap=1000) is None


def test_kupiec_power_validates_inputs():
    with pytest.raises(ValueError, match="p_true"):
        kupiec_power(250, P0, 1.5)
    with pytest.raises(ValueError, match="p_null"):
        kupiec_rejection_counts(250, 0.0)
    with pytest.raises(ValueError, match="n_obs"):
        kupiec_rejection_counts(0, P0)


# ------------------------------------------------- Christoffersen, simulated

def test_markov_simulation_hits_its_target_unconditional_rate():
    rng = np.random.default_rng(1)
    paths = [simulate_markov_exceptions(2000, 0.05, 0.05, rng) for _ in range(40)]
    assert float(np.mean([p.mean() for p in paths])) == pytest.approx(0.05, abs=0.006)


def test_p11_equal_to_the_rate_reproduces_independence():
    """p11 = p_uncond is the iid case: the realized 1->1 rate must match the
    unconditional rate, which is what makes one simulator serve size and power."""
    rng = np.random.default_rng(2)
    path = simulate_markov_exceptions(200_000, 0.05, 0.05, rng)
    after_exception = path[1:][path[:-1]]
    assert float(after_exception.mean()) == pytest.approx(0.05, abs=0.005)


def test_clustering_raises_the_conditional_rate():
    rng = np.random.default_rng(3)
    path = simulate_markov_exceptions(200_000, 0.05, 0.30, rng)
    after_exception = path[1:][path[:-1]]
    assert float(after_exception.mean()) == pytest.approx(0.30, abs=0.01)


def test_vectorized_paths_match_the_reference_simulator():
    """christoffersen_power runs on a time-vectorized simulator so a read-time
    caller can afford thousands of paths; the per-path reference stays as the
    spec. Same stationary rate, same conditional rate, to simulation tolerance."""
    from risk_engine.power import _markov_paths

    rng = np.random.default_rng(5)
    matrix = _markov_paths(4000, 0.05, 0.30, 60, rng)
    assert matrix.shape == (60, 4000)
    assert float(matrix.mean()) == pytest.approx(0.05, abs=0.005)
    prev, cur = matrix[:, :-1], matrix[:, 1:]
    after = cur[prev]
    assert float(after.mean()) == pytest.approx(0.30, abs=0.01)


def test_independence_test_is_badly_under_sized_at_a_one_percent_rate():
    """Realized size far below nominal, with a measured mechanism: everything the
    test knows about P(exception | exception) comes from the days following an
    exception, and at a 1% rate over 250 days that is about 2.5 observations.
    Nothing detects dependence from a sample of two."""
    size = christoffersen_power(250, P0, P0, n_sim=800, seed=42)
    info = conditioning_observations(250, P0, n_sim=800, seed=42)
    assert size < 0.03                          # nominal is 0.05
    assert info["mean_following"] == pytest.approx(2.5, abs=0.3)
    assert info["median_following"] <= 3.0


def test_a_zero_n11_path_is_not_a_zero_statistic():
    """The tempting explanation for the under-sizing is wrong and worth pinning so
    it is not reintroduced: an alternating path has no exception-to-exception
    transition yet scores near rejection, because perfect alternation is itself
    evidence against independence. The statistic vanishes only when no day follows
    an exception at all."""
    alternating = christoffersen_independence(np.array([False, True, False, True]))
    assert alternating.statistic > 3.0
    assert christoffersen_independence(np.array([False, False, False, True])).statistic == 0.0

    info = conditioning_observations(250, P0, n_sim=600, seed=42)
    assert info["share_no_repeat"] > 0.95               # n11 == 0 almost always
    assert info["share_none_following"] < 0.20         # but the statistic rarely vanishes


def test_independence_test_does_have_power_against_real_clustering():
    """Under-sized is not useless: against the failure mode it exists for it
    rejects most of the time at the published window."""
    power = christoffersen_power(750, P0, 0.25, n_sim=600, seed=42)
    size = christoffersen_power(750, P0, P0, n_sim=600, seed=42)
    assert power > 0.55
    assert power > 10.0 * size


def test_simulated_power_is_reproducible_under_a_seed():
    a = christoffersen_power(250, P0, 0.25, n_sim=200, seed=7)
    b = christoffersen_power(250, P0, 0.25, n_sim=200, seed=7)
    assert a == b
    assert christoffersen_power(250, P0, 0.25, n_sim=200, seed=8) != a


def test_markov_simulation_validates_inputs():
    rng = np.random.default_rng(0)
    with pytest.raises(ValueError, match="p_uncond"):
        simulate_markov_exceptions(100, 0.0, 0.1, rng)
    with pytest.raises(ValueError, match="p11"):
        simulate_markov_exceptions(100, 0.05, 1.0, rng)
    with pytest.raises(ValueError, match="n_sim"):
        christoffersen_power(100, P0, P0, n_sim=0, seed=1)
    # the vectorized path must refuse the same domain the reference refuses:
    # p11=1.0 slips the derived-p01 check and would fabricate power 0.0 from
    # absorbing chains, p11<0 would silently behave as p11=0
    with pytest.raises(ValueError, match="p11"):
        christoffersen_power(100, P0, 1.0, n_sim=10, seed=1)
    with pytest.raises(ValueError, match="p11"):
        christoffersen_power(100, P0, -0.5, n_sim=10, seed=1)
