"""Replay-shock anchors pinned against the committed snapshot (offline).

These are the sanity numbers the model doc quotes: if an ingest change moves
them, this test - not an interviewer - catches it first.
"""

import pandas as pd
import pytest

from risk_engine.factors import align_levels, to_returns
from risk_engine.stress import REPLAY_WINDOWS, compute_replay_shock

SNAPSHOT = "data/seed/market_snapshot.parquet"
FFILL_LIMITS = {"FX.EURUSD": 7, "FX.GBPUSD": 7, "FX.JPYUSD": 7, "FX.MXNUSD": 7}
CONVENTIONS = {"EQ": "LOG", "FX": "LOG", "IR": "ABS_BP", "VOL": "ABS"}


def _returns():
    snap = pd.read_parquet(SNAPSHOT)
    levels = snap.pivot(index="obs_date", columns="factor_code", values="value")
    levels.index = pd.to_datetime(levels.index)
    aligned, _ = align_levels(levels.sort_index(), FFILL_LIMITS)
    conv = {c: CONVENTIONS[c.split(".")[0]] for c in aligned.columns}
    return to_returns(aligned, conv).dropna()


def test_gfc_replay_anchor_moves():
    shock = compute_replay_shock(_returns(), *REPLAY_WINDOWS["GFC_2008"])
    assert shock["EQ.SPY"] == pytest.approx(-0.50, abs=0.02)      # SPX halves, log terms
    assert shock["IR.UST.2Y"] == pytest.approx(-118.0, abs=3.0)   # bull flight, bp
    assert shock["FX.JPYUSD"] == pytest.approx(0.116, abs=0.01)   # yen safe haven


def test_covid_replay_directionality():
    shock = compute_replay_shock(_returns(), *REPLAY_WINDOWS["COVID_2020"])
    assert shock["EQ.SPY"] < -0.25                                # equity crash
    assert shock["IR.UST.10Y"] < -50.0                            # yields collapse
    assert shock["FX.MXNUSD"] < -0.15                             # EM FX rout
