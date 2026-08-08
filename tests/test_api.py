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


EXPOSURE_ROWS = [
    {"desk_code": "RATES", "factor_code": "IR.UST.10Y", "measure": "KRD_DV01", "value": 48004.0},
    {"desk_code": "RATES", "factor_code": "IR.UST.2Y", "measure": "KRD_DV01", "value": 17114.0},
    {"desk_code": "FIRM", "factor_code": "IR.UST.2Y", "measure": "KRD_DV01", "value": 17114.0},
    {"desk_code": "EQUITY", "factor_code": "VOL.SPX.IV30", "measure": "VEGA", "value": -1200.0},
]


def test_key_rate_exposures_maps_tenors_and_sorts_firm_first():
    e = schemas.build_key_rate_exposures(RUN, EXPOSURE_ROWS)
    assert e.unit == "USD per 1bp"
    assert e.rows[0].desk_code == "FIRM"
    rates = [r for r in e.rows if r.desk_code == "RATES"]
    assert [r.tenor_years for r in rates] == [2.0, 10.0]         # tenor order, not lexical
    assert rates[1].value == 48004.0
    assert len(e.rows) == 3                                      # vega stays out of the KRD table
    assert e.vega[0].factor_code == "VOL.SPX.IV30" and e.vega[0].value == -1200.0


def test_key_rate_exposures_empty_rows_ok():
    e = schemas.build_key_rate_exposures(RUN, [])
    assert e.rows == [] and e.vega == []


PLA_ROWS = [{"pnl_date": dt.date(2026, 1, 1) + dt.timedelta(days=i),
             "hpl": float(v), "rtpl": float(v) * 0.98}
            for i, v in enumerate(range(-50, 50))]


def test_pla_builder_green_on_tracking_series():
    p = schemas.build_pla_summary("EQUITY", 250, PLA_ROWS)
    assert p.zone == "GREEN" and p.spearman == 1.0
    assert p.n_obs == 100 and len(p.points) == 100
    assert p.points[0].date == dt.date(2026, 1, 1)


DESK_FACTORS = [
    {"desk_code": "RATES", "factor_code": "IR.UST.10Y"},
    {"desk_code": "RATES", "factor_code": "VOL.SPX.IV30"},
    {"desk_code": "FX", "factor_code": "FX.EURUSD"},
    {"desk_code": "FX", "factor_code": "FX.JPYUSD"},
]


def _move_rows(code, conv, levels):
    base = dt.date(2026, 8, 3)
    return [{"factor_code": code, "return_conv": conv,
             "obs_date": base + dt.timedelta(days=i), "value": v}
            for i, v in enumerate(levels)]


MOVE_ROWS = (
    _move_rows("IR.UST.10Y", "ABS_BP", [4.00, 4.10, 4.02, 4.20])       # +18bp, typical 9bp
    + _move_rows("VOL.SPX.IV30", "ABS", [20.0, 21.0, 20.0, 22.1])      # +2.1pt, typical 1pt
    + _move_rows("FX.EURUSD", "LOG", [100.0, 101.0, 100.0, 97.0])      # -3.0%, typical ~1%
    + _move_rows("FX.JPYUSD", "LOG", [1.0, 1.0, 1.0, 1.0])             # flat
)
MOVERS_PREV_ROWS = [_risk_row("FIRM", "VAR_HS", 90.0, agg=True),
                    _risk_row("RATES", "VAR_HS", 50.0), _risk_row("FX", "VAR_HS", 45.0)]


def test_movers_known_answer():
    """RATES 60 vs 50 = +10, FX 40 vs 45 = -5; EQUITY absent from the prior run
    is skipped; drivers rank by move-over-typical-move so a 2.1x vol day
    outranks a 2.0x rates day across conventions."""
    m = schemas.build_risk_movers(RUN, ROWS, MOVERS_PREV_ROWS, PREV["run_date"],
                                  DESK_FACTORS, MOVE_ROWS)
    assert m.prev_date == PREV["run_date"]
    assert [r.desk_code for r in m.rows] == ["RATES", "FX"]      # |+10| > |-5|; no EQUITY
    rates, fx = m.rows
    assert rates.delta_usd == pytest.approx(10.0)
    assert rates.delta_pct == pytest.approx(0.2)
    assert rates.drivers == ["VOL.SPX.IV30 +2.1pt", "IR.UST.10Y +18bp"]
    assert fx.delta_usd == pytest.approx(-5.0)
    assert fx.delta_pct == pytest.approx(-0.1111, abs=1e-4)
    assert fx.drivers == ["FX.EURUSD -3.0%", "FX.JPYUSD +0.0%"]


def test_movers_empty_without_prior_run():
    m = schemas.build_risk_movers(RUN, ROWS, [], None, DESK_FACTORS, MOVE_ROWS)
    assert m.rows == [] and m.prev_date is None


def test_movers_stale_factor_reads_as_zero_move():
    """A factor with no print on the run date carried its level (the EOD fill
    row), so its 'day move' is zero and it ranks behind live factors - not its
    last printed move masquerading as today's."""
    stale = _move_rows("IR.UST.10Y", "ABS_BP", [4.00, 4.10, 4.02])   # ends 08-05, run is 08-06
    fresh = _move_rows("VOL.SPX.IV30", "ABS", [20.0, 21.0, 20.0, 20.5])
    factors = [{"desk_code": "RATES", "factor_code": "IR.UST.10Y"},
               {"desk_code": "RATES", "factor_code": "VOL.SPX.IV30"}]
    m = schemas.build_risk_movers(RUN, ROWS, MOVERS_PREV_ROWS, PREV["run_date"],
                                  factors, stale + fresh)
    rates = next(r for r in m.rows if r.desk_code == "RATES")
    assert rates.drivers == ["VOL.SPX.IV30 +0.5pt", "IR.UST.10Y +0bp"]


CATALOG_ROWS = [
    {"scenario_code": "GFC_2008", "scenario_name": "Gfc 2008",
     "scenario_type": "HISTORICAL_REPLAY", "window_start": dt.date(2008, 9, 12),
     "window_end": dt.date(2008, 10, 10), "description": None,
     "factor_code": None, "shock_type": None, "shock_value": None},
    {"scenario_code": "BEAR_STEEPENER", "scenario_name": "Bear Steepener",
     "scenario_type": "HYPOTHETICAL", "window_start": None, "window_end": None,
     "description": "long-end selloff", "factor_code": "IR.UST.10Y",
     "shock_type": "ABSOLUTE_BP", "shock_value": 75.0},
    {"scenario_code": "BEAR_STEEPENER", "scenario_name": "Bear Steepener",
     "scenario_type": "HYPOTHETICAL", "window_start": None, "window_end": None,
     "description": "long-end selloff", "factor_code": "IR.UST.30Y",
     "shock_type": "ABSOLUTE_BP", "shock_value": 90.0},
]


def test_scenario_catalog_groups_shocks_replays_stay_empty():
    c = schemas.build_scenario_catalog(CATALOG_ROWS)
    assert [s.scenario_code for s in c.scenarios] == ["BEAR_STEEPENER", "GFC_2008"]
    steep, gfc = c.scenarios
    assert [sh.factor_code for sh in steep.shocks] == ["IR.UST.10Y", "IR.UST.30Y"]
    assert steep.shocks[1].shock_value == 90.0
    assert gfc.shocks == [] and gfc.window_start == dt.date(2008, 9, 12)


DESK = {"desk_code": "EQUITY", "desk_name": "Equity", "is_aggregate": False}
COMP_ROWS = [
    {"ticker": "SPY", "instrument_type": "ETF", "quantity": 11_800.0, "factor_class": "EQ",
     "factor_code": "EQ.SPY", "option_type": None, "moneyness": None, "maturity_years": None,
     "standalone_var": 45.55, "component_es": 60.0, "marginal_var": 40.0},
    {"ticker": "NVDA", "instrument_type": "STOCK", "quantity": 1_700.0, "factor_class": "EQ",
     "factor_code": "EQ.NVDA", "option_type": None, "moneyness": None, "maturity_years": None,
     "standalone_var": 30.20, "component_es": 30.0, "marginal_var": 20.0},
    {"ticker": "SPY_PUT_95", "instrument_type": "OPTION", "quantity": 7_800.0,
     "factor_class": "EQ", "factor_code": "EQ.SPY", "option_type": "PUT", "moneyness": 0.95,
     "maturity_years": 0.08333, "standalone_var": 10.10, "component_es": -10.0,
     "marginal_var": -5.0},
]
DESK_EXPOSURES = EXPOSURE_ROWS + [
    {"desk_code": "EQUITY", "factor_code": "EQ.SPY", "measure": "DELTA_USD",
     "value": 5_900_000.0}]


def test_desk_decomposition_waterfall_identity():
    """Buckets + diversification reproduce the desk VaR to the cent - the
    invariant the waterfall chart renders. The collar legs bucket as EQ (their
    delta leg), so the equity desk folds into one bucket."""
    d = schemas.build_desk_decomposition(RUN, DESK, ROWS, COMP_ROWS, DESK_EXPOSURES)
    assert d.var_hs_1d == 50.0
    assert [(b.factor_class, b.standalone_var) for b in d.buckets] == [("EQ", 85.85)]
    assert d.diversification == pytest.approx(-35.85)
    assert round(sum(b.standalone_var for b in d.buckets) + d.diversification, 2) == 50.0
    assert [(e.measure, e.factor_code) for e in d.exposures] == [
        ("VEGA", "VOL.SPX.IV30"), ("DELTA_USD", "EQ.SPY")]       # this desk only
    assert d.exposures[0].tenor_years is None


def test_desk_decomposition_buckets_sort_desc():
    rows = [{"factor_class": "EQ", "standalone_var": 10.0},
            {"factor_class": "IR", "standalone_var": 70.0},
            {"factor_class": "IR", "standalone_var": 5.0}]
    d = schemas.build_desk_decomposition(RUN, DESK, ROWS, rows, [])
    assert [(b.factor_class, b.standalone_var) for b in d.buckets] == [
        ("IR", 75.0), ("EQ", 10.0)]                              # summed, then desc


def test_desk_decomposition_backfill_run_has_no_buckets():
    d = schemas.build_desk_decomposition(RUN, DESK, ROWS, [], DESK_EXPOSURES)
    assert d.buckets == [] and d.diversification is None
    assert d.var_hs_1d == 50.0                                   # the VaR itself still reports


def test_desk_positions_pct_and_order():
    p = schemas.build_desk_positions(RUN, DESK, COMP_ROWS)
    assert [x.ticker for x in p.positions] == ["SPY", "NVDA", "SPY_PUT_95"]
    assert [x.pct_of_desk for x in p.positions] == [0.75, 0.375, -0.125]   # sums to 1
    put = p.positions[-1]
    assert put.option_type == "PUT" and put.moneyness == 0.95    # collar metadata surfaced
    assert put.component_es == -10.0                             # hedge: negative share
    assert put.factor_code == "EQ.SPY"                           # legs map to the underlier


def _tape_row(code, conv, obs_date, value, ftype="EQUITY"):
    return {"factor_code": code, "factor_type": ftype, "return_conv": conv,
            "obs_date": obs_date, "value": value}


def test_factors_latest_conventions_and_tape_order():
    d1, d2 = dt.date(2026, 8, 5), dt.date(2026, 8, 6)
    rows = [
        _tape_row("IR.UST.2Y", "ABS_BP", d1, 3.94, "RATE"),
        _tape_row("IR.UST.2Y", "ABS_BP", d2, 3.91, "RATE"),
        _tape_row("IR.UST.10Y", "ABS_BP", d1, 4.18, "RATE"),
        _tape_row("IR.UST.10Y", "ABS_BP", d2, 4.20, "RATE"),
        _tape_row("EQ.SPY", "LOG", d1, 630.0),
        _tape_row("EQ.SPY", "LOG", d2, 637.10),
        _tape_row("VOL.SPX.IV30", "ABS", d1, 15.0, "VOL"),
        _tape_row("VOL.SPX.IV30", "ABS", d2, 14.62, "VOL"),
        _tape_row("FX.EURUSD", "LOG", d2, 1.0942),           # single print: change None
    ]
    t = schemas.build_factors_latest(RUN, rows)
    # class order EQ, FX, then rates along the curve (2Y before 10Y), then vol
    assert [x.factor_code for x in t.ticks] == [
        "EQ.SPY", "FX.EURUSD", "IR.UST.2Y", "IR.UST.10Y", "VOL.SPX.IV30"]
    by = {x.factor_code: x for x in t.ticks}
    assert by["EQ.SPY"].change == pytest.approx(637.10 / 630.0 - 1, abs=1e-6)
    assert by["EQ.SPY"].unit == "%"
    assert by["IR.UST.2Y"].change == pytest.approx(-3.0)     # (3.91-3.94)*100 bp
    assert by["IR.UST.2Y"].unit == "bp"
    assert by["VOL.SPX.IV30"].change == pytest.approx(-0.38)
    assert by["VOL.SPX.IV30"].unit == "pt"
    assert by["FX.EURUSD"].change is None and by["FX.EURUSD"].level == 1.0942


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


def test_routes_503_not_bare_500_when_connect_fails():
    """Connect-time failures must surface as a handled 503 (which still gets
    CORS headers), not an unhandled 500 that bypasses CORSMiddleware."""
    from sqlalchemy.exc import OperationalError

    class _DownEngine:
        def connect(self):
            raise OperationalError("SELECT 1", {}, Exception("down"))

    app.state.engine = _DownEngine()
    r = TestClient(app).get("/api/v1/meta", headers={"Origin": "http://localhost:5173"})
    assert r.status_code == 503 and "unreachable" in r.json()["detail"]
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


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


def test_exposures_endpoint(client, monkeypatch):
    monkeypatch.setattr(queries, "resolve_run", _fake_resolve())
    monkeypatch.setattr(queries, "exposure_rows", lambda conn, run_id: EXPOSURE_ROWS)
    body = client.get("/api/v1/risk/exposures").json()
    assert body["unit"] == "USD per 1bp" and len(body["rows"]) == 3
    assert body["vega"][0]["desk_code"] == "EQUITY"
    assert body["rows"][0]["desk_code"] == "FIRM"


def test_pla_endpoint_and_insufficient_pairs(client, monkeypatch):
    monkeypatch.setattr(queries, "resolve_run", _fake_resolve())
    monkeypatch.setattr(queries, "desk_exists", lambda conn, code: True)
    monkeypatch.setattr(queries, "pla_series", lambda conn, scope, end, window: PLA_ROWS)
    body = client.get("/api/v1/backtest/pla?scope=EQUITY").json()
    assert body["zone"] == "GREEN" and body["n_obs"] == 100

    monkeypatch.setattr(queries, "pla_series", lambda conn, scope, end, window: [])
    assert client.get("/api/v1/backtest/pla").status_code == 404


def test_backtest_endpoint_insufficient_history(client, monkeypatch):
    monkeypatch.setattr(queries, "resolve_run", _fake_resolve())
    monkeypatch.setattr(queries, "desk_exists", lambda conn, code: True)
    monkeypatch.setattr(queries, "backtest_series",
                        lambda conn, scope, measure, end, window: [])
    assert client.get("/api/v1/backtest/summary").status_code == 404


def test_scenario_catalog_endpoint(client, monkeypatch):
    monkeypatch.setattr(queries, "scenario_catalog_rows", lambda conn: CATALOG_ROWS)
    r = client.get("/api/v1/scenarios")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    assert [s["scenario_code"] for s in r.json()["scenarios"]] == [
        "BEAR_STEEPENER", "GFC_2008"]


def test_modeldoc_endpoint_serves_committed_doc(client):
    body = client.get("/api/v1/modeldoc").json()
    assert body["markdown"].lstrip().startswith("#")             # the real docs/model_doc.md
    assert "Model" in body["markdown"][:200]


def test_modeldoc_404_when_file_missing(client, monkeypatch):
    from api import main
    from api.deps import Settings
    monkeypatch.setattr(main, "get_settings",
                        lambda: Settings(model_doc_path="docs/does_not_exist.md"))
    assert client.get("/api/v1/modeldoc").status_code == 404


def test_movers_endpoint_and_cache_pinning(client, monkeypatch):
    monkeypatch.setattr(queries, "resolve_run", _fake_resolve())
    monkeypatch.setattr(queries, "risk_rows",
                        lambda conn, run_id: ROWS if run_id == RUN["run_id"] else MOVERS_PREV_ROWS)
    monkeypatch.setattr(queries, "desk_factor_codes", lambda conn: DESK_FACTORS)
    monkeypatch.setattr(queries, "factor_move_rows", lambda conn, end, lookback=61: MOVE_ROWS)
    body = client.get("/api/v1/risk/movers").json()
    assert body["rows"][0]["desk_code"] == "RATES"
    assert body["rows"][0]["drivers"][0] == "VOL.SPX.IV30 +2.1pt"
    pinned = client.get(f"/api/v1/risk/movers?as_of={AS_OF.isoformat()}")
    assert "immutable" in pinned.headers["cache-control"]


def _desk_row_stub(conn, code):
    if code in ("EQUITY", "FIRM"):
        return {"desk_code": code, "desk_name": code.title(), "is_aggregate": code == "FIRM"}
    return None


def test_desk_decomposition_endpoint(client, monkeypatch):
    monkeypatch.setattr(queries, "resolve_run", _fake_resolve())
    monkeypatch.setattr(queries, "desk_row", _desk_row_stub)
    monkeypatch.setattr(queries, "risk_rows", lambda conn, run_id: ROWS)
    monkeypatch.setattr(queries, "desk_position_rows",
                        lambda conn, run_id, desk_code: COMP_ROWS)
    monkeypatch.setattr(queries, "exposure_rows", lambda conn, run_id: DESK_EXPOSURES)
    body = client.get("/api/v1/desks/equity/decomposition").json()    # case-insensitive
    assert body["desk_code"] == "EQUITY" and body["var_hs_1d"] == 50.0
    assert body["buckets"][0]["factor_class"] == "EQ"
    assert client.get("/api/v1/desks/CREDIT/decomposition").status_code == 404
    assert client.get("/api/v1/desks/FIRM/decomposition").status_code == 404   # aggregate


def test_factors_latest_endpoint(client, monkeypatch):
    monkeypatch.setattr(queries, "resolve_run", _fake_resolve())
    d1, d2 = dt.date(2026, 8, 5), dt.date(2026, 8, 6)
    monkeypatch.setattr(queries, "factor_latest_rows", lambda conn, end: [
        _tape_row("EQ.SPY", "LOG", d1, 630.0), _tape_row("EQ.SPY", "LOG", d2, 637.10)])
    body = client.get("/api/v1/factors/latest").json()
    assert body["ticks"][0]["factor_code"] == "EQ.SPY"
    assert body["ticks"][0]["unit"] == "%"


def test_desk_positions_endpoint(client, monkeypatch):
    monkeypatch.setattr(queries, "resolve_run", _fake_resolve())
    monkeypatch.setattr(queries, "desk_row", _desk_row_stub)
    monkeypatch.setattr(queries, "desk_position_rows",
                        lambda conn, run_id, desk_code: COMP_ROWS)
    body = client.get("/api/v1/desks/EQUITY/positions").json()
    assert [p["ticker"] for p in body["positions"]] == ["SPY", "NVDA", "SPY_PUT_95"]
    assert body["positions"][2]["option_type"] == "PUT"
