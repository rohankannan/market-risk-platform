"""Tests for the backfill helpers and the backfill loop on synthetic data."""

import numpy as np
import pandas as pd

from risk_engine.backfill import run_backfill
from risk_engine.factors import align_levels
from risk_engine.pricing import _bond_price_vec, bond_price
from risk_engine.var import ewma_vol_forecast_series, ewma_volatility


def test_vectorized_bond_price_matches_scalar_exactly():
    ys = np.array([0.001, 0.01, 0.04, 0.055, 0.12])
    vec = _bond_price_vec(0.04, 10, ys, 1_000_000)
    scalar = np.array([bond_price(0.04, 10, y, 1_000_000) for y in ys])
    np.testing.assert_allclose(vec, scalar, rtol=1e-14)


def test_forecast_series_identity_with_shifted_vols():
    rng = np.random.default_rng(42)
    r = pd.DataFrame({"X": rng.normal(0, 0.01, 200)})
    vols = ewma_volatility(r, seed_window=30)
    fc = ewma_vol_forecast_series(r, seed_window=30)
    # forecast at t equals the recursion's sigma_{t+1}, wherever t+1 exists
    np.testing.assert_allclose(fc["X"].to_numpy()[30:-1], vols["X"].to_numpy()[31:], rtol=1e-12)


def test_align_levels_respects_per_factor_caps_and_counts():
    idx = pd.bdate_range("2024-01-01", periods=10)
    lv = pd.DataFrame({"A": np.arange(10.0), "B": np.arange(10.0)}, index=idx)
    lv.loc[idx[3:8], "A"] = np.nan          # 5-day gap
    lv.loc[idx[3:8], "B"] = np.nan
    filled, counts = align_levels(lv, {"A": 3, "B": 7})
    assert filled["A"].isna().sum() == 2    # cap 3 fills 3 of 5
    assert filled["B"].isna().sum() == 0    # cap 7 fills all 5
    assert counts["A"] == 3 and counts["B"] == 5


def _tiny_book():
    return pd.DataFrame([
        {"ticker": "SPY", "desk_code": "EQUITY", "factor_code": "EQ.SPY", "quantity": 1_000.0,
         "instrument_type": "ETF", "return_conv": "LOG", "coupon": None, "maturity_years": None},
        {"ticker": "UST_10Y", "desk_code": "RATES", "factor_code": "IR.UST.10Y",
         "quantity": 1_000_000.0, "instrument_type": "GOVT_BOND", "return_conv": "ABS_BP",
         "coupon": 0.04, "maturity_years": 10.0},
    ])


def test_backfill_shapes_and_exception_flag():
    rng = np.random.default_rng(42)
    n = 560
    idx = pd.bdate_range("2022-01-03", periods=n)
    eq_levels = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    ir_levels = 4.0 + np.cumsum(rng.normal(0, 0.03, n))
    levels = pd.DataFrame({"EQ.SPY": eq_levels, "IR.UST.10Y": ir_levels}, index=idx)
    returns = pd.DataFrame({
        "EQ.SPY": np.log(levels["EQ.SPY"]).diff(),
        "IR.UST.10Y": levels["IR.UST.10Y"].diff() * 100.0,
    }).dropna()

    out = run_backfill(_tiny_book(), levels, returns, n_days=50)
    # 50 as-of dates x 2 methods x 3 scopes (2 booked desks + FIRM)
    assert len(out) == 50 * 2 * 3
    assert set(out["method"]) == {"HS", "FHS"}
    assert (out["var"] > 0).all()
    # exception flag is exactly the definition
    manual = out["hpl_next"] < -out["var"]
    assert (out["is_exception"] == manual).all()
    # FIRM hpl equals sum of desk hpls per date/method
    g = out[out["method"] == "HS"].pivot(index="as_of", columns="scope", values="hpl_next")
    np.testing.assert_allclose(g["FIRM"], g["EQUITY"] + g["RATES"], rtol=1e-9)
