"""Tests for the pure seed-bundle transform (no DB)."""

import datetime as dt

import pandas as pd
import pytest

from risk.jobs.seed import build_seed_bundle, latest_by_factor
from risk_engine.pricing import bond_price


def _snap(rows):
    return pd.DataFrame(rows, columns=["factor_code", "obs_date", "value",
                                       "value_unadjusted", "source"])


CFG = {
    "desks": [{"code": "EQUITY", "name": "Eq"}, {"code": "FX", "name": "Fx"},
              {"code": "RATES", "name": "Rates"},
              {"code": "FIRM", "name": "Firm", "is_aggregate": True}],
    "factors": [
        {"code": "EQ.SPY", "type": "PRICE", "conv": "LOG", "source": "YFINANCE", "symbol": "SPY"},
        {"code": "FX.JPYUSD", "type": "FX_RATE", "conv": "LOG", "source": "FRED",
         "symbol": "DEXJPUS", "invert": True, "ffill_limit": 7},
        {"code": "IR.UST.10Y", "type": "YIELD", "conv": "ABS_BP", "source": "FRED", "symbol": "DGS10"},
    ],
    "positions": {
        "EQUITY": [{"ticker": "SPY", "factor": "EQ.SPY", "target_notional_usd": 8_000_000}],
        "FX": [{"ticker": "JPYUSD_SPOT", "factor": "FX.JPYUSD", "target_notional_usd": -12_000_000}],
        "RATES": [{"ticker": "UST_10Y", "factor": "IR.UST.10Y", "face_usd": 10_000_000,
                   "coupon": "par", "maturity_years": 10}],
    },
    "limits": [{"desk": "FIRM", "measure": "VAR_HS", "limit_usd": 2_000_000}],
}

SNAP = _snap([
    ("EQ.SPY", pd.Timestamp("2026-08-05"), 770.0, 771.5, "YFINANCE"),   # adj vs unadj differ
    ("EQ.SPY", pd.Timestamp("2026-08-06"), 769.0, 770.5, "YFINANCE"),
    ("FX.JPYUSD", pd.Timestamp("2026-07-31"), 0.00680, 0.00680, "FRED"),
    ("IR.UST.10Y", pd.Timestamp("2026-08-05"), 4.20, 4.20, "FRED"),
])


def test_latest_by_factor_picks_last_row():
    latest = latest_by_factor(SNAP)
    assert latest.loc["EQ.SPY", "value_unadjusted"] == pytest.approx(770.5)


def test_equity_shares_use_unadjusted_anchor():
    b = build_seed_bundle(CFG, SNAP)
    spy = next(p for p in b.positions if p["ticker"] == "SPY")
    assert spy["quantity"] == round(8_000_000 / 770.5)          # unadjusted, not adjusted
    assert spy["quantity"] * 770.5 == pytest.approx(8_000_000, rel=0.001)
    assert spy["entry_price"] == pytest.approx(770.5)


def test_fx_short_position_units_signed():
    b = build_seed_bundle(CFG, SNAP)
    jpy = next(p for p in b.positions if p["ticker"] == "JPYUSD_SPOT")
    assert jpy["quantity"] == pytest.approx(-12_000_000 / 0.00680, rel=1e-6)
    assert jpy["quantity"] < 0


def test_par_bond_struck_at_anchor_yield():
    b = build_seed_bundle(CFG, SNAP)
    bond = next(i for i in b.instruments if i["ticker"] == "UST_10Y")
    assert bond["meta"]["coupon"] == pytest.approx(0.042)        # percent -> decimal
    # coupon == anchor yield => the bond prices at par on the anchor date
    assert bond_price(bond["meta"]["coupon"], 10, 0.042) == pytest.approx(100.0, abs=1e-9)


def test_bond_dv01_sensitivity_per_unit_face():
    b = build_seed_bundle(CFG, SNAP)
    s = next(s for s in b.instrument_factors if s["ticker"] == "UST_10Y")
    assert s["sensitivity_type"] == "DV01"
    assert s["sensitivity"] == pytest.approx(8.09e-4, rel=0.02)  # ~ $8.1e-4 per bp per $1 face @ 4.2%


def test_anchor_date_is_max_across_factors():
    b = build_seed_bundle(CFG, SNAP)
    assert b.anchor_date == dt.date(2026, 8, 6)                  # FX lags but anchor is the max
