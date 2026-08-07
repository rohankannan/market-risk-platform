"""Seed loader - milestone 2 (Aug 24-Sep 14).

Loads data/seed/market_snapshot.parquet (committed - cloners need zero API keys)
plus portfolio.yaml fixtures via COPY, then hands off to `eod backfill`.
Target: cold clone -> populated dashboard < 5 minutes.
"""


def main() -> int:
    raise NotImplementedError(
        "milestone 2: parquet COPY load + portfolio.yaml fixtures. "
        "The snapshot itself is produced by a one-off pull script (also milestone 2)."
    )


if __name__ == "__main__":
    raise SystemExit(main())
