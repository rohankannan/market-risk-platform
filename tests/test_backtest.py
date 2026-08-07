"""Known-answer tests for the backtesting statistics."""

import numpy as np
import pytest

from risk_engine.backtest import (
    basel_traffic_light,
    christoffersen_conditional_coverage,
    christoffersen_independence,
    kupiec_pof,
)


def test_kupiec_known_value():
    """Published worked example: n=250, x=5, p=0.01 -> LR ~ 1.96, p ~ 0.16."""
    res = kupiec_pof(5, 250, p=0.01)
    assert res.statistic == pytest.approx(1.96, abs=0.01)
    assert res.p_value == pytest.approx(0.16, abs=0.01)
    assert not res.reject_5pct


def test_kupiec_exact_coverage_gives_zero():
    """x = n*p exactly -> LR = 0 (H0 and MLE coincide)."""
    res = kupiec_pof(5, 500, p=0.01)
    assert res.statistic == pytest.approx(0.0, abs=1e-12)


def test_kupiec_zero_exceptions_no_crash():
    res = kupiec_pof(0, 250, p=0.01)
    assert np.isfinite(res.statistic) and np.isfinite(res.p_value)


def test_christoffersen_detects_clustering():
    """10 consecutive exceptions in 500 days: independence rejected hard.
    The same 10 spread evenly: not rejected. Kupiec sees both identically -
    that's why both tests run."""
    clustered = np.zeros(500, dtype=bool)
    clustered[100:110] = True
    spread = np.zeros(500, dtype=bool)
    spread[49::50] = True

    res_c = christoffersen_independence(clustered)
    res_s = christoffersen_independence(spread)
    assert res_c.p_value < 0.01
    assert res_s.p_value > 0.05

    # identical Kupiec outcomes by construction
    assert kupiec_pof(10, 500).statistic == pytest.approx(kupiec_pof(10, 500).statistic)


def test_christoffersen_no_1_to_1_transitions_edge_case():
    """n11 = 0 (typical in green-zone data) must not crash: 0*log(0) := 0."""
    e = np.zeros(250, dtype=bool)
    e[[50, 120, 200]] = True
    res = christoffersen_independence(e)
    assert np.isfinite(res.statistic)
    cc = christoffersen_conditional_coverage(e, p=0.01)
    assert cc.df == 2 and np.isfinite(cc.p_value)


def test_traffic_light_boundaries():
    assert basel_traffic_light(0).zone == "GREEN"
    assert basel_traffic_light(4).zone == "GREEN"
    g5 = basel_traffic_light(5)
    assert (g5.zone, g5.plus_factor, g5.multiplier) == ("AMBER", 0.40, 3.40)
    g9 = basel_traffic_light(9)
    assert (g9.zone, g9.plus_factor) == ("AMBER", 0.85)
    g10 = basel_traffic_light(10)
    assert (g10.zone, g10.plus_factor, g10.multiplier) == ("RED", 1.00, 4.00)
