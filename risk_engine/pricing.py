"""Instrument revaluation.

Rates desk: constant-maturity UST par-bond proxies priced with the closed-form
semiannual fixed-coupon formula - full revaluation, no curve bootstrap, no swaps
(deliberate MVP scope; see docs/model_doc.md limitations).
"""

from __future__ import annotations

import numpy as np


def bond_price(coupon: float, maturity_years: float, ytm: float, face: float = 100.0) -> float:
    """Price of a semiannual fixed-coupon bond.

    P(y) = face * (c/y) * (1 - (1 + y/2)^(-2T)) + face * (1 + y/2)^(-2T)

    Sanity property (unit-tested): coupon == ytm  =>  price == face exactly.
    Near y=0 the closed form is replaced by its limit: face * (1 + c*T).
    """
    n_periods = int(round(2 * maturity_years))
    if n_periods <= 0:
        raise ValueError(f"maturity_years={maturity_years} gives no coupon periods")
    if abs(ytm) < 1e-9:
        return face * (1.0 + coupon * maturity_years)
    y2 = ytm / 2.0
    df = (1.0 + y2) ** (-n_periods)
    return face * (coupon / ytm) * (1.0 - df) + face * df


def dv01(coupon: float, maturity_years: float, ytm: float, face: float = 1_000_000.0,
         bump: float = 5e-5) -> float:
    """Dollar value of one basis point, by central-difference bump-and-reprice.

    Returns dollars of P&L per +1bp yield move (positive for a long bond).
    Reference check (unit-tested): 10Y 4% par bond ~ $817.5 per $1M face.
    """
    p_dn = bond_price(coupon, maturity_years, ytm - bump, face)
    p_up = bond_price(coupon, maturity_years, ytm + bump, face)
    return (p_dn - p_up) / (2.0 * bump) * 1e-4


def dollar_convexity(coupon: float, maturity_years: float, ytm: float, face: float = 1_000_000.0,
                     bump: float = 5e-5) -> float:
    """Second-order price sensitivity: d2P/dy2 by central difference (dollars per unit yield^2).

    Used by the risk-theoretical P&L approximation:
    P&L_dg = -DV01_bp * dy_bp + 0.5 * dollar_convexity * dy^2   (dy in decimal).
    """
    p_0 = bond_price(coupon, maturity_years, ytm, face)
    p_dn = bond_price(coupon, maturity_years, ytm - bump, face)
    p_up = bond_price(coupon, maturity_years, ytm + bump, face)
    return (p_up + p_dn - 2.0 * p_0) / (bump**2)


def equity_fx_pnl(quantity_units: float, level: float, log_shock: np.ndarray) -> np.ndarray:
    """Exact (not linearized) P&L for a linear spot position under log shocks.

    P&L = qty * S0 * (exp(r) - 1). The linear approximation qty*S0*r lives only
    in the risk-theoretical P&L (pla.py), so the HPL-RTPL gap is real and
    internally generated.
    """
    return quantity_units * level * (np.exp(log_shock) - 1.0)


def _bond_price_vec(coupon: float, maturity_years: float, ytm: np.ndarray,
                    face: float) -> np.ndarray:
    """Vectorized closed-form price over an array of yields (backfill hot path).

    Same formula as bond_price; unit-tested for exact agreement with the scalar
    version. Callers guarantee ytm > 0 (bond_pnl floors at 1bp).
    """
    y = np.asarray(ytm, dtype=float)
    n_periods = int(round(2 * maturity_years))
    y2 = y / 2.0
    df = (1.0 + y2) ** (-n_periods)
    return face * (coupon / y) * (1.0 - df) + face * df


def bond_pnl(coupon: float, maturity_years: float, ytm: float, face: float,
             shock_bp: np.ndarray) -> np.ndarray:
    """Full-revaluation P&L for a par-bond proxy under yield shocks in basis points.

    Yields are floored at 1bp after shocking (documented limitation).
    """
    base = bond_price(coupon, maturity_years, ytm, face)
    shocked_y = np.maximum(ytm + np.asarray(shock_bp, dtype=float) / 1e4, 1e-4)
    return _bond_price_vec(coupon, maturity_years, shocked_y, face) - base
