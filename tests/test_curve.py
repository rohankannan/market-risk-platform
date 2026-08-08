"""Known-answer tests for the zero-curve bootstrap and key-rate DV01s."""

import numpy as np
import pandas as pd
import pytest

from risk_engine.curve import (
    bootstrap_zero_curve,
    curve_dv01,
    key_rate_dv01s,
    price_bond_on_curve,
)
from risk_engine.pricing import bond_price, dv01

PAR = pd.Series({0.25: 0.040, 2.0: 0.038, 5.0: 0.040, 10.0: 0.042, 30.0: 0.045})


def test_flat_par_curve_bootstraps_to_flat_zeros():
    """On a flat curve, par yield == semiannual zero exactly (coupon reinvests
    at the same rate) - the bootstrap must recover it to solver precision."""
    flat = pd.Series({0.25: 0.04, 2.0: 0.04, 5.0: 0.04, 10.0: 0.04, 30.0: 0.04})
    curve = bootstrap_zero_curve(flat)
    np.testing.assert_allclose(curve.zeros, 0.04, atol=1e-10)


def test_node_par_bonds_reprice_at_par_by_construction():
    curve = bootstrap_zero_curve(PAR)
    for maturity, y in PAR.items():
        if maturity < 0.5:
            continue
        assert price_bond_on_curve(curve, y, maturity, 100.0) == pytest.approx(100.0, abs=1e-8)


def test_zeros_sit_above_pars_on_an_upward_curve():
    """Upward-sloping par curve discounts early coupons at lower rates, so
    zeros exceed pars at the long end (classic coupon-drag ordering)."""
    upward = pd.Series({0.25: 0.02, 2.0: 0.025, 5.0: 0.03, 10.0: 0.035, 30.0: 0.04})
    curve = bootstrap_zero_curve(upward)
    assert curve.zeros[-1] > upward.iloc[-1]


def test_krds_sum_to_parallel_curve_dv01():
    krd = key_rate_dv01s(PAR, coupon=0.042, maturity_years=10.0, face=1_000_000.0)
    total = curve_dv01(PAR, coupon=0.042, maturity_years=10.0, face=1_000_000.0)
    assert krd.sum() == pytest.approx(total, rel=1e-3)


def test_calibration_instrument_krd_is_diagonal_by_construction():
    """A par bond at a node with coupon equal to that node's par input IS the
    bootstrap instrument: any other node's bump re-solves the curve to keep it
    at 100 exactly, so its KRD is pinned to its own node. This is the classic
    validator question, not a bug."""
    krd = key_rate_dv01s(PAR, coupon=PAR[10.0], maturity_years=10.0, face=1_000_000.0)
    assert krd[10.0] / krd.sum() == pytest.approx(1.0, abs=1e-6)
    off_diag = krd.drop(10.0).abs().max()
    assert off_diag < 1.0                                        # under a dollar per $1M


def test_off_par_coupon_unpins_cross_tenor_krd():
    """Once the coupon drifts from the node's par input (an aged book), the
    price depends on the interpolated zeros discounting the coupons, and
    neighbouring nodes carry real risk - the spillover the one-factor mapping
    cannot see."""
    krd = key_rate_dv01s(PAR, coupon=PAR[10.0] - 0.010, maturity_years=10.0,
                         face=1_000_000.0)
    assert abs(krd[5.0]) > 5.0                                   # dollars per bp, real
    # discount coupons make the neighbour KRDs negative, pushing own-share past 1
    assert abs(krd[10.0] / krd.sum() - 1.0) > 0.005


def test_off_node_bond_splits_between_neighbouring_nodes():
    krd = key_rate_dv01s(PAR, coupon=0.041, maturity_years=7.0, face=1_000_000.0)
    shares = (krd / krd.sum()).drop(0.25)
    assert shares[5.0] > 0.15 and shares[10.0] > 0.15          # both neighbours carry it
    assert shares[5.0] + shares[10.0] > 0.9


def test_curve_total_dv01_close_to_closed_form_ytm_dv01():
    """Same bond, two models: curve DV01 vs the closed-form ytm DV01. They
    differ by construction (bootstrapped zeros vs flat ytm) but must agree to
    a few percent for a node par bond."""
    total = curve_dv01(PAR, coupon=0.042, maturity_years=10.0, face=1_000_000.0)
    closed = dv01(coupon=0.042, maturity_years=10.0, ytm=0.042, face=1_000_000.0)
    assert total == pytest.approx(closed, rel=0.05)


def test_curve_price_matches_closed_form_on_flat_curve():
    """On a flat curve the strip-discounted price equals the ytm closed form
    exactly (same compounding, same rate everywhere)."""
    flat = pd.Series({0.25: 0.04, 2.0: 0.04, 5.0: 0.04, 10.0: 0.04, 30.0: 0.04})
    curve = bootstrap_zero_curve(flat)
    got = price_bond_on_curve(curve, 0.035, 10.0, 1_000_000.0)
    want = bond_price(0.035, 10.0, 0.04, 1_000_000.0)
    assert got == pytest.approx(want, rel=1e-9)
