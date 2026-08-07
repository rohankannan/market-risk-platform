"""Out-of-sample VaR backfill CLI: history -> daily VaR/HPL -> backtest stats + chart.

    python -m risk.jobs.backfill [--days 750] [--out data/derived/backfill_var.parquet]
                                 [--chart docs/img/backtest_firm.png]

DB-free by design (results are regenerable from the committed snapshot); the EOD
batch will reuse run_backfill to fill risk_results/pnl/backtest_exceptions once
the database path lands (milestone 3).
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import pandas as pd
import yaml

from risk.jobs.seed import build_seed_bundle, to_positions_frame
from risk_engine.backfill import run_backfill
from risk_engine.backtest import (
    basel_traffic_light,
    christoffersen_conditional_coverage,
    kupiec_pof,
)
from risk_engine.config import DEFAULT_CONFIG
from risk_engine.factors import align_levels, to_returns


def load_inputs(snapshot: str, portfolio: str):
    cfg = yaml.safe_load(open(portfolio))
    snap = pd.read_parquet(snapshot)
    bundle = build_seed_bundle(cfg, snap)
    book = to_positions_frame(bundle)
    levels_raw = snap.pivot(index="obs_date", columns="factor_code", values="value")
    ffill_limits = {f["code"]: int(f.get("ffill_limit", 3)) for f in cfg["factors"]}
    levels, ffilled = align_levels(levels_raw, ffill_limits)
    conv = {f["code"]: f["conv"] for f in cfg["factors"]}
    returns = to_returns(levels, conv).dropna()
    levels = levels.loc[returns.index]
    return book, levels, returns, ffilled


def print_backtest_stats(results: pd.DataFrame, p: float) -> pd.DataFrame:
    rows = []
    for method, g in results[results["scope"] == "FIRM"].groupby("method"):
        g = g.sort_values("as_of")
        n, x = len(g), int(g["is_exception"].sum())
        pof = kupiec_pof(x, n, p=p)
        cc = christoffersen_conditional_coverage(g["is_exception"].to_numpy(), p=p)
        tl = basel_traffic_light(int(g["is_exception"].tail(250).sum()))
        rows.append({
            "method": method, "days": n, "exceptions": x, "expected": round(n * p, 1),
            "kupiec_p": round(pof.p_value, 3), "christoffersen_cc_p": round(cc.p_value, 3),
            "zone_250d": tl.zone, "multiplier": tl.multiplier,
        })
    table = pd.DataFrame(rows)
    print(table.to_string(index=False))
    return table


def make_chart(results: pd.DataFrame, out_path: str) -> None:
    """Firm clean P&L vs -VaR for HS and FHS with exception markers - the README chart."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    firm = results[results["scope"] == "FIRM"]
    hs = firm[firm["method"] == "HS"].set_index("as_of").sort_index()
    fhs = firm[firm["method"] == "FHS"].set_index("as_of").sort_index()

    fig, ax = plt.subplots(figsize=(13, 5.5), dpi=150)
    ax.bar(hs.index, hs["hpl_next"], width=1.0, color="#9aa7b8", alpha=0.55,
           linewidth=0, label="daily clean P&L (t+1)")
    ax.plot(hs.index, -hs["var"], color="#c0392b", lw=1.3, label="-VaR 99% historical sim")
    ax.plot(fhs.index, -fhs["var"], color="#1f6f8b", lw=1.3, label="-VaR 99% filtered HS (EWMA)")
    exc_hs = hs[hs["is_exception"]]
    exc_fhs = fhs[fhs["is_exception"]]
    ax.scatter(exc_hs.index, exc_hs["hpl_next"], color="#c0392b", s=26, zorder=5,
               label=f"HS exceptions ({len(exc_hs)})")
    ax.scatter(exc_fhs.index, exc_fhs["hpl_next"], marker="x", color="#1f6f8b", s=34,
               zorder=6, label=f"FHS exceptions ({len(exc_fhs)})")
    ax.axhline(0, color="black", lw=0.5)
    ax.set_title("Firm 1-day 99% VaR vs next-day clean P&L - historical sim vs EWMA-filtered")
    ax.set_ylabel("USD")
    ax.legend(loc="lower left", fontsize=8, ncol=2, framealpha=0.9)
    ax.margins(x=0.01)
    fig.tight_layout()
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="risk.jobs.backfill")
    parser.add_argument("--snapshot", default="data/seed/market_snapshot.parquet")
    parser.add_argument("--portfolio", default="data/seed/portfolio.yaml")
    parser.add_argument("--days", type=int, default=750)
    parser.add_argument("--out", default="data/derived/backfill_var.parquet")
    parser.add_argument("--chart", default="docs/img/backtest_firm.png")
    args = parser.parse_args(argv)

    book, levels, returns, ffilled = load_inputs(args.snapshot, args.portfolio)
    nz = ffilled[ffilled > 0]
    if len(nz):
        print("[backfill] forward-filled cells by factor (DQ metric):",
              dict(nz.sort_values(ascending=False)))

    t0 = time.perf_counter()
    results = run_backfill(book, levels, returns, n_days=args.days)
    print(f"[backfill] {args.days} days x {results['method'].nunique()} methods in "
          f"{time.perf_counter() - t0:.1f}s")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    results.to_parquet(args.out, compression="zstd", index=False)
    print(f"[backfill] wrote {len(results):,} rows -> {args.out}")

    print_backtest_stats(results, p=1.0 - DEFAULT_CONFIG.alpha_var)
    make_chart(results, args.chart)
    print(f"[backfill] chart -> {args.chart}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
