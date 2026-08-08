"""Black-Scholes pricing and Greeks for the equity options sleeve.

Hand-rolled (whiteboard rule): d1/d2, prices, delta, gamma, vega; scipy
supplies only the normal distribution. Conventions: vol and rate are decimals
(a 16.5 VIX print is sigma 0.165), maturity in years, vega is dP/dsigma per
1.00 of vol - divide by 100 to quote per vol point. Post-shock implied vol
floors at MIN_IV, the vol-space sibling of the 1bp yield floor; both are
documented limitations, not hidden clamps.
"""

from __future__ import annotations

import numpy as np
from scipy.stats import norm

MIN_IV = 0.01                 # 1 vol point - shocked vols floor here
MIN_TIME_YEARS = 1.0 / 252    # a proxy option is never priced below one trading day


def _d1_d2(spot, strike, vol, t, rate):
    spot = np.asarray(spot, dtype=float)
    vol = np.maximum(np.asarray(vol, dtype=float), MIN_IV)
    t = max(float(t), MIN_TIME_YEARS)
    sq = vol * np.sqrt(t)
    d1 = (np.log(spot / strike) + (rate + 0.5 * vol**2) * t) / sq
    return d1, d1 - sq, t


def bs_price(option_type: str, spot, strike: float, vol, t: float, rate) -> np.ndarray:
    """European call/put on a non-dividend-paying underlier (q = 0 is a
    documented simplification - the stored equity levels are total-return)."""
    d1, d2, t = _d1_d2(spot, strike, vol, t, rate)
    df = np.exp(-np.asarray(rate, dtype=float) * t)
    spot = np.asarray(spot, dtype=float)
    if option_type == "CALL":
        return spot * norm.cdf(d1) - strike * df * norm.cdf(d2)
    if option_type == "PUT":
        return strike * df * norm.cdf(-d2) - spot * norm.cdf(-d1)
    raise ValueError(f"option_type must be CALL or PUT, got {option_type!r}")


def bs_delta(option_type: str, spot, strike: float, vol, t: float, rate) -> np.ndarray:
    d1, _, _ = _d1_d2(spot, strike, vol, t, rate)
    return norm.cdf(d1) if option_type == "CALL" else norm.cdf(d1) - 1.0


def bs_gamma(spot, strike: float, vol, t: float, rate) -> np.ndarray:
    d1, _, t = _d1_d2(spot, strike, vol, t, rate)
    vol = np.maximum(np.asarray(vol, dtype=float), MIN_IV)
    return norm.pdf(d1) / (np.asarray(spot, dtype=float) * vol * np.sqrt(t))


def bs_vega(spot, strike: float, vol, t: float, rate) -> np.ndarray:
    """dP/dsigma per 1.00 of vol (same for calls and puts)."""
    d1, _, t = _d1_d2(spot, strike, vol, t, rate)
    return np.asarray(spot, dtype=float) * norm.pdf(d1) * np.sqrt(t)
