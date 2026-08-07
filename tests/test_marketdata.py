"""Tests for the pure market-data transforms (no network - network code is
exercised only by the one-off snapshot script)."""

import pandas as pd
import pytest

from risk.marketdata import (
    FactorSpec,
    build_snapshot_rows,
    parse_fred_csv,
    parse_stooq_csv,
    snapshot_summary,
)


def test_parse_fred_csv_handles_holiday_dots():
    text = "observation_date,DGS10\n2024-01-02,3.95\n2024-01-03,.\n2024-01-04,4.02\n"
    s = parse_fred_csv(text)
    assert len(s) == 2                       # '.' row dropped
    assert s.iloc[0] == pytest.approx(3.95)
    assert s.index[1] == pd.Timestamp("2024-01-04")


def test_parse_stooq_csv():
    text = "Date,Open,High,Low,Close,Volume\n2024-01-02,470.1,472.0,469.0,471.5,1000\n"
    s = parse_stooq_csv(text)
    assert s.iloc[0] == pytest.approx(471.5)


def test_fx_inversion_at_ingest():
    """DEXJPUS is JPY per USD; the stored factor must be USD per JPY."""
    spec = FactorSpec(code="FX.JPYUSD", factor_type="FX_RATE", return_conv="LOG",
                      source="FRED", symbol="DEXJPUS", invert=True)
    raw = pd.Series([110.0, 125.0], index=pd.to_datetime(["2024-01-02", "2024-01-03"]))
    rows = build_snapshot_rows(spec, raw, "FRED")
    assert rows["value"].iloc[0] == pytest.approx(1 / 110.0)
    assert rows["value"].iloc[1] == pytest.approx(1 / 125.0)


def test_snapshot_rows_keep_unadjusted_close():
    spec = FactorSpec(code="EQ.NVDA", factor_type="PRICE", return_conv="LOG",
                      source="YFINANCE", symbol="NVDA")
    idx = pd.to_datetime(["2024-06-07", "2024-06-10"])           # 10:1 split weekend
    adj = pd.Series([120.9, 121.8], index=idx)                    # adjusted: smooth
    close = pd.Series([1209.0, 121.8], index=idx)                 # unadjusted: 10x jump
    rows = build_snapshot_rows(spec, adj, "YFINANCE", unadjusted=close)
    assert rows["value"].tolist() == pytest.approx([120.9, 121.8])
    assert rows["value_unadjusted"].tolist() == pytest.approx([1209.0, 121.8])


def test_snapshot_summary_counts_gaps():
    df = pd.DataFrame({
        "factor_code": "X",
        "obs_date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-05"]),  # 01-04 missing
        "value": [1.0, 2.0, 3.0],
        "value_unadjusted": [1.0, 2.0, 3.0],
        "source": "FRED",
    })
    summary = snapshot_summary(df)
    assert summary.loc[0, "missing_bdays"] == 1
