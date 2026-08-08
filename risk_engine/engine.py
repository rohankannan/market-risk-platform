"""Portfolio revaluation and aggregation.

The engine is pure: it takes a tidy positions frame, current factor levels, and
a scenario shock matrix; it never touches the database. The positions frame
contract (one row per position):

    ticker, desk_code, factor_code, quantity, instrument_type, return_conv,
    coupon (bonds), maturity_years (bonds and options),
    vol_factor_code, rate_factor_code, option_type, moneyness (options; the
    non-applicable columns are NaN/None)

Shocks are in each factor's return convention (LOG factors: log returns;
ABS_BP factors: basis points; ABS vol factors: points). P&L is EXACT under
mode="full": equity/FX = qty * S0 * (exp(r) - 1), bonds are fully repriced,
options fully reprice through Black-Scholes at the shocked spot, vol, and
rate. The linearized path lives only in mode="delta_gamma" - the
risk-theoretical P&L for the PLA test: delta/gamma in the underlier plus
vega in the vol factor, deliberately excluding rho and cross terms, so the
HPL-RTPL gap is real and internally generated.

Equity note (documented model choice): stored equity levels are the ADJUSTED
close, so positions are total-return positions - quantity was struck from the
unadjusted anchor close (where adjusted == unadjusted), and historical as-of
market values follow the dividend-reinvested path.
"""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

from .options import bs_delta, bs_gamma, bs_price, bs_vega
from .pricing import bond_pnl, dollar_convexity, dv01

_LINEAR_TYPES = {"STOCK", "ETF", "FX_SPOT"}
VOL_POINTS_PER_UNIT = 100.0          # vol factors store points; sigma is decimal


def revalue(positions: pd.DataFrame, levels: pd.Series, shocks: pd.DataFrame,
            mode: Literal["full", "delta_gamma"] = "full") -> pd.DataFrame:
    """Scenario P&L matrix (n_scenarios x n_positions), columns keyed by ticker."""
    missing = sorted(set(positions["factor_code"]) - set(shocks.columns))
    if missing:
        raise ValueError(f"shock matrix is missing factors for booked positions: {missing}")
    no_level = sorted(set(positions["factor_code"]) - set(levels.index))
    if no_level:
        raise ValueError(f"levels are missing factors for booked positions: {no_level}")

    out: dict[str, np.ndarray] = {}
    # loop over ~15 positions (not a hot path); each position is vectorized across scenarios
    for pos in positions.itertuples(index=False):
        r = shocks[pos.factor_code].to_numpy(dtype=float)
        lvl = float(levels[pos.factor_code])
        qty = float(pos.quantity)

        if pos.instrument_type in _LINEAR_TYPES:
            if pos.return_conv != "LOG":
                raise ValueError(f"{pos.ticker}: linear position on non-LOG factor "
                                 f"{pos.factor_code} ({pos.return_conv})")
            pnl = qty * lvl * (np.exp(r) - 1.0) if mode == "full" else qty * lvl * r
        elif pos.instrument_type == "GOVT_BOND":
            if pos.return_conv != "ABS_BP":
                raise ValueError(f"{pos.ticker}: bond on non-ABS_BP factor "
                                 f"{pos.factor_code} ({pos.return_conv})")
            y0 = lvl / 100.0                                   # yields stored in percent
            if mode == "full":
                pnl = bond_pnl(pos.coupon, pos.maturity_years, y0, qty, r)
            else:
                d = dv01(pos.coupon, pos.maturity_years, y0, face=qty)         # signed with face
                g = dollar_convexity(pos.coupon, pos.maturity_years, y0, face=qty)
                pnl = -d * r + 0.5 * g * (r / 1e4) ** 2
        elif pos.instrument_type == "OPTION":
            if pos.return_conv != "LOG":
                raise ValueError(f"{pos.ticker}: option underlier on non-LOG factor "
                                 f"{pos.factor_code} ({pos.return_conv})")
            for extra in (pos.vol_factor_code, pos.rate_factor_code):
                if extra not in shocks.columns or extra not in levels.index:
                    raise ValueError(f"{pos.ticker}: option factor {extra!r} missing "
                                     "from shocks or levels")
            sigma0 = float(levels[pos.vol_factor_code]) / VOL_POINTS_PER_UNIT
            rate0 = float(levels[pos.rate_factor_code]) / 100.0    # percent
            strike = float(pos.moneyness) * lvl                    # struck at today's spot
            t = float(pos.maturity_years)
            dvol = shocks[pos.vol_factor_code].to_numpy(dtype=float) / VOL_POINTS_PER_UNIT
            drate = shocks[pos.rate_factor_code].to_numpy(dtype=float) / 1e4    # bp
            if mode == "full":
                base = bs_price(pos.option_type, lvl, strike, sigma0, t, rate0)
                pnl = qty * (bs_price(pos.option_type, lvl * np.exp(r), strike,
                                      sigma0 + dvol, t, rate0 + drate) - base)
            else:
                # RTPL: delta/gamma in spot, vega in vol; rho and cross terms
                # excluded by design - that omission IS the PLA content
                delta = bs_delta(pos.option_type, lvl, strike, sigma0, t, rate0)
                gamma = bs_gamma(lvl, strike, sigma0, t, rate0)
                vega = bs_vega(lvl, strike, sigma0, t, rate0)
                ds = lvl * r                                       # linearized spot move
                pnl = qty * (delta * ds + 0.5 * gamma * ds**2 + vega * dvol)
        else:
            raise ValueError(f"{pos.ticker}: unknown instrument_type {pos.instrument_type!r}")
        out[pos.ticker] = np.asarray(pnl, dtype=float)

    return pd.DataFrame(out, index=shocks.index)


def aggregate(pnl_matrix: pd.DataFrame, positions: pd.DataFrame) -> pd.DataFrame:
    """Desk-level scenario P&L plus a FIRM total column."""
    desk_of = dict(zip(positions["ticker"], positions["desk_code"]))
    unknown = sorted(set(pnl_matrix.columns) - set(desk_of))
    if unknown:
        raise ValueError(f"pnl columns without a booked desk: {unknown}")
    desk_pnl = pnl_matrix.T.groupby(pnl_matrix.columns.map(desk_of).to_numpy()).sum().T
    desk_pnl["FIRM"] = desk_pnl.sum(axis=1)
    return desk_pnl


def component_es(desk_pnl: pd.DataFrame, alpha_es: float = 0.975) -> pd.Series:
    """Euler allocation of firm ES: mean of each desk's P&L over the firm's tail
    scenarios. Sums exactly to firm ES; negative for hedging desks. Pass the
    desk columns only (no FIRM column)."""
    firm = desk_pnl.sum(axis=1)
    q = np.quantile(firm.to_numpy(dtype=float), 1.0 - alpha_es, method="linear")
    tail_idx = firm[firm <= q].index
    return -desk_pnl.loc[tail_idx].mean()
