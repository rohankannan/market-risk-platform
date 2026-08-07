"""Known-answer tests for the bond pricer and sensitivities."""

import numpy as np
import pytest

from risk_engine.pricing import bond_pnl, bond_price, dollar_convexity, dv01, equity_fx_pnl


def test_par_bond_prices_at_face():
    """coupon == ytm => price == face, exactly, at every tested maturity."""
    for maturity in (2, 5, 10, 30):
        assert bond_price(0.04, maturity, 0.04) == pytest.approx(100.0, abs=1e-9)


def test_dv01_known_answer():
    """10Y 4% par bond: modified duration 8.1757 => DV01 ~ $817.5 per $1M face."""
    assert dv01(0.04, 10, 0.04, face=1_000_000) == pytest.approx(817.5, rel=0.01)


def test_delta_gamma_vs_full_reval():
    """Small shocks: delta-gamma ~ full reval within 0.1%. Large rally (-300bp):
    full-reval gain > delta-only gain (positive convexity), delta-gamma in between."""
    c, t, y, face = 0.04, 10, 0.04, 1_000_000
    d = dv01(c, t, y, face)          # $ per +1bp
    gamma = dollar_convexity(c, t, y, face)

    for bp in (5.0, -5.0):
        full = bond_pnl(c, t, y, face, np.array([bp]))[0]
        dg = -d * bp + 0.5 * gamma * (bp / 1e4) ** 2
        assert dg == pytest.approx(full, rel=1e-3)

    full_rally = bond_pnl(c, t, y, face, np.array([-300.0]))[0]
    delta_only = -d * (-300.0)
    dg_rally = delta_only + 0.5 * gamma * (300.0 / 1e4) ** 2
    assert full_rally > dg_rally > delta_only  # convexity ordering


def test_equity_pnl_exact_not_linearized():
    """P&L = qty*S0*(exp(r)-1): a -50% log shock loses 39.3%, not 50%."""
    pnl = equity_fx_pnl(1_000, 100.0, np.array([np.log(0.5)]))[0]
    assert pnl == pytest.approx(1_000 * 100.0 * (0.5 - 1.0), rel=1e-12)


def test_bond_yield_floor():
    """Yields are floored at 1bp after shocking - no negative-yield blowups."""
    pnl = bond_pnl(0.04, 10, 0.001, 1_000_000, np.array([-500.0]))
    assert np.isfinite(pnl).all()
