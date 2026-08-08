"""Known-answer tests for the P&L attribution statistics."""

import numpy as np
import pytest

from risk_engine.pla import ks_statistic, pla_test, pla_zone


def test_identical_series_are_green():
    rng = np.random.default_rng(42)
    pnl = rng.normal(0, 1000, 250)
    res = pla_test(pnl, pnl)
    assert res.spearman == pytest.approx(1.0)
    assert res.ks == 0.0
    assert res.zone == "GREEN"


def test_independent_series_are_red():
    rng = np.random.default_rng(42)
    res = pla_test(rng.normal(0, 1000, 250), rng.normal(0, 1000, 250))
    assert abs(res.spearman) < 0.3
    assert res.zone == "RED"


def test_systematic_bias_fails_ks_while_spearman_stays_perfect():
    """The reason BOTH metrics exist: a shifted RTPL ranks identically
    (Spearman 1.0) but is distributionally wrong - KS catches it alone."""
    rng = np.random.default_rng(42)
    hpl = rng.normal(0, 1000, 250)
    rtpl = hpl + 5000.0
    res = pla_test(hpl, rtpl)
    assert res.spearman == pytest.approx(1.0)
    assert res.ks > 0.9
    assert res.zone == "RED"


def test_ks_hand_computed_small_case():
    # at x=1: F_a=1/3, F_b=0 -> the sup is 1/3
    assert ks_statistic([1.0, 2.0, 3.0], [1.5, 2.5, 3.5]) == pytest.approx(1 / 3)


def test_zone_boundaries_follow_mar32():
    assert pla_zone(0.80, 0.09) == "GREEN"                       # inclusive green edges
    assert pla_zone(0.799, 0.05) == "AMBER"
    assert pla_zone(0.95, 0.091) == "AMBER"
    assert pla_zone(0.699, 0.05) == "RED"
    assert pla_zone(0.95, 0.121) == "RED"


def test_short_series_is_loud():
    with pytest.raises(ValueError, match=">= 20"):
        pla_test(np.zeros(5), np.zeros(5))
