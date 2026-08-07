"""Presentation helpers shared by the pages: money/percent formatting, the
desk identity palette, and the two matplotlib figures.

No Streamlit imports here - everything is a pure function of API payloads,
so it unit-tests headlessly.
"""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter

# Desk identity colors (spec section 7); red/green stay reserved for P&L sign
# and limit status.
DESK_COLORS = {"RATES": "#5B8DEF", "FX": "#C08BF0", "EQUITY": "#3EBFA5", "FIRM": "#444444"}
PNL_POS, PNL_NEG = "#3E9C55", "#C64545"
EXCEPTION_DOT = "#C61A1A"
VAR_LINES = {"hs": ("#1B2A4A", "-"), "fhs": ("#5B8DEF", "--")}


def fmt_usd(x: float | None) -> str:
    if x is None:
        return "-"
    sign = "-" if x < 0 else ""
    a = abs(x)
    if a >= 1e6:
        return f"{sign}${a / 1e6:,.2f}M"
    if a >= 1e3:
        return f"{sign}${a / 1e3:,.0f}k"
    return f"{sign}${a:,.0f}"


def fmt_pct(x: float | None, digits: int = 1) -> str:
    return "-" if x is None else f"{x * 100:.{digits}f}%"


def _money_axis(ax) -> None:
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _pos: fmt_usd(v)))


def history_frame(points: list[dict]) -> pd.DataFrame:
    """History points -> date-indexed frame with negated VaR columns for
    plotting (VaR arrives as positive loss; on a P&L axis the loss threshold
    is -VaR)."""
    df = pd.DataFrame(points)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    for m in ("var_hs", "var_fhs"):
        df[f"neg_{m}"] = -df[m]
    return df


def pnl_vs_var_figure(points: list[dict], models: tuple[str, ...] = ("hs", "fhs")) -> Figure:
    """Daily P&L bars against -VaR lines, exception days dotted."""
    df = history_frame(points)
    fig, ax = plt.subplots(figsize=(11, 4))
    pnl = df["pnl"].astype(float).fillna(0.0)
    ax.bar(df.index, pnl, width=1.0, alpha=0.6, label="P&L (hypothetical)",
           color=[PNL_POS if v >= 0 else PNL_NEG for v in pnl])
    for m in models:
        color, ls = VAR_LINES[m]
        ax.plot(df.index, df[f"neg_var_{m}"], color=color, ls=ls, lw=1.2,
                label=f"-VaR99 {m.upper()}")
        exc = df[df[f"exception_{m}"]]
        if len(exc):
            ax.scatter(exc.index, exc["pnl"], color=EXCEPTION_DOT, zorder=5, s=28,
                       label=f"{m.upper()} exception")
    ax.axhline(0.0, color="#999999", lw=0.6)
    _money_axis(ax)
    ax.legend(loc="lower left", fontsize=8, ncols=2)
    fig.autofmt_xdate()
    fig.tight_layout()
    return fig


def scenario_bars_figure(results: list[dict]) -> Figure:
    """Grouped per-desk scenario P&L with a firm-total diamond per scenario."""
    codes = [r["scenario_code"] for r in results]
    desks = sorted({d for r in results for d in r["impacts"]} - {"FIRM"})
    x = np.arange(len(codes))
    n = max(len(desks), 1)
    width = 0.8 / n
    fig, ax = plt.subplots(figsize=(11, 4))
    for i, desk in enumerate(desks):
        off = (i - (n - 1) / 2) * width
        ax.bar(x + off, [r["impacts"].get(desk, 0.0) for r in results], width=width,
               label=desk, color=DESK_COLORS.get(desk, "#888888"))
    ax.scatter(x, [r["firm_impact"] for r in results], marker="D", s=40, zorder=5,
               color=DESK_COLORS["FIRM"], label="FIRM total")
    ax.axhline(0.0, color="#999999", lw=0.6)
    ax.set_xticks(x, codes, rotation=20, ha="right")
    _money_axis(ax)
    ax.legend(fontsize=8, ncols=2)
    fig.tight_layout()
    return fig
