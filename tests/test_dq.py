"""Tests for the pure data-quality checks and scenario application."""

import numpy as np
import pandas as pd
import pytest

from risk_engine.dq import check_bounds, check_outliers, check_staleness, has_block
from risk_engine.stress import apply_scenario


def _calm_returns(n=100, sigma=0.005, seed=42):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"EQ.X": rng.normal(0, sigma, n), "IR.Y": rng.normal(0, 3.0, n)})


def test_outlier_flags_crash_day_but_not_calm_day():
    conv = {"EQ.X": "LOG", "IR.Y": "ABS_BP"}
    calm = _calm_returns()
    assert check_outliers(calm, conv) == []

    crash = calm.copy()
    crash.iloc[-1, crash.columns.get_loc("EQ.X")] = -0.12       # -12% on ~0.5% vol
    crash.iloc[-1, crash.columns.get_loc("IR.Y")] = -110.0      # 110bp rally
    issues = check_outliers(crash, conv)
    assert {i["factor_code"] for i in issues} == {"EQ.X", "IR.Y"}
    assert all(i["severity"] == "WARN" for i in issues)          # flag, never delete


def test_outlier_needs_both_vol_and_absolute_gates():
    """A 6-sigma move that is still under 3% absolute must NOT flag (quiet names
    twitch; the absolute gate keeps noise out)."""
    conv = {"EQ.X": "LOG"}
    r = pd.DataFrame({"EQ.X": np.full(100, 0.001)})
    r.iloc[-1] = 0.02        # huge vs 0.1% vol, but < 3% absolute
    assert check_outliers(r, conv) == []


def test_staleness_only_on_price_like_factors():
    idx = pd.bdate_range("2026-01-01", periods=10)
    lv = pd.DataFrame({"EQ.X": 100.0, "IR.Y": 4.25}, index=idx)   # both constant
    issues = check_staleness(lv, {"EQ.X": "PRICE", "IR.Y": "YIELD"})
    assert [i["factor_code"] for i in issues] == ["EQ.X"]         # yields may sit still


def test_bounds_catch_unit_errors():
    row = pd.Series({"IR.Y": 425.0, "EQ.X": 100.0, "FX.Z": 1.10})  # yield in bp not %
    issues = check_bounds(row, {"IR.Y": "YIELD", "EQ.X": "PRICE", "FX.Z": "FX_RATE"})
    assert [i["factor_code"] for i in issues] == ["IR.Y"]
    assert has_block(issues)


def test_bounds_fx_median_guard():
    med = pd.Series({"FX.Z": 1.10})
    ok = check_bounds(pd.Series({"FX.Z": 1.20}), {"FX.Z": "FX_RATE"}, med)
    bad = check_bounds(pd.Series({"FX.Z": 2.00}), {"FX.Z": "FX_RATE"}, med)  # +82% vs median
    assert ok == [] and [i["factor_code"] for i in bad] == ["FX.Z"]


def test_apply_scenario_known_answer():
    book = pd.DataFrame([{
        "ticker": "SPY", "desk_code": "EQUITY", "factor_code": "EQ.SPY", "quantity": 100.0,
        "instrument_type": "ETF", "return_conv": "LOG", "coupon": None, "maturity_years": None,
    }])
    levels = pd.Series({"EQ.SPY": 500.0, "IR.UST.10Y": 4.0})
    pnl = apply_scenario(book, levels, pd.Series({"EQ.SPY": np.log(0.8)}))
    assert pnl["EQUITY"] == pytest.approx(100 * 500 * (0.8 - 1.0), rel=1e-12)
    assert pnl["FIRM"] == pytest.approx(pnl["EQUITY"], rel=1e-12)
    # unshocked factors are zero-filled: shocking nothing = zero P&L
    flat = apply_scenario(book, levels, pd.Series({"IR.UST.10Y": 100.0}))
    assert flat["FIRM"] == pytest.approx(0.0, abs=1e-9)
