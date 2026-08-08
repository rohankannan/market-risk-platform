"""Known-answer tests for Black-Scholes pricing and the engine's option branch."""

import numpy as np
import pandas as pd
import pytest

from risk_engine.engine import revalue
from risk_engine.options import MIN_IV, bs_delta, bs_gamma, bs_price, bs_vega

# Hull's classic example: S=42, K=40, r=10%, sigma=20%, T=0.5
HULL = dict(spot=42.0, strike=40.0, vol=0.20, t=0.5, rate=0.10)


def test_hull_textbook_prices():
    assert float(bs_price("CALL", **HULL)) == pytest.approx(4.759, abs=1e-3)
    assert float(bs_price("PUT", **HULL)) == pytest.approx(0.808, abs=1e-3)


def test_put_call_parity_exact():
    c = float(bs_price("CALL", **HULL))
    p = float(bs_price("PUT", **HULL))
    forward = HULL["spot"] - HULL["strike"] * np.exp(-HULL["rate"] * HULL["t"])
    assert c - p == pytest.approx(forward, abs=1e-12)


def test_greeks_match_finite_differences():
    eps = 1e-5
    for kind in ("CALL", "PUT"):
        up = float(bs_price(kind, HULL["spot"] + eps, HULL["strike"], HULL["vol"],
                            HULL["t"], HULL["rate"]))
        dn = float(bs_price(kind, HULL["spot"] - eps, HULL["strike"], HULL["vol"],
                            HULL["t"], HULL["rate"]))
        assert float(bs_delta(kind, **HULL)) == pytest.approx((up - dn) / (2 * eps), abs=1e-6)
    heps = 1e-3          # second difference needs a wider step to beat cancellation
    mid = float(bs_price("CALL", **HULL))
    up = float(bs_price("CALL", HULL["spot"] + heps, HULL["strike"], HULL["vol"],
                        HULL["t"], HULL["rate"]))
    dn = float(bs_price("CALL", HULL["spot"] - heps, HULL["strike"], HULL["vol"],
                        HULL["t"], HULL["rate"]))
    assert float(bs_gamma(HULL["spot"], HULL["strike"], HULL["vol"], HULL["t"],
                          HULL["rate"])) == pytest.approx((up + dn - 2 * mid) / heps**2, rel=1e-4)
    vup = float(bs_price("CALL", HULL["spot"], HULL["strike"], HULL["vol"] + eps,
                         HULL["t"], HULL["rate"]))
    vdn = float(bs_price("CALL", HULL["spot"], HULL["strike"], HULL["vol"] - eps,
                         HULL["t"], HULL["rate"]))
    assert float(bs_vega(HULL["spot"], HULL["strike"], HULL["vol"], HULL["t"],
                         HULL["rate"])) == pytest.approx((vup - vdn) / (2 * eps), rel=1e-6)


def test_shocked_vol_floors_instead_of_exploding():
    crashed = bs_price("CALL", 100.0, 100.0, -0.30, 1 / 12, 0.04)
    floored = bs_price("CALL", 100.0, 100.0, MIN_IV, 1 / 12, 0.04)
    assert float(crashed) == pytest.approx(float(floored))


def _option_book():
    return pd.DataFrame([
        {"ticker": "SPY_PUT", "desk_code": "EQUITY", "factor_code": "EQ.SPY",
         "quantity": 7800.0, "instrument_type": "OPTION", "return_conv": "LOG",
         "coupon": np.nan, "maturity_years": 1 / 12, "vol_factor_code": "VOL.SPX.IV30",
         "rate_factor_code": "IR.UST.3M", "option_type": "PUT", "moneyness": 0.95},
    ])


LEVELS = pd.Series({"EQ.SPY": 770.0, "VOL.SPX.IV30": 16.5, "IR.UST.3M": 3.9})


def test_option_full_reval_matches_direct_bs():
    shocks = pd.DataFrame({"EQ.SPY": [-0.05], "VOL.SPX.IV30": [4.0], "IR.UST.3M": [-10.0]})
    pnl = revalue(_option_book(), LEVELS, shocks, mode="full")
    base = bs_price("PUT", 770.0, 0.95 * 770.0, 0.165, 1 / 12, 0.039)
    bumped = bs_price("PUT", 770.0 * np.exp(-0.05), 0.95 * 770.0, 0.205, 1 / 12, 0.038)
    assert pnl["SPY_PUT"].iloc[0] == pytest.approx(7800.0 * float(bumped - base), rel=1e-12)


def test_rtpl_taylor_per_leg_convergence_and_cross_term_gap():
    """Delta-gamma matches a pure spot move and vega a pure vol move to well
    under 1%; the JOINT small move keeps a vanna-sized residual (RTPL excludes
    cross terms by design) and a large joint move gaps wide open - the exact
    anatomy of the PLA content."""
    book = _option_book()

    def gap(spy, volpts):
        shocks = pd.DataFrame({"EQ.SPY": [spy], "VOL.SPX.IV30": [volpts], "IR.UST.3M": [0.0]})
        full = revalue(book, LEVELS, shocks, mode="full")["SPY_PUT"].iloc[0]
        dg = revalue(book, LEVELS, shocks, mode="delta_gamma")["SPY_PUT"].iloc[0]
        return full, dg

    full, dg = gap(0.002, 0.0)
    assert dg == pytest.approx(full, rel=0.005)                    # spot leg: locally exact
    full, dg = gap(0.0, 0.1)
    assert dg == pytest.approx(full, rel=0.005)                    # vol leg: locally exact
    full_small, dg_small = gap(0.002, 0.1)
    small_gap = abs(dg_small - full_small)
    assert 0 < small_gap < 0.03 * abs(full_small)                  # vanna-sized residual
    full_large, dg_large = gap(-0.08, 8.0)
    assert abs(dg_large - full_large) > 10 * small_gap             # honestly wrong far out


def test_long_put_pnl_signs():
    shocks = pd.DataFrame({"EQ.SPY": [-0.05, 0.05], "VOL.SPX.IV30": [0.0, 0.0],
                           "IR.UST.3M": [0.0, 0.0]})
    pnl = revalue(_option_book(), LEVELS, shocks, mode="full")["SPY_PUT"]
    assert pnl.iloc[0] > 0 > pnl.iloc[1]                           # protection pays in the fall


def test_option_missing_vol_factor_is_loud():
    shocks = pd.DataFrame({"EQ.SPY": [0.01], "IR.UST.3M": [0.0]})
    with pytest.raises(ValueError, match="VOL.SPX.IV30"):
        revalue(_option_book(), LEVELS.drop("VOL.SPX.IV30"), shocks, mode="full")
