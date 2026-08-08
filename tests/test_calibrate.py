"""Known-answer tests for stress calibration - the arithmetic that turns the
catalog's magnitudes from round numbers into cited severities."""

import numpy as np
import pandas as pd
import pytest
import yaml

from risk.jobs.calibrate_stress import driver_shock
from risk_engine.calibrate import (
    conditional_tail_mean,
    horizon_moves,
    quantile_of,
    severity,
)
from risk_engine.stress import REPLAY_WINDOWS

SHOCKS_YAML = "scenarios/hypothetical_shocks.yaml"
CONV_OF_TYPE = {"RELATIVE": "LOG", "ABSOLUTE_BP": "ABS_BP", "ABSOLUTE": "ABS"}


def test_horizon_moves_are_rolling_sums():
    """Returns are additive in every convention here, so a k-day move is the
    rolling sum - no compounding, no unit mixing."""
    r = pd.DataFrame({"A": [0.01] * 10, "B": [2.0] * 10},
                     index=pd.date_range("2026-01-01", periods=10, freq="D"))
    m = horizon_moves(r, horizon=5)
    assert len(m) == 6                                   # 10 rows, 5-day window
    assert m["A"].iloc[0] == pytest.approx(0.05)
    assert m["B"].iloc[0] == pytest.approx(10.0)
    with pytest.raises(ValueError, match="horizon must be"):
        horizon_moves(r, horizon=0)


def test_severity_is_the_quantile_of_absolute_moves():
    rng = np.random.default_rng(0)
    r = pd.DataFrame({"A": rng.normal(0, 1, 2000)},
                     index=pd.date_range("2020-01-01", periods=2000, freq="D"))
    s = severity(r, "A", horizon=1, q=0.99)
    assert s == pytest.approx(np.quantile(np.abs(r["A"]), 0.99), rel=1e-12)


def test_quantile_of_locates_a_chosen_magnitude():
    r = pd.DataFrame({"A": [1.0, -2.0, 3.0, -4.0, 5.0]},
                     index=pd.date_range("2026-01-01", periods=5, freq="D"))
    q, hits = quantile_of(r, "A", 3.0, horizon=1)
    assert hits == 3                                     # |3|, |-4|, |5|
    assert q == pytest.approx(0.6)                        # 3 of 5 are <= 3


def test_conditional_tail_mean_selects_on_the_driver():
    # B mirrors A exactly in A's worst windows, C is flat: the conditional mean
    # must recover the co-move, not the unconditional average
    a = [0.0] * 90 + [-1.0] * 10
    r = pd.DataFrame({"A": a, "B": [x * 2 for x in a], "C": [0.5] * 100},
                     index=pd.date_range("2020-01-01", periods=100, freq="D"))
    ctm = conditional_tail_mean(r, "A", horizon=1, tail_q=0.1)
    assert ctm["A"] == pytest.approx(-1.0)
    assert ctm["B"] == pytest.approx(-2.0)
    assert ctm["C"] == pytest.approx(0.5)
    with pytest.raises(ValueError, match="not a calibrated factor"):
        conditional_tail_mean(r, "NOPE")


def test_driver_shock_ranks_inside_the_modal_convention():
    """20 vol points is not larger than a 0.223 log return: an equity selloff
    must report its equity leg, never the vol add-on."""
    shocks = {
        "EQ.SPY": {"type": "RELATIVE", "value": -0.223},
        "EQ.NVDA": {"type": "RELATIVE", "value": -0.223},
        "VOL.SPX.IV30": {"type": "ABSOLUTE", "value": 20},
    }
    factor, spec = driver_shock(shocks)
    assert factor == "EQ.SPY" and spec["value"] == -0.223

    # a uniform ladder ties across the class: quote the proxy tenor
    parallel = {f"IR.UST.{t}": {"type": "ABSOLUTE_BP", "value": 100}
                for t in ("3M", "2Y", "5Y", "10Y", "30Y")}
    assert driver_shock(parallel)[0] == "IR.UST.10Y"


def test_catalog_shock_types_match_factor_conventions():
    """A mislabeled type would apply bp as a log return - a 100x error. The
    batch raises on this; the catalog must never ship it."""
    portfolio = yaml.safe_load(open("data/seed/portfolio.yaml"))
    convs = {f["code"]: f["conv"] for f in portfolio["factors"]}
    for scenario in yaml.safe_load(open(SHOCKS_YAML)):
        for factor, spec in scenario["shocks"].items():
            assert factor in convs, f"{scenario['code']}: unknown factor {factor}"
            assert CONV_OF_TYPE[spec["type"]] == convs[factor], (
                f"{scenario['code']}: {factor} declares {spec['type']} but the "
                f"factor's convention is {convs[factor]}")


def test_catalog_rows_declare_their_calibration_class():
    """SENSITIVITY (round supervisory ladder) vs SCENARIO (measured
    co-movement) - the distinction the model doc turns on."""
    catalog = yaml.safe_load(open(SHOCKS_YAML))
    classes = {s["code"]: s["class"] for s in catalog}
    assert set(classes.values()) <= {"SENSITIVITY", "SCENARIO"}
    assert classes["RISK_OFF"] == "SCENARIO"
    assert classes["RATES_UP_100"] == "SENSITIVITY"
    # every row states its basis in prose - no undocumented magnitudes
    for s in catalog:
        assert len(s.get("description", "")) > 80, f"{s['code']} lacks a stated basis"


def test_replay_catalog_covers_a_rising_rate_regime():
    """Both crises are flight-to-quality episodes where this long-duration
    book gains on the rates leg; a stress catalog without a correlated
    stock-bond selloff would never show its adverse regime."""
    assert set(REPLAY_WINDOWS) == {"GFC_2008", "COVID_2020", "RATES_2022"}
    start, end = REPLAY_WINDOWS["RATES_2022"]
    assert start.year == 2022 and end.year == 2022
