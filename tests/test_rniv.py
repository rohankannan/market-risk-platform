"""Known-answer tests for the RNIV measurement primitives."""

import numpy as np
import pandas as pd
import pytest

from risk_engine.rniv import (
    fill_mask,
    kday_overlapping_shocks,
    lead_lag_correlations,
    vol_damping_ratios,
)
from risk_engine.var import var_es_from_pnl


def _frame(values, start="2024-01-01"):
    idx = pd.bdate_range(start, periods=len(values))
    return pd.DataFrame({"X": values}, index=idx)


def test_kday_shocks_are_rolling_sums():
    r = _frame([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    shocks = kday_overlapping_shocks(r, r.index[-1], k=2, lookback=5)
    assert list(shocks["X"]) == [5.0, 7.0, 9.0, 11.0]           # 4 = lookback - k + 1
    with pytest.raises(ValueError):
        kday_overlapping_shocks(r, r.index[-1], k=1, lookback=5)


def test_fill_mask_respects_per_factor_cap():
    idx = pd.bdate_range("2024-01-01", periods=8)
    levels = pd.DataFrame({
        "A": [1.0, np.nan, np.nan, 1.0, 1.0, 1.0, 1.0, 1.0],    # 2-gap, cap 3 -> filled
        "B": [1.0, np.nan, np.nan, np.nan, np.nan, 1.0, 1.0, 1.0],  # 4-gap, cap 3
    }, index=idx)
    mask = fill_mask(levels, {"A": 3, "B": 3})
    assert mask["A"].sum() == 2 and mask["A"].iloc[1] and mask["A"].iloc[2]
    assert mask["B"].sum() == 3                                  # filled up to the cap only
    assert not mask["B"].iloc[4]                                 # beyond cap stays NaN


def test_vol_damping_ratio_exceeds_one_with_masked_zeros():
    rng = np.random.default_rng(42)
    vals = rng.normal(0, 0.01, 300)
    filled_days = np.zeros(300, dtype=bool)
    filled_days[50:300:7] = True                                 # weekly stale prints
    vals[filled_days] = 0.0                                      # fills imprint zero returns
    r = _frame(vals)
    mask = pd.DataFrame({"X": filled_days}, index=r.index)

    rho = vol_damping_ratios(r, mask, lam=0.94, seed_window=30)
    assert rho["X"] > 1.0

    no_fills = vol_damping_ratios(r, mask & False, lam=0.94, seed_window=30)
    assert no_fills["X"] == pytest.approx(1.0)                   # no mask -> identical series

    short = vol_damping_ratios(r.head(35), mask.head(35), lam=0.94, seed_window=30)
    assert np.isnan(short["X"])                                  # too few obs -> NaN, not noise


def test_lead_lag_correlation_finds_constructed_lag():
    rng = np.random.default_rng(42)
    left = pd.Series(rng.normal(0, 1, 400))
    r = pd.DataFrame({"L": left, "R": left.shift(1)}).dropna()
    r.index = pd.bdate_range("2024-01-01", periods=len(r))
    ll = lead_lag_correlations(r, ["L"], ["R"], lags=(0, 1), window=len(r))
    by_lag = ll.set_index("lag")["corr"]
    assert by_lag[1] == pytest.approx(1.0, abs=1e-9)             # R tomorrow == L today
    assert abs(by_lag[0]) < 0.15


def test_overlapping_kday_var_matches_sqrt_k_for_iid_returns():
    """Under iid daily P&L the sqrt-k rule is exact in expectation - the
    overlapping estimator must land near it (this is the null R1 tests against)."""
    rng = np.random.default_rng(42)
    r = _frame(rng.normal(0, 10_000.0, 600))                     # daily P&L, iid by construction
    daily = var_es_from_pnl(r["X"].tail(500), method="iid")
    shocks = kday_overlapping_shocks(r, r.index[-1], k=10, lookback=500)
    overlap = var_es_from_pnl(shocks["X"], method="iid-overlap", horizon_days=10)
    assert overlap.var == pytest.approx(np.sqrt(10) * daily.var, rel=0.20)
