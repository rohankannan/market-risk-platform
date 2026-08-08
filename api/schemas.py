"""Pydantic response models plus their builders, one per route.

Builders are pure - result-table rows in, model out - so every response shape
has a known-answer test with no database. Conventions: VaR/ES are positive
potential loss in USD; P&L and scenario impacts are signed.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel

from risk_engine.backtest import (
    LikelihoodRatioTest,
    basel_traffic_light,
    christoffersen_conditional_coverage,
    christoffersen_independence,
    kupiec_pof,
)
from risk_engine.config import DEFAULT_CONFIG as CFG
from risk_engine.curve import NODE_TENORS

# Basel traffic-light zone boundaries are calibrated to a 250-day window;
# other realized window lengths are reported but flagged non-regulatory.
BASEL_WINDOW_DAYS = 250


# ---------------------------------------------------------------- meta / health

class Healthz(BaseModel):
    status: Literal["ok"]
    database: Literal["ok"]


class Desk(BaseModel):
    desk_code: str
    desk_name: str
    is_aggregate: bool


class Meta(BaseModel):
    latest_as_of: dt.date | None
    batch_status: str                       # not_yet_run / SUCCESS / PARTIAL
    batch_type: str | None                  # EOD / BACKFILL
    batch_completed_at: dt.datetime | None
    code_version: str | None
    available_dates: list[dt.date]
    desks: list[Desk]


# ---------------------------------------------------------------- risk summary

class DeskRisk(BaseModel):
    desk_code: str
    desk_name: str
    is_aggregate: bool
    var_hs_1d: float | None = None
    var_fhs_1d: float | None = None
    var_hs_10d: float | None = None         # EOD runs only; backfill writes 1d
    var_fhs_10d: float | None = None
    es_975_1d: float | None = None
    es_975_10d: float | None = None
    es_stressed_1d: float | None = None
    var_dod: float | None = None            # 1d HS VaR change vs the prior run, signed
    limit_value: float | None = None        # VAR_HS limit in force on as_of
    utilization: float | None = None
    limit_status: Literal["OK", "WARN", "BREACH"] | None = None


class RiskSummary(BaseModel):
    as_of: dt.date
    run_id: int
    run_type: str
    status: str
    diversification_benefit: float | None   # 1 - firm VaR / sum of desk VaRs (HS 1d)
    desks: list[DeskRisk]


_H10 = CFG.reporting_horizon_days
_MEASURE_FIELDS = {
    ("VAR_HS", 1): "var_hs_1d", ("VAR_FHS", 1): "var_fhs_1d",
    ("VAR_HS", _H10): "var_hs_10d", ("VAR_FHS", _H10): "var_fhs_10d",
    ("ES_975", 1): "es_975_1d", ("ES_975", _H10): "es_975_10d",
    ("ES_STRESSED", 1): "es_stressed_1d",
}


def build_risk_summary(run: dict, rows: list[dict], limits: list[dict],
                       prev_rows: list[dict]) -> RiskSummary:
    """Limits attach to VAR_HS 1d only (that is what the limits table carries);
    utilization thresholds mirror q2_limit_utilization: WARN at warn_threshold,
    BREACH above the limit."""
    lim = {r["desk_code"]: r for r in limits if r["measure"] == "VAR_HS"}
    prev_hs = {r["desk_code"]: r["value"] for r in prev_rows
               if r["measure"] == "VAR_HS" and r["horizon_days"] == 1}

    names: dict[str, tuple[str, bool]] = {}
    values: dict[str, dict[str, float]] = {}
    for r in rows:
        names[r["desk_code"]] = (r["desk_name"], r["is_aggregate"])
        field = _MEASURE_FIELDS.get((r["measure"], r["horizon_days"]))
        if field:
            values.setdefault(r["desk_code"], {})[field] = r["value"]

    desks = []
    for code in sorted(values, key=lambda c: (not names[c][1], c)):     # FIRM first
        v = dict(values[code])
        var_hs = v.get("var_hs_1d")
        if var_hs is not None and code in prev_hs:
            v["var_dod"] = round(var_hs - prev_hs[code], 2)
        if var_hs is not None and code in lim:
            row = lim[code]
            v["limit_value"] = row["limit_value"]
            v["utilization"] = round(var_hs / row["limit_value"], 4)
            v["limit_status"] = ("BREACH" if var_hs > row["limit_value"]
                                 else "WARN" if var_hs >= row["warn_threshold"] * row["limit_value"]
                                 else "OK")
        desks.append(DeskRisk(desk_code=code, desk_name=names[code][0],
                              is_aggregate=names[code][1], **v))

    firm = next((d.var_hs_1d for d in desks if d.is_aggregate), None)
    standalone = [d.var_hs_1d for d in desks if not d.is_aggregate and d.var_hs_1d is not None]
    div = (round(1.0 - firm / sum(standalone), 4)
           if firm is not None and standalone and sum(standalone) > 0 else None)
    return RiskSummary(as_of=run["run_date"], run_id=run["run_id"], run_type=run["run_type"],
                       status=run["status"], diversification_benefit=div, desks=desks)


# ---------------------------------------------------------------- history

class HistoryPoint(BaseModel):
    date: dt.date
    var_hs: float | None
    var_fhs: float | None
    es_975: float | None
    pnl: float | None                       # hypothetical P&L; None on the first day
    exception_hs: bool
    exception_fhs: bool


class RiskHistory(BaseModel):
    scope: str
    start: dt.date
    end: dt.date
    points: list[HistoryPoint]


def build_history(scope: str, risk_rows: list[dict], pnl_rows: list[dict],
                  exc_rows: list[dict]) -> RiskHistory:
    field_of = {"VAR_HS": "var_hs", "VAR_FHS": "var_fhs", "ES_975": "es_975"}
    by_date: dict[dt.date, dict[str, float]] = {}
    for r in risk_rows:
        field = field_of.get(r["measure"])
        if field:
            by_date.setdefault(r["obs_date"], {})[field] = r["value"]
    pnl = {r["pnl_date"]: r["amount"] for r in pnl_rows}
    exc = {(r["obs_date"], r["measure"]) for r in exc_rows}
    points = [HistoryPoint(date=d, var_hs=v.get("var_hs"), var_fhs=v.get("var_fhs"),
                           es_975=v.get("es_975"), pnl=pnl.get(d),
                           exception_hs=(d, "VAR_HS") in exc,
                           exception_fhs=(d, "VAR_FHS") in exc)
              for d, v in sorted(by_date.items())]
    return RiskHistory(scope=scope, start=points[0].date, end=points[-1].date, points=points)


# ---------------------------------------------------------------- exposures

class KeyRateRow(BaseModel):
    desk_code: str
    factor_code: str
    tenor_years: float
    value: float                            # USD per +1bp bump of the par node


class KeyRateExposures(BaseModel):
    as_of: dt.date
    run_id: int
    unit: str
    rows: list[KeyRateRow]                  # empty for runs that skip the curve step


def build_key_rate_exposures(run: dict, rows: list[dict]) -> KeyRateExposures:
    built = [KeyRateRow(desk_code=r["desk_code"], factor_code=r["factor_code"],
                        tenor_years=NODE_TENORS.get(r["factor_code"], 0.0),
                        value=r["value"]) for r in rows]
    built.sort(key=lambda r: (not r.desk_code == "FIRM", r.desk_code, r.tenor_years))
    return KeyRateExposures(as_of=run["run_date"], run_id=run["run_id"],
                            unit="USD per 1bp", rows=built)


# ---------------------------------------------------------------- backtest

class LRTest(BaseModel):
    name: str
    statistic: float
    p_value: float
    df: int
    reject_5pct: bool
    details: dict[str, Any]

    @classmethod
    def from_result(cls, t: LikelihoodRatioTest) -> LRTest:
        return cls(name=t.name, statistic=t.statistic, p_value=t.p_value, df=t.df,
                   reject_5pct=t.reject_5pct, details=t.details)


class TrafficLight(BaseModel):
    zone: Literal["GREEN", "AMBER", "RED"]
    n_exceptions: int
    n_obs: int
    plus_factor: float
    multiplier: float
    regulatory_window: bool                 # true only when n_obs == 250


class BacktestException(BaseModel):
    date: dt.date
    var_value: float
    pnl_value: float


class BacktestSummary(BaseModel):
    scope: str
    model: Literal["HS", "FHS"]
    measure: str
    window: int                             # requested; n_obs is what history allowed
    start: dt.date
    end: dt.date
    n_obs: int
    n_exceptions: int
    expected_exceptions: float
    kupiec: LRTest
    christoffersen_independence: LRTest
    christoffersen_cc: LRTest
    traffic_light: TrafficLight
    exceptions: list[BacktestException]


def build_backtest_summary(scope: str, model: str, series: list[dict],
                           window: int) -> BacktestSummary:
    """series: date-ordered rows {pnl_date, pnl, var_value, pnl_value}; a
    non-null var_value marks an exception day (needs >= 2 rows)."""
    indicators = [r["var_value"] is not None for r in series]
    n, x = len(indicators), sum(indicators)
    p = round(1.0 - CFG.alpha_var, 10)
    tl = basel_traffic_light(x, n)
    return BacktestSummary(
        scope=scope, model=model, measure=f"VAR_{model}", window=window,
        start=series[0]["pnl_date"], end=series[-1]["pnl_date"],
        n_obs=n, n_exceptions=x, expected_exceptions=round(n * p, 2),
        kupiec=LRTest.from_result(kupiec_pof(x, n, p=p)),
        christoffersen_independence=LRTest.from_result(christoffersen_independence(indicators)),
        christoffersen_cc=LRTest.from_result(christoffersen_conditional_coverage(indicators, p=p)),
        traffic_light=TrafficLight(zone=tl.zone, n_exceptions=tl.n_exceptions, n_obs=tl.n_obs,
                                   plus_factor=tl.plus_factor, multiplier=tl.multiplier,
                                   regulatory_window=(n == BASEL_WINDOW_DAYS)),
        exceptions=[BacktestException(date=r["pnl_date"], var_value=r["var_value"],
                                      pnl_value=r["pnl_value"])
                    for r in series if r["var_value"] is not None])


# ---------------------------------------------------------------- scenarios

class ScenarioResult(BaseModel):
    scenario_code: str
    scenario_name: str
    scenario_type: str                      # HISTORICAL_REPLAY / HYPOTHETICAL / REGULATORY
    window_start: dt.date | None            # replays only
    window_end: dt.date | None
    description: str | None
    impacts: dict[str, float]               # desk_code -> signed P&L impact
    firm_impact: float | None
    worst_desk: str | None                  # most negative standalone desk


class ScenarioResults(BaseModel):
    as_of: dt.date
    run_id: int
    results: list[ScenarioResult]


def build_scenario_results(run: dict, rows: list[dict]) -> ScenarioResults:
    grouped: dict[str, dict] = {}
    for r in rows:
        g = grouped.setdefault(r["scenario_code"], {"meta": r, "impacts": {}, "agg": {}})
        g["impacts"][r["desk_code"]] = r["pnl_impact"]
        g["agg"][r["desk_code"]] = r["is_aggregate"]
    results = []
    for code, g in grouped.items():
        m = g["meta"]
        standalone = {k: v for k, v in g["impacts"].items() if not g["agg"][k]}
        firm = next((v for k, v in g["impacts"].items() if g["agg"][k]), None)
        results.append(ScenarioResult(
            scenario_code=code, scenario_name=m["scenario_name"],
            scenario_type=m["scenario_type"], window_start=m["window_start"],
            window_end=m["window_end"], description=m["description"],
            impacts=g["impacts"], firm_impact=firm,
            worst_desk=min(standalone, key=standalone.get) if standalone else None))
    results.sort(key=lambda r: (r.firm_impact is None, r.firm_impact))    # worst first
    return ScenarioResults(as_of=run["run_date"], run_id=run["run_id"], results=results)
