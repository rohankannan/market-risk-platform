"""Known-answer tests for the GARCH(1,1) challenger filter."""

import numpy as np
import pandas as pd
import pytest

from risk_engine.garch import (
    GarchFit,
    fit_garch,
    garch_vol_forecast_series,
    garch_volatility,
)
from risk_engine.var import ewma_volatility

TRUE = {"omega": 2e-6, "alpha": 0.08, "beta": 0.90}


def _simulate(n=4000, seed=42):
    rng = np.random.default_rng(seed)
    r = np.empty(n)
    sig2 = TRUE["omega"] / (1 - TRUE["alpha"] - TRUE["beta"])
    for t in range(n):
        r[t] = np.sqrt(sig2) * rng.standard_normal()
        sig2 = TRUE["omega"] + TRUE["alpha"] * r[t] ** 2 + TRUE["beta"] * sig2
    return pd.Series(r, index=pd.bdate_range("2010-01-01", periods=n))


def test_mle_recovers_simulated_parameters():
    fit = fit_garch(_simulate())
    assert fit.converged
    assert fit.alpha == pytest.approx(TRUE["alpha"], abs=0.03)
    assert fit.beta == pytest.approx(TRUE["beta"], abs=0.05)
    assert fit.persistence == pytest.approx(0.98, abs=0.03)
    true_uncond = TRUE["omega"] / (1 - TRUE["alpha"] - TRUE["beta"])
    assert fit.uncond_var == pytest.approx(true_uncond, rel=0.30)


def test_loglik_is_the_raw_scale_gaussian_likelihood():
    """The stored loglik must equal the hand-computed Gaussian log-likelihood
    of the RAW returns under the fitted params (standardization Jacobian and
    constant restored), so AIC/LR comparisons across factors are legitimate."""
    r = _simulate(1000)
    fit = fit_garch(r)
    x = r.to_numpy()
    sig2 = float(np.mean(x**2))                # seed = uncentered second moment, as fitted
    ll = 0.0
    for t in range(1, x.size):
        sig2 = fit.omega + fit.alpha * x[t - 1] ** 2 + fit.beta * sig2
        ll += -0.5 * (np.log(2 * np.pi) + np.log(sig2) + x[t] ** 2 / sig2)
    assert fit.loglik == pytest.approx(ll, rel=1e-9)


def test_iid_series_fits_no_arch_effects():
    rng = np.random.default_rng(42)
    r = pd.Series(rng.normal(0, 0.01, 3000),
                  index=pd.bdate_range("2012-01-01", periods=3000))
    fit = fit_garch(r)
    assert fit.alpha < 0.03                                     # nothing to react to
    assert np.sqrt(fit.uncond_var) == pytest.approx(0.01, rel=0.10)


def test_forecast_series_is_shifted_conditional_vol():
    r = pd.DataFrame({"X": _simulate(1000)})
    fits = {"X": GarchFit(2e-6, 0.08, 0.90, 0.0, 1000, True)}
    vols = garch_volatility(r, fits, seed_window=30)
    fc = garch_vol_forecast_series(r, fits, seed_window=30)
    np.testing.assert_allclose(fc["X"].to_numpy()[30:-1], vols["X"].to_numpy()[31:],
                               rtol=1e-12)


def test_ewma_is_the_igarch_boundary_case():
    """omega=0, alpha=1-lambda, beta=lambda reproduces the champion EWMA
    recursion exactly - same seeding, same filter, bit-for-bit."""
    rng = np.random.default_rng(42)
    r = pd.DataFrame({"X": rng.normal(0, 0.01, 500), "Y": rng.normal(0, 5.0, 500)},
                     index=pd.bdate_range("2020-01-01", periods=500))
    lam = 0.94
    fits = {c: GarchFit(0.0, 1 - lam, lam, 0.0, 500, True) for c in r.columns}
    got = garch_volatility(r, fits, seed_window=30)
    want = ewma_volatility(r, lam=lam, seed_window=30)
    np.testing.assert_allclose(got.to_numpy(), want.to_numpy(), rtol=1e-13)


def test_fit_rejects_short_or_degenerate_series():
    with pytest.raises(ValueError):
        fit_garch(pd.Series(np.random.default_rng(1).normal(size=100)))
    with pytest.raises(ValueError):
        fit_garch(pd.Series(np.zeros(500)))
