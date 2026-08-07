"""API tests: known-answer checks on the pure response builders, plus route
behavior with the query layer stubbed - no live database, like the rest of
the suite."""

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from api import queries, schemas
from api.main import app

AS_OF = dt.date(2026, 8, 6)
RUN = {"run_id": 7, "run_date": AS_OF, "run_type": "EOD", "status": "SUCCESS",
       "finished_at": None, "code_version": "abc123def456"}
PREV = {"run_id": 6, "run_date": AS_OF - dt.timedelta(days=1), "run_type": "EOD",
        "status": "SUCCESS", "finished_at": None, "code_version": "abc123def456"}


def _risk_row(code, measure, value, horizon=1, agg=False):
    return {"desk_code": code, "desk_name": code.title(), "is_aggregate": agg,
            "measure": measure, "horizon_days": horizon, "value": value}


ROWS = [
    _risk_row("FIRM", "VAR_HS", 100.0, agg=True),
    _risk_row("FIRM", "VAR_HS", 316.23, horizon=10, agg=True),
    _risk_row("FIRM", "ES_975", 120.0, agg=True),
    _risk_row("FIRM", "ES_STRESSED", 260.0, agg=True),
    _risk_row("RATES", "VAR_HS", 60.0),
    _risk_row("EQUITY", "VAR_HS", 50.0),
    _risk_row("FX", "VAR_HS", 40.0),
]
PREV_ROWS = [_risk_row("FIRM", "VAR_HS", 90.0, agg=True)]
LIMITS = [
    {"desk_code": "FIRM", "measure": "VAR_HS", "limit_value": 200.0, "warn_threshold": 0.8},
    {"desk_code": "RATES", "measure": "VAR_HS", "limit_value": 50.0, "warn_threshold": 0.8},
    {"desk_code": "FX", "measure": "VAR_HS", "limit_value": 50.0, "warn_threshold": 0.8},
]


# ---------------------------------------------------------------- builders

def test_summary_known_answer():
    s = schemas.build_risk_summary(RUN, ROWS, LIMITS, PREV_ROWS)
    assert [d.desk_code for d in s.desks] == ["FIRM", "EQUITY", "FX", "RATES"]
    firm = s.desks[0]
    assert firm.var_hs_1d == 100.0 and firm.var_hs_10d == 316.23
    assert firm.es_stressed_1d == 260.0
    assert firm.var_dod == pytest.approx(10.0)                   # 100 vs prior 90
    assert firm.utilization == pytest.approx(0.5) and firm.limit_status == "OK"
    # 1 - 100/(60+50+40) = 1/3 diversification benefit
    assert s.diversification_benefit == pytest.approx(1 / 3, abs=1e-4)


def test_summary_limit_status_boundaries():
    s = schemas.build_risk_summary(RUN, ROWS, LIMITS, [])
    by = {d.desk_code: d for d in s.desks}
    assert by["RATES"].limit_status == "BREACH"                  # 60 > 50
    assert by["FX"].limit_status == "WARN"                       # 40 == 0.8 * 50
    assert by["EQUITY"].limit_status is None                     # no limit row
    assert by["FIRM"].var_dod is None                            # no prior run given


def test_summary_no_desks_no_diversification():
    s = schemas.build_risk_summary(RUN, [_risk_row("FIRM", "VAR_HS", 100.0, agg=True)],
                                   [], [])
    assert s.diversification_benefit is None


def test_history_alignment():
    d1, d2 = dt.date(2026, 8, 4), dt.date(2026, 8, 5)
    risk = [{"obs_date": d1, "measure": "VAR_HS", "value": 100.0},
            {"obs_date": d2, "measure": "VAR_HS", "value": 110.0},
            {"obs_date": d2, "measure": "ES_975", "value": 130.0}]
    pnl = [{"pnl_date": d2, "amount": -120.0}]
    exc = [{"obs_date": d2, "measure": "VAR_HS"}]
    h = schemas.build_history("FIRM", risk, pnl, exc)
    assert h.start == d1 and h.end == d2 and len(h.points) == 2
    assert h.points[0].pnl is None and not h.points[0].exception_hs
    p2 = h.points[1]
    assert p2.var_hs == 110.0 and p2.es_975 == 130.0 and p2.pnl == -120.0
    assert p2.exception_hs and not p2.exception_fhs


def _series(n, exception_idx):
    base = dt.date(2025, 1, 1)
    rows = [{"pnl_date": base + dt.timedelta(days=i), "pnl": 10.0,
             "var_value": None, "pnl_value": None} for i in range(n)]
    for i in exception_idx:
        rows[i]["var_value"], rows[i]["pnl_value"] = 100.0, -150.0
    return rows


def test_backtest_known_answer_250_days_5_exceptions():
    """Same published worked example as test_kupiec_known_value, through the
    full response builder: LR ~ 1.96, p ~ 0.16, amber zone, multiplier 3.40."""
    s = schemas.build_backtest_summary("FIRM", "HS", _series(250, (10, 60, 110, 160, 210)), 250)
    assert s.n_obs == 250 and s.n_exceptions == 5
    assert s.expected_exceptions == pytest.approx(2.5)
    assert s.kupiec.statistic == pytest.approx(1.96, abs=0.01)
    assert s.kupiec.p_value == pytest.approx(0.16, abs=0.01)
    assert not s.kupiec.reject_5pct
    assert not s.christoffersen_independence.reject_5pct        # isolated, not clustered
    assert s.traffic_light.zone == "AMBER"
    assert s.traffic_light.multiplier == pytest.approx(3.40)
    assert s.traffic_light.regulatory_window
    assert s.measure == "VAR_HS" and len(s.exceptions) == 5
    assert s.exceptions[0].pnl_value == -150.0


def test_backtest_zero_exceptions_green():
    s = schemas.build_backtest_summary("FIRM", "FHS", _series(100, ()), 250)
    assert s.n_exceptions == 0 and s.traffic_light.zone == "GREEN"
    assert not s.traffic_light.regulatory_window                # only 100 obs realized
    assert s.window == 250 and s.n_obs == 100


def _scenario_row(code, desk, impact, agg=False, stype="HYPOTHETICAL"):
    return {"scenario_code": code, "scenario_name": code.title(), "scenario_type": stype,
            "window_start": None, "window_end": None, "description": None,
            "desk_code": desk, "is_aggregate": agg, "pnl_impact": impact}


def test_scenarios_worst_first_and_worst_desk_excludes_firm():
    rows = [_scenario_row("MILD", "RATES", -10.0), _scenario_row("MILD", "FX", 5.0),
            _scenario_row("MILD", "FIRM", -5.0, agg=True),
            _scenario_row("GFC", "RATES", -300.0), _scenario_row("GFC", "FX", -100.0),
            _scenario_row("GFC", "FIRM", -400.0, agg=True)]
    s = schemas.build_scenario_results(RUN, rows)
    assert [r.scenario_code for r in s.results] == ["GFC", "MILD"]
    gfc = s.results[0]
    assert gfc.firm_impact == -400.0 and gfc.worst_desk == "RATES"
    assert gfc.impacts["FIRM"] == -400.0                         # FIRM stays in impacts


# ---------------------------------------------------------------- routes

class _FakeResult:
    def mappings(self):
        return self

    def first(self):
        return None

    def all(self):
        return []

    def __iter__(self):
        return iter(())


class _FakeConn:
    fail = False

    def execute(self, *a, **k):
        if self.fail:
            raise RuntimeError("connection refused")
        return _FakeResult()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeEngine:
    def __init__(self, fail=False):
        self.fail = fail

    def connect(self):
        conn = _FakeConn()
        conn.fail = self.fail
        return conn


@pytest.fixture
def client():
    app.state.engine = _FakeEngine()
    return TestClient(app)


def _fake_resolve(current=RUN, prev=PREV):
    def resolve(conn, as_of=None, **kw):
        if as_of is not None and as_of < current["run_date"]:
            return prev
        return current
    return resolve


def test_healthz_ok(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok", "database": "ok"}


def test_healthz_503_when_db_down():
    app.state.engine = _FakeEngine(fail=True)
    r = TestClient(app).get("/healthz")
    assert r.status_code == 503 and "unreachable" in r.json()["detail"]


def test_meta_empty_db(client, monkeypatch):
    monkeypatch.setattr(queries, "resolve_run", lambda conn, **kw: None)
    monkeypatch.setattr(queries, "available_dates", lambda conn: [])
    monkeypatch.setattr(queries, "desks", lambda conn: [])
    body = client.get("/api/v1/meta").json()
    assert body["batch_status"] == "not_yet_run"
    assert body["latest_as_of"] is None and body["available_dates"] == []


def test_meta_reports_latest_run(client, monkeypatch):
    monkeypatch.setattr(queries, "resolve_run", _fake_resolve())
    monkeypatch.setattr(queries, "available_dates", lambda conn: [PREV["run_date"], AS_OF])
    monkeypatch.setattr(
        queries, "desks",
        lambda conn: [{"desk_code": "FIRM", "desk_name": "Firm", "is_aggregate": True}])
    body = client.get("/api/v1/meta").json()
    assert body["latest_as_of"] == AS_OF.isoformat() and body["batch_status"] == "SUCCESS"
    assert body["desks"][0]["desk_code"] == "FIRM"


def test_summary_endpoint_and_cache_pinning(client, monkeypatch):
    monkeypatch.setattr(queries, "resolve_run", _fake_resolve())
    monkeypatch.setattr(queries, "risk_rows",
                        lambda conn, run_id: ROWS if run_id == RUN["run_id"] else PREV_ROWS)
    monkeypatch.setattr(queries, "limits_in_force", lambda conn, on: LIMITS)

    unpinned = client.get("/api/v1/risk/summary")
    assert unpinned.status_code == 200
    assert unpinned.headers["cache-control"] == "no-cache"
    assert unpinned.json()["desks"][0]["var_dod"] == pytest.approx(10.0)

    pinned = client.get(f"/api/v1/risk/summary?as_of={AS_OF.isoformat()}")
    assert pinned.json() == unpinned.json()
    assert "immutable" in pinned.headers["cache-control"]


def test_summary_404_when_no_runs(client, monkeypatch):
    monkeypatch.setattr(queries, "resolve_run", lambda conn, **kw: None)
    assert client.get("/api/v1/risk/summary").status_code == 404


def test_history_scope_parsing_and_unknown_scope(client, monkeypatch):
    monkeypatch.setattr(queries, "resolve_run", _fake_resolve())
    monkeypatch.setattr(queries, "desk_exists", lambda conn, code: code == "RATES")
    d = dt.date(2026, 8, 5)
    monkeypatch.setattr(queries, "history_risk_rows",
                        lambda conn, scope, end, window: [
                            {"obs_date": d, "measure": "VAR_HS", "value": 60.0}])
    monkeypatch.setattr(queries, "pnl_rows", lambda conn, scope, start, end: [])
    monkeypatch.setattr(queries, "exception_rows", lambda conn, scope, start, end: [])
    body = client.get("/api/v1/risk/history?scope=desk:rates").json()
    assert body["scope"] == "RATES" and body["points"][0]["var_hs"] == 60.0
    assert client.get("/api/v1/risk/history?scope=CREDIT").status_code == 404


def test_backtest_endpoint_insufficient_history(client, monkeypatch):
    monkeypatch.setattr(queries, "resolve_run", _fake_resolve())
    monkeypatch.setattr(queries, "desk_exists", lambda conn, code: True)
    monkeypatch.setattr(queries, "backtest_series",
                        lambda conn, scope, measure, end, window: [])
    assert client.get("/api/v1/backtest/summary").status_code == 404
