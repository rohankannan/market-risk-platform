"""Tests for return conventions and alignment-adjacent behavior."""

import numpy as np
import pandas as pd
import pytest

from risk_engine.factors import to_returns


def test_return_conventions():
    idx = pd.bdate_range("2024-01-01", periods=3)
    levels = pd.DataFrame(
        {"EQ.SPY": [100.0, 101.0, 99.0],       # LOG
         "IR.UST.10Y": [4.00, 4.10, 3.85],     # percent -> ABS_BP
         "VOL.SPX.IV30": [14.0, 18.5, 16.0]},  # ABS
        index=idx,
    )
    conv = {"EQ.SPY": "LOG", "IR.UST.10Y": "ABS_BP", "VOL.SPX.IV30": "ABS"}
    r = to_returns(levels, conv)

    assert r["EQ.SPY"].iloc[0] == pytest.approx(np.log(101 / 100))
    assert r["IR.UST.10Y"].iloc[0] == pytest.approx(10.0)   # +0.10% = +10bp
    assert r["IR.UST.10Y"].iloc[1] == pytest.approx(-25.0)
    assert r["VOL.SPX.IV30"].iloc[0] == pytest.approx(4.5)


def test_unknown_convention_raises():
    levels = pd.DataFrame({"X": [1.0, 2.0]})
    with pytest.raises(ValueError, match="unknown return convention"):
        to_returns(levels, {"X": "PCT"})


def test_split_immunity_by_construction():
    """A 4:1 split in the UNADJUSTED series would inject a fake -75% scenario.
    The pipeline computes returns from the ADJUSTED series only - this test
    encodes that contract at the transform level."""
    idx = pd.bdate_range("2024-01-01", periods=4)
    adj = pd.DataFrame({"EQ.NVDA": [100.0, 101.0, 102.0, 103.0]}, index=idx)   # adjusted: smooth
    r = to_returns(adj, {"EQ.NVDA": "LOG"})
    assert (r["EQ.NVDA"].abs() < 0.20).all()
