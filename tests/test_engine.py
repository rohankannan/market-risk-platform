"""Known-answer tests for portfolio revaluation and aggregation."""

import numpy as np
import pandas as pd
import pytest

from risk_engine.engine import aggregate, component_es, position_components, revalue
from risk_engine.pricing import bond_pnl
from risk_engine.var import var_es_from_pnl


def _positions():
    return pd.DataFrame([
        {"ticker": "SPY", "desk_code": "EQUITY", "factor_code": "EQ.SPY", "quantity": 100.0,
         "instrument_type": "ETF", "return_conv": "LOG", "coupon": None, "maturity_years": None},
        {"ticker": "JPYUSD_SPOT", "desk_code": "FX", "factor_code": "FX.JPYUSD",
         "quantity": -1_000_000.0, "instrument_type": "FX_SPOT", "return_conv": "LOG",
         "coupon": None, "maturity_years": None},
        {"ticker": "UST_10Y", "desk_code": "RATES", "factor_code": "IR.UST.10Y",
         "quantity": 1_000_000.0, "instrument_type": "GOVT_BOND", "return_conv": "ABS_BP",
         "coupon": 0.04, "maturity_years": 10.0},
    ])


LEVELS = pd.Series({"EQ.SPY": 500.0, "FX.JPYUSD": 0.0100, "IR.UST.10Y": 4.00})

SHOCKS = pd.DataFrame({
    "EQ.SPY": [np.log(1.10), 0.0],
    "FX.JPYUSD": [-0.05, 0.0],
    "IR.UST.10Y": [-100.0, 0.0],
})


def test_full_reval_known_answers():
    pnl = revalue(_positions(), LEVELS, SHOCKS, mode="full")
    # equity: exact, not linearized: 100 * 500 * (1.10 - 1) = 5,000
    assert pnl.loc[0, "SPY"] == pytest.approx(5_000.0, rel=1e-12)
    # short JPY position gains when JPY falls
    assert pnl.loc[0, "JPYUSD_SPOT"] == pytest.approx(-1_000_000 * 0.01 * (np.exp(-0.05) - 1), rel=1e-12)
    assert pnl.loc[0, "JPYUSD_SPOT"] > 0
    # bond leg equals the pricing module's own full reval
    expected = bond_pnl(0.04, 10.0, 0.04, 1_000_000.0, np.array([-100.0, 0.0]))
    np.testing.assert_allclose(pnl["UST_10Y"].to_numpy(), expected, rtol=1e-12)
    # zero shock -> zero P&L everywhere
    assert pnl.loc[1].abs().max() == pytest.approx(0.0, abs=1e-9)


def test_delta_gamma_close_for_small_shocks():
    small = pd.DataFrame({"EQ.SPY": [0.001], "FX.JPYUSD": [0.001], "IR.UST.10Y": [5.0]})
    full = revalue(_positions(), LEVELS, small, mode="full")
    dg = revalue(_positions(), LEVELS, small, mode="delta_gamma")
    for col in full.columns:
        assert dg.loc[0, col] == pytest.approx(full.loc[0, col], rel=2e-3)


def test_aggregate_desks_and_firm():
    pnl = revalue(_positions(), LEVELS, SHOCKS, mode="full")
    desk = aggregate(pnl, _positions())
    assert set(desk.columns) == {"EQUITY", "FX", "RATES", "FIRM"}
    assert desk["FIRM"].equals(desk[["EQUITY", "FX", "RATES"]].sum(axis=1))


def test_component_es_sums_to_firm_es():
    rng = np.random.default_rng(42)
    desk_pnl = pd.DataFrame(rng.normal(0, 10_000, size=(1_000, 3)), columns=["A", "B", "C"])
    comp = component_es(desk_pnl)
    firm = desk_pnl.sum(axis=1)
    q = np.quantile(firm, 0.025, method="linear")
    firm_es = -firm[firm <= q].mean()
    assert comp.sum() == pytest.approx(firm_es, rel=1e-12)


def test_position_components_identities():
    rng = np.random.default_rng(7)
    pos = pd.DataFrame([
        {"ticker": "AAA", "desk_code": "EQUITY", "factor_code": "EQ.AAA",
         "quantity": 100.0, "instrument_type": "STOCK"},
        {"ticker": "BBB", "desk_code": "EQUITY", "factor_code": "EQ.BBB",
         "quantity": 200.0, "instrument_type": "STOCK"},
        {"ticker": "VVV", "desk_code": "EQUITY", "factor_code": "VOL.SPX.IV30",
         "quantity": 10.0, "instrument_type": "OPTION"},
        {"ticker": "UST", "desk_code": "RATES", "factor_code": "IR.UST.10Y",
         "quantity": 1_000_000.0, "instrument_type": "GOVT_BOND"},
    ])
    pnl = pd.DataFrame(rng.normal(0, 10_000, size=(500, 4)),
                       columns=["AAA", "BBB", "VVV", "UST"])
    comp = position_components(pos, pnl)
    by = comp.set_index("ticker")

    assert dict(zip(comp["ticker"], comp["factor_class"])) == {
        "AAA": "EQ", "BBB": "EQ", "VVV": "VOL", "UST": "IR"}
    assert by.loc["BBB", "quantity"] == 200.0                    # book facts ride along
    assert by.loc["UST", "instrument_type"] == "GOVT_BOND"

    # a desk's Euler components sum exactly to the desk's own ES
    eq_es = var_es_from_pnl(pnl[["AAA", "BBB", "VVV"]].sum(axis=1)).es
    assert by.loc[["AAA", "BBB", "VVV"], "component_es"].sum() == pytest.approx(eq_es, rel=1e-12)

    # marginal <= standalone holds on this joint-Gaussian fixture (elliptical
    # => VaR subadditive); NOT a theorem for empirical VaR - pins the fixture
    assert (comp["marginal_var"] <= comp["standalone_var"] + 1e-9).all()

    # single-position desk: standalone == marginal == desk VaR, component == its ES
    ust = var_es_from_pnl(pnl["UST"])
    assert by.loc["UST", "standalone_var"] == pytest.approx(ust.var, rel=1e-12)
    assert by.loc["UST", "marginal_var"] == pytest.approx(ust.var, rel=1e-12)
    assert by.loc["UST", "component_es"] == pytest.approx(ust.es, rel=1e-12)


def test_loud_failures():
    pos = _positions()
    with pytest.raises(ValueError, match="missing factors"):
        revalue(pos, LEVELS, SHOCKS.drop(columns=["EQ.SPY"]))
    bad = pos.copy()
    bad.loc[0, "return_conv"] = "ABS_BP"      # equity on a bp factor = miswired book
    with pytest.raises(ValueError, match="non-LOG factor"):
        revalue(bad, LEVELS, SHOCKS)
