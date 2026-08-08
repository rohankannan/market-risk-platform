"""Zero-curve bootstrap and key-rate risk for the Treasury book.

The VaR path prices each proxy bond off its own constant-maturity yield (the
documented one-factor mapping); this module supplies the curve view: bootstrap
semiannually-compounded zeros from the par CMT nodes, price bonds as cashflow
strips off the interpolated curve, and measure par key-rate DV01s by bumping
one input node at a time and re-bootstrapping. Hand-rolled per the house rule;
scipy supplies only the root finder.

Conventions: par yields and zeros are decimals, semiannual compounding, linear
interpolation in zero rates between nodes with flat extrapolation outside
(kinked forwards between nodes - named in the model doc). A first node shorter
than one coupon period (the 3M bill) is treated as a zero-coupon rate.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import brentq

# the curve definition for the seeded book: factor code -> node maturity, years
NODE_TENORS = {"IR.UST.3M": 0.25, "IR.UST.2Y": 2.0, "IR.UST.5Y": 5.0,
               "IR.UST.10Y": 10.0, "IR.UST.30Y": 30.0}
FREQ = 2                      # semiannual coupons and compounding
BRACKET = 0.10                # zero solved within par +/- 10% (absolute rate)
KRD_BUMP = 1e-4               # 1bp central-difference bump on one par node


@dataclass(frozen=True)
class ZeroCurve:
    """Node maturities (years) and semiannually-compounded zero rates."""
    nodes: np.ndarray
    zeros: np.ndarray

    def zero(self, t) -> np.ndarray:
        return np.interp(np.asarray(t, dtype=float), self.nodes, self.zeros)

    def df(self, t) -> np.ndarray:
        t = np.asarray(t, dtype=float)
        return (1.0 + self.zero(t) / FREQ) ** (-FREQ * t)


def _coupon_times(maturity_years: float) -> np.ndarray:
    n = int(round(FREQ * maturity_years))
    return np.arange(1, n + 1) / FREQ


def _price_par_bond(curve: ZeroCurve, coupon: float, maturity: float) -> float:
    """Per-unit-face price of a semiannual par-coupon bond off the curve."""
    times = _coupon_times(maturity)
    dfs = curve.df(times)
    return float(coupon / FREQ * dfs.sum() + dfs[-1])


def bootstrap_zero_curve(par_yields: pd.Series) -> ZeroCurve:
    """Sequential bootstrap from par yields indexed by maturity in years.

    Each node's zero is solved (brentq) so the node's par bond prices at 1.0
    on the curve built so far, with zeros between nodes coming from the same
    interpolation the final curve uses - the standard bootstrap-with-
    interpolation fixed point, one dimension per node.
    """
    mats = np.asarray(sorted(par_yields.index), dtype=float)
    ys = par_yields.sort_index().to_numpy(dtype=float)
    zeros = np.empty_like(ys)
    for i, (t, y) in enumerate(zip(mats, ys)):
        if t < 1.0 / FREQ:                       # bill node: already a zero rate
            zeros[i] = y
            continue

        def objective(z: float, i: int = i, t: float = t, y: float = y) -> float:
            trial = ZeroCurve(mats[: i + 1], np.append(zeros[:i], z))
            return _price_par_bond(trial, y, t) - 1.0

        zeros[i] = brentq(objective, y - BRACKET, y + BRACKET, xtol=1e-12)
    return ZeroCurve(mats, zeros)


def price_bond_on_curve(curve: ZeroCurve, coupon: float, maturity_years: float,
                        face: float = 100.0) -> float:
    return face * _price_par_bond(curve, coupon, maturity_years)


def key_rate_dv01s(par_yields: pd.Series, coupon: float, maturity_years: float,
                   face: float) -> pd.Series:
    """Par key-rate DV01s: dollars of P&L per +1bp bump of ONE par input node,
    curve re-bootstrapped per bump (central difference). Signed with face;
    the sum across nodes reprices a parallel par shift to first order."""
    out = {}
    for node in par_yields.index:
        up, dn = par_yields.copy(), par_yields.copy()
        up[node] += KRD_BUMP
        dn[node] -= KRD_BUMP
        p_up = price_bond_on_curve(bootstrap_zero_curve(up), coupon, maturity_years, face)
        p_dn = price_bond_on_curve(bootstrap_zero_curve(dn), coupon, maturity_years, face)
        out[node] = (p_dn - p_up) / 2.0
    return pd.Series(out)


def curve_dv01(par_yields: pd.Series, coupon: float, maturity_years: float,
               face: float) -> float:
    """Total curve DV01: +/-1bp parallel shift of every par node, central diff."""
    up = bootstrap_zero_curve(par_yields + KRD_BUMP)
    dn = bootstrap_zero_curve(par_yields - KRD_BUMP)
    return (price_bond_on_curve(dn, coupon, maturity_years, face)
            - price_bond_on_curve(up, coupon, maturity_years, face)) / 2.0
