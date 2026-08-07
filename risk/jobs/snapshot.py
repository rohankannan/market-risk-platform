"""One-off snapshot pull: fetch full factor history and write the committed seed file.

    python -m risk.jobs.snapshot [--start 2007-01-01] [--end YYYY-MM-DD]
                                 [--portfolio data/seed/portfolio.yaml]
                                 [--out data/seed/market_snapshot.parquet]

Design notes:
- Start defaults to 2007-01-01 (not 5 years): same number of API calls, ~80k rows,
  ~1-2 MB compressed - and it makes the fixed 2008-09 stressed-ES window and the
  GFC replay computable from the seed. Every factor in the universe has history
  back to 2007.
- The snapshot is COMMITTED to the repo: no test, CI run, or demo ever depends on
  a live third-party API. This script is the only thing that touches the network.
- Zero keys required (fredgraph.csv fallback); FRED_API_KEY is used when present.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import pandas as pd

from risk.marketdata import (
    build_snapshot_rows,
    fetch_fred_series,
    fetch_stooq_daily,
    fetch_yfinance_batch,
    load_factor_specs,
    require_env_fred_key,
    snapshot_summary,
)


def pull_snapshot(specs, start: dt.date, end: dt.date) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    failures: list[str] = []

    eq = [s for s in specs if s.source == "YFINANCE"]
    fred = [s for s in specs if s.source == "FRED"]

    # --- equities: one batched yfinance call, Stooq per-ticker fallback
    yf_data: dict[str, pd.DataFrame] = {}
    if eq:
        try:
            yf_data = fetch_yfinance_batch([s.symbol for s in eq], start, end)
        except Exception as exc:  # batch-level failure -> everything falls through to Stooq
            print(f"[snapshot] yfinance batch failed ({exc}); falling back to Stooq per ticker",
                  file=sys.stderr)
    for spec in eq:
        df = yf_data.get(spec.symbol)
        if df is not None and not df.empty:
            frames.append(build_snapshot_rows(spec, df["adj_close"].dropna(), "YFINANCE",
                                              unadjusted=df["close"]))
            continue
        if spec.fallback_source == "STOOQ" and spec.fallback_symbol:
            try:
                s = fetch_stooq_daily(spec.fallback_symbol, start, end)
                frames.append(build_snapshot_rows(spec, s, "STOOQ"))
                print(f"[snapshot] {spec.code}: used Stooq fallback ({spec.fallback_symbol})",
                      file=sys.stderr)
                continue
            except Exception as exc:
                print(f"[snapshot] {spec.code}: Stooq fallback failed ({exc})", file=sys.stderr)
        failures.append(spec.code)

    # --- FRED: rates, FX (inverted where flagged), VIX
    api_key = require_env_fred_key()
    if fred and not api_key:
        print("[snapshot] FRED_API_KEY not set - using keyless fredgraph.csv endpoint",
              file=sys.stderr)
    for spec in fred:
        try:
            s = fetch_fred_series(spec.symbol, start, end, api_key=api_key)
            frames.append(build_snapshot_rows(spec, s, "FRED"))
        except Exception as exc:
            print(f"[snapshot] {spec.code}: FRED fetch failed ({exc})", file=sys.stderr)
            failures.append(spec.code)

    if failures:
        raise SystemExit(f"[snapshot] FAILED for factors: {failures} - refusing to write "
                         "a partial seed (delete/retry instead of committing holes)")
    return (pd.concat(frames, ignore_index=True)
            .sort_values(["factor_code", "obs_date"]).reset_index(drop=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="risk.jobs.snapshot")
    parser.add_argument("--start", type=dt.date.fromisoformat, default=dt.date(2007, 1, 1))
    parser.add_argument("--end", type=dt.date.fromisoformat,
                        default=(dt.date.today() - dt.timedelta(days=1)))
    parser.add_argument("--portfolio", default="data/seed/portfolio.yaml")
    parser.add_argument("--out", default="data/seed/market_snapshot.parquet")
    args = parser.parse_args(argv)

    specs = load_factor_specs(args.portfolio)
    print(f"[snapshot] pulling {len(specs)} factors, {args.start} .. {args.end}")
    snap = pull_snapshot(specs, args.start, args.end)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    snap.to_parquet(out, compression="zstd", index=False)

    summary = snapshot_summary(snap)
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print(summary.to_string(index=False))
    size_kb = out.stat().st_size / 1024
    print(f"[snapshot] wrote {len(snap):,} rows for {snap['factor_code'].nunique()} factors "
          f"-> {out} ({size_kb:,.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
