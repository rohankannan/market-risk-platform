"""Known-answer tests for the VaR/ES estimators and EWMA filtering."""

import numpy as np
import pandas as pd
import pytest

from risk_engine.factors import build_scenarios_fhs, build_scenarios_hs
from risk_engine.var import ewma_volatility, var_es_from_pnl


def test_hs_var_normal_known_answer():
    """99% VaR of N(0, 1%) returns on a $1M position = 2.3263 sigma = $23,263."""
    rng = np.random.default_rng(42)
    pnl = 1_000_000 * rng.normal(0.0, 0.01, size=10_000)
    res = var_es_from_pnl(pnl)
    assert res.var == pytest.approx(23_263, rel=0.03)


def test_es_975_vs_var_99_basel_calibration():
    """For a normal distribution ES97.5 = 2.338 sigma vs VaR99 = 2.326 sigma:
    Basel's move to ES was calibration-neutral by design. Ratio ~ 1.005."""
    rng = np.random.default_rng(42)
    pnl = 1_000_000 * rng.normal(0.0, 0.01, size=10_000)
    res = var_es_from_pnl(pnl)
    assert res.es / res.var == pytest.approx(1.005, abs=0.03)


def test_ewma_recursion_exact():
    """The implementation must reproduce the hand-run recursion exactly."""
    r = np.array([0.010, -0.020, 0.015, 0.005, -0.010])
    lam, seed_window = 0.94, 2
    returns = pd.DataFrame({"X": r})
    vols = ewma_volatility(returns, lam=lam, seed_window=seed_window)

    sig2 = np.empty_like(r)
    sig2[:seed_window] = np.var(r[:seed_window], ddof=0)
    for t in range(seed_window, len(r)):
        sig2[t] = lam * sig2[t - 1] + (1 - lam) * r[t - 1] ** 2

    np.testing.assert_allclose(vols["X"].to_numpy(), np.sqrt(sig2), rtol=1e-12)


def test_fhs_reduces_to_hs_under_constant_vol():
    """With a flat vol path and forecast equal to that vol, devol/revol is the
    identity: FHS scenarios must equal the HS scenarios exactly."""
    rng = np.random.default_rng(42)
    idx = pd.bdate_range("2023-01-02", periods=600)
    returns = pd.DataFrame({"X": rng.normal(0.0, 0.01, size=600)}, index=idx)
    vols = pd.DataFrame(0.01, index=idx, columns=["X"])
    forecast = pd.Series({"X": 0.01})

    hs = build_scenarios_hs(returns, idx[-1], lookback=500)
    fhs = build_scenarios_fhs(returns, vols, forecast, idx[-1], lookback=500)
    pd.testing.assert_frame_equal(hs, fhs, rtol=1e-12)

    v_hs = var_es_from_pnl(1_000_000 * hs["X"].to_numpy())
    v_fhs = var_es_from_pnl(1_000_000 * fhs["X"].to_numpy())
    assert v_fhs.var == pytest.approx(v_hs.var, rel=1e-9)


def test_sqrt_time_scaling():
    rng = np.random.default_rng(42)
    res = var_es_from_pnl(rng.normal(0, 10_000, 1_000))
    scaled = res.scaled(10)
    assert scaled.var == pytest.approx(res.var * np.sqrt(10), rel=1e-12)
    assert scaled.horizon_days == 10
