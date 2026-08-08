"""Dashboard tests: known answers for the pure ui helpers, plus AppTest smoke
renders of each page with the API client stubbed - no server, no network."""

import pytest
from streamlit.testing.v1 import AppTest

from dashboard import api_client, ui

# ---------------------------------------------------------------- ui helpers


def test_fmt_usd_scales_and_sign():
    assert ui.fmt_usd(1_420_000) == "$1.42M"
    assert ui.fmt_usd(-318_000) == "-$318k"
    assert ui.fmt_usd(950) == "$950"
    assert ui.fmt_usd(None) == "-"


def test_fmt_pct():
    assert ui.fmt_pct(0.584) == "58.4%"
    assert ui.fmt_pct(None) == "-"


POINTS = [
    {"date": "2026-08-04", "var_hs": 100.0, "var_fhs": 110.0, "es_975": 120.0,
     "pnl": None, "exception_hs": False, "exception_fhs": False},
    {"date": "2026-08-05", "var_hs": 105.0, "var_fhs": 112.0, "es_975": 122.0,
     "pnl": -130.0, "exception_hs": True, "exception_fhs": True},
    {"date": "2026-08-06", "var_hs": 98.0, "var_fhs": 108.0, "es_975": 118.0,
     "pnl": 40.0, "exception_hs": False, "exception_fhs": False},
]


def test_history_frame_negates_var_and_sorts():
    df = ui.history_frame(list(reversed(POINTS)))
    assert df.index.is_monotonic_increasing
    assert df["neg_var_hs"].iloc[0] == -100.0
    assert df["neg_var_fhs"].iloc[-1] == -108.0


SCENARIOS = [
    {"scenario_code": "GFC_2008", "scenario_name": "Gfc 2008",
     "scenario_type": "HISTORICAL_REPLAY", "window_start": "2008-09-12",
     "window_end": "2009-09-11", "description": None,
     "impacts": {"RATES": -3e6, "FX": -8e6, "EQUITY": -2e6, "FIRM": -13e6},
     "firm_impact": -13e6, "worst_desk": "FX"},
    {"scenario_code": "RATES_UP_100", "scenario_name": "Rates Up 100",
     "scenario_type": "HYPOTHETICAL", "window_start": None, "window_end": None,
     "description": "Parallel +100bp shift", "impacts": {"RATES": -7e6, "FIRM": -7e6},
     "firm_impact": -7e6, "worst_desk": "RATES"},
]


def test_figures_build():
    assert ui.pnl_vs_var_figure(POINTS).axes
    assert ui.pnl_vs_var_figure(POINTS, models=("fhs",)).axes
    assert ui.scenario_bars_figure(SCENARIOS).axes


# ---------------------------------------------------------------- page smoke

DESK = {"desk_code": "FIRM", "desk_name": "Firm Aggregate", "is_aggregate": True,
        "var_hs_1d": 1_168_055.33, "var_fhs_1d": 1_299_526.59,
        "var_hs_10d": 3_693_715.26, "var_fhs_10d": 4_109_463.91,
        "es_975_1d": 1_181_544.44, "es_975_10d": 3_736_371.58,
        "es_stressed_1d": 2_737_393.44, "var_dod": 5_333.67,
        "limit_value": 2_000_000.0, "utilization": 0.584, "limit_status": "OK"}
LRT = {"name": "kupiec_pof", "statistic": 0.108, "p_value": 0.742, "df": 1,
       "reject_5pct": False, "details": {}}
PAYLOADS = {
    "/api/v1/risk/summary": {
        "as_of": "2026-08-06", "run_id": 328, "run_type": "EOD", "status": "SUCCESS",
        "diversification_benefit": 0.403,
        "desks": [DESK,
                  {**DESK, "desk_code": "RATES", "desk_name": "US Rates",
                   "is_aggregate": False, "var_hs_1d": 868_455.07, "utilization": 0.72,
                   "limit_value": 1_200_000.0, "limit_status": "OK"}]},
    "/api/v1/risk/history": {"scope": "FIRM", "start": "2026-08-04",
                             "end": "2026-08-06", "points": POINTS},
    "/api/v1/backtest/summary": {
        "scope": "FIRM", "model": "HS", "measure": "VAR_HS", "window": 250,
        "start": "2025-08-06", "end": "2026-08-06", "n_obs": 250, "n_exceptions": 2,
        "expected_exceptions": 2.5, "kupiec": LRT,
        "christoffersen_independence": {**LRT, "name": "christoffersen_independence"},
        "christoffersen_cc": {**LRT, "name": "christoffersen_cc", "df": 2},
        "traffic_light": {"zone": "GREEN", "n_exceptions": 2, "n_obs": 250,
                          "plus_factor": 0.0, "multiplier": 3.0, "regulatory_window": True},
        "exceptions": [{"date": "2026-05-15", "var_value": 1_184_980.48,
                        "pnl_value": -1_273_699.88},
                       {"date": "2026-06-05", "var_value": 1_211_819.96,
                        "pnl_value": -1_394_732.46}]},
    "/api/v1/scenarios/results": {"as_of": "2026-08-06", "run_id": 328,
                                  "results": SCENARIOS},
    "/api/v1/risk/exposures": {
        "as_of": "2026-08-06", "run_id": 328, "unit": "USD per 1bp",
        "rows": [
            {"desk_code": "FIRM", "factor_code": "IR.UST.2Y", "tenor_years": 2.0,
             "value": 17114.0},
            {"desk_code": "FIRM", "factor_code": "IR.UST.10Y", "tenor_years": 10.0,
             "value": 48004.0},
            {"desk_code": "RATES", "factor_code": "IR.UST.2Y", "tenor_years": 2.0,
             "value": 17114.0},
            {"desk_code": "RATES", "factor_code": "IR.UST.10Y", "tenor_years": 10.0,
             "value": 48004.0},
        ]},
}


@pytest.fixture
def stub_api(monkeypatch):
    monkeypatch.setattr(api_client, "get", lambda path, **params: PAYLOADS[path])


# AppTest.from_function lifts the function's source into a bare namespace, so
# each page wrapper imports inside its body.

def _overview_page():
    from dashboard import overview
    overview.render()


def _backtesting_page():
    from dashboard import backtesting
    backtesting.render()


def _stress_page():
    from dashboard import stress
    stress.render()


def _render(page_fn):
    at = AppTest.from_function(page_fn, default_timeout=15)
    at.session_state["as_of"] = "2026-08-06"
    at.session_state["desk_codes"] = ["FIRM", "EQUITY", "FX", "RATES"]
    at.run()
    assert not at.exception
    return at


def test_overview_renders(stub_api):
    at = _render(_overview_page)
    assert at.title[0].value == "Overview"
    assert at.metric[0].value == "$1.17M"                        # firm HS VaR tile
    assert at.metric[5].value == "40.3%"                         # diversification
    krd = at.dataframe[1].value                                  # key-rate table
    assert list(krd.columns) == ["2Y", "10Y", "Total"]           # tenor order
    assert krd.loc["RATES", "10Y"] == "$48k"


def test_backtesting_renders(stub_api):
    at = _render(_backtesting_page)
    assert at.metric[1].value == "GREEN"
    assert any("Kupiec" in str(df.value["Test"].tolist()) for df in at.dataframe)


def test_stress_renders(stub_api):
    at = _render(_stress_page)
    assert at.title[0].value == "Stress scenarios"
    table = at.dataframe[0].value
    assert list(table["Scenario"]) == ["Gfc 2008", "Rates Up 100"]
    assert any("Parallel +100bp shift" in m.value for m in at.markdown)


def test_backtesting_no_exceptions_branch(monkeypatch):
    empty = {**PAYLOADS["/api/v1/backtest/summary"], "n_exceptions": 0, "exceptions": [],
             "traffic_light": {**PAYLOADS["/api/v1/backtest/summary"]["traffic_light"],
                               "n_exceptions": 0}}
    monkeypatch.setattr(api_client, "get",
                        lambda path, **params: {**PAYLOADS, "/api/v1/backtest/summary": empty}[path])
    at = _render(_backtesting_page)
    assert any("No exceptions in the window." in c.value for c in at.caption)


def test_stress_falls_back_when_pinned_date_has_no_scenario_run(monkeypatch):
    """Backfill-only as-of dates have no scenario run at or before them: the
    page must fall back to the latest scenario run and say so."""
    def get(path, **params):
        if params.get("as_of") is not None:
            request = __import__("httpx").Request("GET", path)
            response = __import__("httpx").Response(
                404, json={"detail": "no scenario run on or before 2026-08-05"},
                request=request)
            raise __import__("httpx").HTTPStatusError("404", request=request, response=response)
        return PAYLOADS[path]
    monkeypatch.setattr(api_client, "get", get)
    at = _render(_stress_page)
    assert not at.exception
    assert any("showing the latest" in c.value for c in at.caption)
    assert at.dataframe[0].value["Scenario"].tolist() == ["Gfc 2008", "Rates Up 100"]


def test_fetch_maps_connect_error_to_stop(monkeypatch):
    def boom(path, **params):
        raise __import__("httpx").ConnectError("refused")
    monkeypatch.setattr(api_client, "get", boom)

    def page():
        from dashboard import api_client as ac
        ac.fetch("/api/v1/meta")
    at = AppTest.from_function(page, default_timeout=15).run()
    assert not at.exception and at.error
    assert "unreachable" in at.error[0].value


def test_fetch_renders_404_detail_as_warning(monkeypatch):
    def gone(path, **params):
        httpx = __import__("httpx")
        request = httpx.Request("GET", path)
        response = httpx.Response(404, json={"detail": "no completed runs yet"},
                                  request=request)
        raise httpx.HTTPStatusError("404", request=request, response=response)
    monkeypatch.setattr(api_client, "get", gone)

    def page():
        from dashboard import api_client as ac
        ac.fetch("/api/v1/risk/summary")
    at = AppTest.from_function(page, default_timeout=15).run()
    assert not at.exception and at.warning
    assert "no completed runs yet" in at.warning[0].value
