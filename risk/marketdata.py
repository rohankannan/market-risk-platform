"""Market-data fetch clients and transforms.

Used by the one-off snapshot script now and by the nightly EOD ingest later.
Source hierarchy per factor comes from data/seed/portfolio.yaml:

- Equities: yfinance (batched, single session, no threads) with per-ticker Stooq
  CSV fallback. Both close and adjusted close are kept: RETURNS come only from
  the adjusted series (split/dividend immunity); the unadjusted close exists to
  convert target notionals to units at seed time.
- Rates / FX / VIX: FRED. Uses the official API when FRED_API_KEY is set,
  otherwise falls back to the keyless fredgraph.csv endpoint - the repo stays
  runnable with zero API keys.
- FX quote conventions are normalized AT INGEST to "USD per 1 unit of foreign
  currency" (invert_on_ingest), so downstream P&L is uniformly qty * dS.

Stooq caveat (documented, acceptable for a fallback): its daily closes are
split-adjusted but not reliably dividend-adjusted; rows sourced from Stooq set
value_unadjusted = value.
"""

from __future__ import annotations

import datetime as dt
import io
import os
from dataclasses import dataclass

import httpx
import pandas as pd
import yaml
from tenacity import retry, stop_after_attempt, wait_exponential

FRED_API_URL = "https://api.stlouisfed.org/fred/series/observations"
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
STOOQ_CSV_URL = "https://stooq.com/q/d/l/"

_RETRY = dict(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=5, max=120), reraise=True)


@dataclass(frozen=True)
class FactorSpec:
    code: str
    factor_type: str
    return_conv: str
    source: str            # YFINANCE | FRED
    symbol: str
    invert: bool = False
    fallback_source: str | None = None
    fallback_symbol: str | None = None
    ffill_limit: int = 3


def load_factor_specs(portfolio_yaml: str) -> list[FactorSpec]:
    """Factor definitions from portfolio.yaml (single source of truth for the universe)."""
    with open(portfolio_yaml) as f:
        cfg = yaml.safe_load(f)
    specs = []
    for row in cfg["factors"]:
        fb = row.get("fallback") or {}
        specs.append(FactorSpec(
            code=row["code"],
            factor_type=row["type"],
            return_conv=row["conv"],
            source=row["source"],
            symbol=row["symbol"],
            invert=bool(row.get("invert", False)),
            fallback_source=fb.get("source"),
            fallback_symbol=fb.get("symbol"),
            ffill_limit=int(row.get("ffill_limit", 3)),
        ))
    return specs


# ---------------------------------------------------------------- FRED

def parse_fred_csv(text: str) -> pd.Series:
    """Parse a fredgraph.csv payload: first column date, second column value,
    '.' means no observation that day (federal holidays)."""
    df = pd.read_csv(io.StringIO(text))
    if df.shape[1] < 2:
        raise ValueError("unexpected fredgraph.csv shape")
    date_col, value_col = df.columns[0], df.columns[1]
    s = pd.Series(
        pd.to_numeric(df[value_col].replace(".", pd.NA), errors="coerce").to_numpy(),
        index=pd.to_datetime(df[date_col]),
        name=value_col,
        dtype="float64",
    )
    return s.dropna()


@retry(**_RETRY)
def fetch_fred_series(series_id: str, start: dt.date, end: dt.date,
                      api_key: str | None = None) -> pd.Series:
    """One FRED series as a float Series indexed by date.

    With FRED_API_KEY: official observations API. Without: keyless fredgraph.csv
    (full history, filtered locally) - less official, fine for a seed snapshot.
    """
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        if api_key:
            r = client.get(FRED_API_URL, params={
                "series_id": series_id, "api_key": api_key, "file_type": "json",
                "observation_start": start.isoformat(), "observation_end": end.isoformat(),
            })
            r.raise_for_status()
            obs = r.json()["observations"]
            s = pd.Series(
                pd.to_numeric(pd.Series([o["value"] for o in obs]).replace(".", pd.NA),
                              errors="coerce").to_numpy(),
                index=pd.to_datetime([o["date"] for o in obs]),
                dtype="float64",
            ).dropna()
        else:
            r = client.get(FRED_CSV_URL, params={"id": series_id})
            r.raise_for_status()
            s = parse_fred_csv(r.text)
    s = s.loc[(s.index >= pd.Timestamp(start)) & (s.index <= pd.Timestamp(end))]
    if s.empty:
        raise ValueError(f"FRED returned no observations for {series_id} in {start}..{end}")
    return s


# ---------------------------------------------------------------- Stooq (equity fallback)

def parse_stooq_csv(text: str) -> pd.Series:
    """Parse Stooq daily CSV (Date,Open,High,Low,Close,Volume) into a close Series."""
    df = pd.read_csv(io.StringIO(text))
    if "Close" not in df.columns or "Date" not in df.columns:
        raise ValueError("unexpected Stooq CSV shape")
    return pd.Series(df["Close"].astype(float).to_numpy(),
                     index=pd.to_datetime(df["Date"]), dtype="float64").dropna()


@retry(**_RETRY)
def fetch_stooq_daily(symbol: str, start: dt.date, end: dt.date) -> pd.Series:
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        r = client.get(STOOQ_CSV_URL, params={"s": symbol, "i": "d"})
        r.raise_for_status()
        s = parse_stooq_csv(r.text)
    s = s.loc[(s.index >= pd.Timestamp(start)) & (s.index <= pd.Timestamp(end))]
    if s.empty:
        raise ValueError(f"Stooq returned no rows for {symbol} in {start}..{end}")
    return s


# ---------------------------------------------------------------- yfinance (equity primary)

@retry(**_RETRY)
def fetch_yfinance_batch(tickers: list[str], start: dt.date, end: dt.date
                         ) -> dict[str, pd.DataFrame]:
    """One batched download, one session, no threads (rate-limit etiquette).

    Returns {ticker: DataFrame[close, adj_close]}. auto_adjust=False so both the
    raw and the split/dividend-adjusted series are available.
    """
    import yfinance as yf

    raw = yf.download(tickers, start=start.isoformat(),
                      end=(end + dt.timedelta(days=1)).isoformat(),
                      auto_adjust=False, threads=False, progress=False,
                      group_by="column")
    if raw is None or raw.empty:
        raise ValueError("yfinance returned an empty frame")
    out: dict[str, pd.DataFrame] = {}
    for t in tickers:
        if isinstance(raw.columns, pd.MultiIndex):
            close, adj = raw["Close"][t], raw["Adj Close"][t]
        else:  # single-ticker download has flat columns
            close, adj = raw["Close"], raw["Adj Close"]
        df = pd.DataFrame({"close": close, "adj_close": adj}).dropna(how="all")
        if not df.empty:
            out[t] = df
    return out


# ---------------------------------------------------------------- assembly

def build_snapshot_rows(spec: FactorSpec, values: pd.Series, source: str,
                        unadjusted: pd.Series | None = None) -> pd.DataFrame:
    """Long-format snapshot rows for one factor.

    `values` is what returns will be computed from (adjusted close for equities,
    levels for FRED). Inversion (JPY/MXN quote convention) happens HERE, once.
    """
    v = values.astype(float)
    if spec.invert:
        v = 1.0 / v
    df = pd.DataFrame({
        "factor_code": spec.code,
        "obs_date": v.index.normalize(),
        "value": v.to_numpy(),
        "value_unadjusted": (unadjusted.reindex(v.index).astype(float).to_numpy()
                             if unadjusted is not None else v.to_numpy()),
        "source": source,
    })
    return df.dropna(subset=["value"]).reset_index(drop=True)


def snapshot_summary(snapshot: pd.DataFrame) -> pd.DataFrame:
    """Per-factor sanity table: row counts, coverage, value range, business-day gaps."""
    rows = []
    for code, g in snapshot.groupby("factor_code"):
        idx = pd.DatetimeIndex(g["obs_date"]).sort_values()
        bdays = pd.bdate_range(idx[0], idx[-1])
        rows.append({
            "factor": code,
            "rows": len(g),
            "first": idx[0].date(),
            "last": idx[-1].date(),
            "missing_bdays": len(bdays.difference(idx)),
            "min": float(g["value"].min()),
            "max": float(g["value"].max()),
            "source": ",".join(sorted(g["source"].unique())),
        })
    return pd.DataFrame(rows).sort_values("factor").reset_index(drop=True)


def require_env_fred_key() -> str | None:
    return os.getenv("FRED_API_KEY") or None
