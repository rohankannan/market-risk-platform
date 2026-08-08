"""GARCH(1,1) volatility - the challenger filter for filtered historical simulation.

    sigma^2_t = omega + alpha * r^2_{t-1} + beta * sigma^2_{t-1}

Hand-rolled Gaussian quasi-MLE (whiteboard rule); scipy supplies only the
optimizer. Returns are standardized to unit variance before fitting so one set
of bounds serves log-return and basis-point factors alike; omega is rescaled
back afterwards. The champion EWMA filter is the IGARCH boundary case
(omega = 0, alpha = 1 - lambda, beta = lambda) - unit-tested equivalence -
which is exactly the model-choice conversation: EWMA imposes infinite
persistence, GARCH estimates it and mean-reverts to an unconditional level.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import minimize

FIT_BOUNDS = ((1e-10, 10.0), (0.0, 0.5), (0.0, 0.999))   # omega*, alpha, beta (standardized)
FIT_START = (0.05, 0.05, 0.90)
STATIONARITY_CAP = 0.9999          # alpha + beta must stay below this
_PENALTY = 1e10


@dataclass(frozen=True)
class GarchFit:
    omega: float                   # in return-variance units (rescaled from the fit)
    alpha: float
    beta: float
    loglik: float
    n_obs: int
    converged: bool

    @property
    def persistence(self) -> float:
        return self.alpha + self.beta

    @property
    def uncond_var(self) -> float:
        return self.omega / (1.0 - self.persistence)

    @property
    def half_life_days(self) -> float:
        """Half-life of a variance shock: ln(0.5) / ln(alpha + beta)."""
        return float(np.log(0.5) / np.log(self.persistence))


def _neg_loglik(params: np.ndarray, r2: np.ndarray, seed_var: float) -> float:
    omega, alpha, beta = params
    if alpha + beta >= STATIONARITY_CAP:
        return _PENALTY * (1.0 + alpha + beta)
    sig2 = seed_var
    total = 0.0
    for t in range(1, r2.size):
        sig2 = omega + alpha * r2[t - 1] + beta * sig2
        total += np.log(sig2) + r2[t] / sig2
    return 0.5 * total


def fit_garch(returns: pd.Series) -> GarchFit:
    """Gaussian QMLE on the full series (NaNs dropped); the recursion seeds
    with the sample second moment and the likelihood starts at the second
    observation."""
    r = returns.dropna().to_numpy(dtype=float)
    if r.size < 200:
        raise ValueError(f"need >= 200 observations to fit GARCH, got {r.size}")
    scale = float(np.std(r))
    if scale <= 0:
        raise ValueError("degenerate series: zero variance")
    z2 = np.square(r / scale)
    res = minimize(_neg_loglik, FIT_START, args=(z2, float(z2.mean())),
                   method="L-BFGS-B", bounds=FIT_BOUNDS)
    omega_std, alpha, beta = (float(v) for v in res.x)
    # res.fun is the reduced NLL of the STANDARDIZED series; restore the raw-
    # scale Gaussian log-likelihood (Jacobian of r -> r/scale plus the dropped
    # constant) so the value is comparable across factors and usable for AIC/LR.
    # It covers the n-1 terms after the seed observation.
    n_ll = r.size - 1
    loglik = float(-(res.fun + n_ll * np.log(scale) + 0.5 * n_ll * np.log(2.0 * np.pi)))
    return GarchFit(omega=omega_std * scale**2, alpha=alpha, beta=beta,
                    loglik=loglik, n_obs=int(r.size),
                    converged=bool(res.success and alpha + beta < STATIONARITY_CAP))


def garch_volatility(returns: pd.DataFrame, fits: Mapping[str, GarchFit],
                     seed_window: int = 30) -> pd.DataFrame:
    """Per-factor conditional vol under fitted GARCH params, aligned to
    `returns.index` with the same seeding convention as ewma_volatility: the
    first `seed_window` rows carry the seed (population variance of that
    window), then the recursion runs forward."""
    r = returns.to_numpy(dtype=float)
    n, k = r.shape
    if n <= seed_window:
        raise ValueError(f"need more than seed_window={seed_window} observations, got {n}")
    r2 = np.square(np.nan_to_num(r, nan=0.0))
    sig2 = np.empty((n, k))
    seed = np.nanvar(r[:seed_window], axis=0, ddof=0)
    sig2[:seed_window] = seed
    params = np.array([[fits[c].omega, fits[c].alpha, fits[c].beta]
                       for c in returns.columns])
    omega, alpha, beta = params[:, 0], params[:, 1], params[:, 2]
    for t in range(seed_window, n):
        sig2[t] = omega + alpha * r2[t - 1] + beta * sig2[t - 1]
    return pd.DataFrame(np.sqrt(sig2), index=returns.index, columns=returns.columns)


def garch_vol_forecast_series(returns: pd.DataFrame, fits: Mapping[str, GarchFit],
                              seed_window: int = 30) -> pd.DataFrame:
    """Row t holds sigma_{t+1} = sqrt(omega + alpha r^2_t + beta sigma^2_t) -
    the same shifted-vol identity the EWMA forecast series satisfies."""
    vols = garch_volatility(returns, fits, seed_window=seed_window)
    sig2 = np.square(vols.to_numpy(dtype=float))
    r2 = np.square(np.nan_to_num(returns.to_numpy(dtype=float), nan=0.0))
    params = np.array([[fits[c].omega, fits[c].alpha, fits[c].beta]
                       for c in returns.columns])
    omega, alpha, beta = params[:, 0], params[:, 1], params[:, 2]
    return pd.DataFrame(np.sqrt(omega + alpha * r2 + beta * sig2),
                        index=returns.index, columns=returns.columns)
