"""VaR / ES estimators and EWMA volatility filtering.

Hand-rolled by design (see README): anything an interviewer could ask you to
derive lives here in plain numpy; scipy is used only for distribution functions.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class VaRESResult:
    var: float          # positive dollars of potential loss
    es: float           # positive dollars, tail mean beyond the ES quantile
    alpha_var: float
    alpha_es: float
    method: str
    n_scenarios: int
    horizon_days: int

    def scaled(self, horizon_days: int) -> VaRESResult:
        """sqrt-of-time scaling to a longer horizon (iid caveat in the model doc)."""
        k = np.sqrt(horizon_days / self.horizon_days)
        return VaRESResult(self.var * k, self.es * k, self.alpha_var, self.alpha_es,
                           self.method, self.n_scenarios, horizon_days)


def var_es_from_pnl(pnl, alpha_var: float = 0.99, alpha_es: float = 0.975,
                    method: str = "hs", horizon_days: int = 1) -> VaRESResult:
    """Empirical VaR and ES from a vector of scenario P&Ls (signed, losses negative).

    VaR_a  = -Quantile_{1-a}(P&L), linear interpolation (at n=500 and a=0.99 this
    interpolates between the 5th and 6th worst losses - report those order
    statistics when asked).
    ES_a   = -mean(P&L | P&L <= Quantile_{1-a}(P&L)).

    Reference facts encoded as unit tests: for N(0, sigma), VaR99 = 2.326*sigma
    and ES97.5 = 2.338*sigma - Basel's calibration-neutral swap.
    """
    arr = np.asarray(pnl, dtype=float)
    arr = arr[~np.isnan(arr)]
    if arr.size < 100:
        raise ValueError(f"need >=100 scenarios for a stable tail estimate, got {arr.size}")
    q_var = np.quantile(arr, 1.0 - alpha_var, method="linear")
    q_es = np.quantile(arr, 1.0 - alpha_es, method="linear")
    tail = arr[arr <= q_es]
    return VaRESResult(
        var=float(-q_var),
        es=float(-tail.mean()),
        alpha_var=alpha_var,
        alpha_es=alpha_es,
        method=method,
        n_scenarios=int(arr.size),
        horizon_days=horizon_days,
    )


def ewma_volatility(returns: pd.DataFrame, lam: float = 0.94,
                    seed_window: int = 30) -> pd.DataFrame:
    """Per-factor EWMA conditional volatility, aligned to `returns.index`.

    sigma^2_t = lam * sigma^2_{t-1} + (1 - lam) * r^2_{t-1}

    The first `seed_window` rows carry the seed value (population variance of
    the first `seed_window` returns). The recursion is inherently sequential -
    the explicit loop is deliberate and whiteboard-derivable; lam=0.94 is the
    RiskMetrics convention (half-life ~ 11 days), a convention, not an estimate.
    """
    if not 0.0 < lam < 1.0:
        raise ValueError(f"lambda must be in (0,1), got {lam}")
    r = returns.to_numpy(dtype=float)
    n, k = r.shape
    if n <= seed_window:
        raise ValueError(f"need more than seed_window={seed_window} observations, got {n}")
    sig2 = np.empty((n, k))
    seed = np.nanvar(r[:seed_window], axis=0, ddof=0)
    sig2[:seed_window] = seed
    r2 = np.square(np.nan_to_num(r, nan=0.0))
    for t in range(seed_window, n):
        sig2[t] = lam * sig2[t - 1] + (1.0 - lam) * r2[t - 1]
    return pd.DataFrame(np.sqrt(sig2), index=returns.index, columns=returns.columns)


def ewma_vol_forecast(returns: pd.DataFrame, lam: float = 0.94,
                      seed_window: int = 30) -> pd.Series:
    """Next-day vol forecast per factor: one more step of the recursion past the last return."""
    vols = ewma_volatility(returns, lam=lam, seed_window=seed_window)
    last_sig2 = np.square(vols.iloc[-1].to_numpy(dtype=float))
    last_r2 = np.square(np.nan_to_num(returns.iloc[-1].to_numpy(dtype=float), nan=0.0))
    return pd.Series(np.sqrt(lam * last_sig2 + (1.0 - lam) * last_r2), index=returns.columns)


def ewma_vol_forecast_series(returns: pd.DataFrame, lam: float = 0.94,
                             seed_window: int = 30) -> pd.DataFrame:
    """Per-date next-day vol forecast, aligned to `returns.index`.

    Row t holds sigma_{t+1} = sqrt(lam * sigma^2_t + (1-lam) * r^2_t) - the vol the
    FHS devol/revol uses when running AS-OF date t. Identity (unit-tested):
    forecast.iloc[t] == ewma_volatility(...).iloc[t+1] wherever both exist.
    """
    vols = ewma_volatility(returns, lam=lam, seed_window=seed_window)
    sig2 = np.square(vols.to_numpy(dtype=float))
    r2 = np.square(np.nan_to_num(returns.to_numpy(dtype=float), nan=0.0))
    return pd.DataFrame(np.sqrt(lam * sig2 + (1.0 - lam) * r2),
                        index=returns.index, columns=returns.columns)
