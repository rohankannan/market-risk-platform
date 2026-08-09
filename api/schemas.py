"""Pydantic response models plus their builders, one per route.

Builders are pure - result-table rows in, model out - so every response shape
has a known-answer test with no database. Conventions: VaR/ES are positive
potential loss in USD; P&L and scenario impacts are signed.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any, Literal

from pydantic import BaseModel, Field

from risk_engine.backtest import (
    LikelihoodRatioTest,
    basel_traffic_light,
    christoffersen_conditional_coverage,
    christoffersen_independence,
    kupiec_pof,
)
from risk_engine.config import DEFAULT_CONFIG as CFG
from risk_engine.curve import NODE_TENORS
from risk_engine.pla import pla_test

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


# ---------------------------------------------------------------- factor tape

class FactorTick(BaseModel):
    factor_code: str
    factor_type: str
    level: float
    change: float | None                    # day move: LOG as pct-decimal, ABS_BP in bp, ABS in pts
    unit: Literal["%", "bp", "pt"]


class FactorsLatest(BaseModel):
    as_of: dt.date
    run_id: int
    ticks: list[FactorTick]                 # EQ, FX, rates by tenor, vol - tape order


_UNIT_OF = {"LOG": "%", "ABS_BP": "bp", "ABS": "pt"}
_CLASS_ORDER = {"EQ": 0, "FX": 1, "IR": 2, "VOL": 3}


def _tape_sort_key(code: str) -> tuple:
    cls = code.split(".")[0]
    # rates read left-to-right along the curve, not alphabetically
    tenor = NODE_TENORS.get(code, 0.0) if cls == "IR" else 0.0
    return (_CLASS_ORDER.get(cls, 9), tenor, code)


def build_factors_latest(run: dict, rows: list[dict]) -> FactorsLatest:
    """rows: date-ordered (factor_code, return_conv, obs_date, value) pairs, at
    most two per factor. A factor with a single observation ticks with change
    None rather than dropping off the tape."""
    by_factor: dict[str, list[dict]] = {}
    for r in rows:
        by_factor.setdefault(r["factor_code"], []).append(r)
    ticks = []
    for code in sorted(by_factor, key=_tape_sort_key):
        fr = by_factor[code]
        last = fr[-1]
        change: float | None = None
        if len(fr) == 2:
            prev, curr = fr[0]["value"], fr[1]["value"]
            conv = last["return_conv"]
            if conv == "LOG":
                change = round(curr / prev - 1.0, 6)
            elif conv == "ABS_BP":
                change = round((curr - prev) * 100.0, 2)
            else:
                change = round(curr - prev, 4)
        ticks.append(FactorTick(factor_code=code, factor_type=last["factor_type"],
                                level=last["value"], change=change,
                                unit=_UNIT_OF[last["return_conv"]]))
    return FactorsLatest(as_of=run["run_date"], run_id=run["run_id"], ticks=ticks)


# ---------------------------------------------------------------- movers

class MoverRow(BaseModel):
    desk_code: str
    delta_usd: float                        # signed 1d HS VaR change vs the prior run
    delta_pct: float | None
    drivers: list[str]                      # convention-formatted, largest normalized move first


class RiskMovers(BaseModel):
    as_of: dt.date
    run_id: int
    prev_date: dt.date | None               # None on the first run (rows empty then)
    rows: list[MoverRow]                    # sorted by |delta_usd| desc


def _format_move(conv: str, code: str, r: float) -> str:
    if conv == "LOG":
        return f"{code} {math.exp(r) - 1.0:+.1%}"
    if conv == "ABS_BP":
        return f"{code} {r:+.0f}bp"
    return f"{code} {r:+.1f}pt"


def _factor_day_moves(move_rows: list[dict],
                      run_date: dt.date) -> dict[str, tuple[str, float]]:
    """factor_code -> (formatted day move, |move| / trailing mean |move|). The
    normalization makes moves comparable across conventions - an outsized rates
    day and an outsized equity day rank together despite bp-vs-percent units.
    A factor with no print on run_date carried its level (what the EOD fill row
    encodes), so its day move is zero, not the last move it did print."""
    by_factor: dict[str, list[dict]] = {}
    for r in move_rows:
        by_factor.setdefault(r["factor_code"], []).append(r)
    out: dict[str, tuple[str, float]] = {}
    for code, rows in by_factor.items():
        vals = [r["value"] for r in rows]                 # date-ordered by the query
        if len(vals) < 2:
            continue
        conv = rows[0]["return_conv"]
        if conv == "LOG":
            rets = [math.log(b / a) for a, b in zip(vals, vals[1:])]
        elif conv == "ABS_BP":
            rets = [(b - a) * 100.0 for a, b in zip(vals, vals[1:])]
        else:
            rets = [b - a for a, b in zip(vals, vals[1:])]
        if rows[-1]["obs_date"] < run_date:
            day, prior = 0.0, rets
        else:
            day, prior = rets[-1], rets[:-1]
        typical = sum(abs(x) for x in prior) / len(prior) if prior else 0.0
        out[code] = (_format_move(conv, code, day),
                     abs(day) / typical if typical > 0 else 0.0)
    return out


def build_risk_movers(run: dict, curr_rows: list[dict], prev_rows: list[dict],
                      prev_date: dt.date | None, desk_factors: list[dict],
                      move_rows: list[dict]) -> RiskMovers:
    """Day-over-day 1d HS VaR deltas per standalone desk, each with the desk's
    top-3 factor moves (normalized ranking) as driver strings."""
    curr = {r["desk_code"]: r["value"] for r in curr_rows
            if r["measure"] == "VAR_HS" and r["horizon_days"] == 1 and not r["is_aggregate"]}
    prev = {r["desk_code"]: r["value"] for r in prev_rows
            if r["measure"] == "VAR_HS" and r["horizon_days"] == 1}
    moves = _factor_day_moves(move_rows, run["run_date"])
    factors_of: dict[str, list[str]] = {}
    for r in desk_factors:
        factors_of.setdefault(r["desk_code"], []).append(r["factor_code"])

    rows = []
    for code, value in curr.items():
        if code not in prev:
            continue
        delta = round(value - prev[code], 2)
        ranked = sorted((f for f in factors_of.get(code, []) if f in moves),
                        key=lambda f: moves[f][1], reverse=True)
        rows.append(MoverRow(desk_code=code, delta_usd=delta,
                             delta_pct=round(delta / prev[code], 4) if prev[code] else None,
                             drivers=[moves[f][0] for f in ranked[:3]]))
    rows.sort(key=lambda r: abs(r.delta_usd), reverse=True)
    return RiskMovers(as_of=run["run_date"], run_id=run["run_id"],
                      prev_date=prev_date, rows=rows)


# ---------------------------------------------------------------- exposures

class KeyRateRow(BaseModel):
    desk_code: str
    factor_code: str
    tenor_years: float
    value: float                            # USD per +1bp bump of the par node


class VegaExposure(BaseModel):
    desk_code: str
    factor_code: str
    value: float                            # USD per 1 vol point


class KeyRateExposures(BaseModel):
    as_of: dt.date
    run_id: int
    unit: str
    rows: list[KeyRateRow]                  # empty for runs that skip the curve step
    vega: list[VegaExposure]                # empty until the options sleeve has run


def build_key_rate_exposures(run: dict, rows: list[dict]) -> KeyRateExposures:
    krd = [KeyRateRow(desk_code=r["desk_code"], factor_code=r["factor_code"],
                      tenor_years=NODE_TENORS.get(r["factor_code"], 0.0),
                      value=r["value"])
           for r in rows if r.get("measure", "KRD_DV01") == "KRD_DV01"]
    krd.sort(key=lambda r: (not r.desk_code == "FIRM", r.desk_code, r.tenor_years))
    vega = [VegaExposure(desk_code=r["desk_code"], factor_code=r["factor_code"],
                         value=r["value"])
            for r in rows if r.get("measure") == "VEGA"]
    vega.sort(key=lambda r: (not r.desk_code == "FIRM", r.desk_code))
    return KeyRateExposures(as_of=run["run_date"], run_id=run["run_id"],
                            unit="USD per 1bp", rows=krd, vega=vega)


# ---------------------------------------------------------------- desk drill-down

class DeskBucket(BaseModel):
    factor_class: str                       # EQ / FX / IR (VOL reserved; options
    standalone_var: float                   # bucket by their delta leg)


class DeskExposure(BaseModel):
    measure: str                            # KRD_DV01 / VEGA / DELTA_USD
    factor_code: str
    tenor_years: float | None               # KRD rows only
    value: float


class DeskDecomposition(BaseModel):
    as_of: dt.date
    run_id: int
    desk_code: str
    desk_name: str
    var_hs_1d: float | None
    buckets: list[DeskBucket]               # standalone VaR summed by factor class, desc
    diversification: float | None           # desk VaR - sum of buckets; negative in practice
                                            # for this book, but empirical VaR is not
                                            # subadditive - clients must not assume the sign
    exposures: list[DeskExposure]           # this desk's rows only


class DeskPosition(BaseModel):
    ticker: str
    instrument_type: str
    quantity: float
    factor_class: str
    factor_code: str | None                 # primary (DELTA/DV01) mapping factor
    option_type: str | None                 # options only
    moneyness: float | None
    maturity_years: float | None            # bonds and options
    standalone_var: float
    component_es: float                     # per-desk Euler share; negative for hedges
    marginal_var: float                     # exact: desk VaR minus desk-without-position VaR
    pct_of_desk: float | None               # component_es / sum of the desk's components


class DeskPositions(BaseModel):
    as_of: dt.date
    run_id: int
    desk_code: str
    desk_name: str
    positions: list[DeskPosition]           # largest component first


def build_desk_decomposition(run: dict, desk: dict, risk_rows: list[dict],
                             comp_rows: list[dict],
                             exposure_rows: list[dict]) -> DeskDecomposition:
    """Waterfall identity by construction: buckets + diversification == the
    desk's 1d HS VaR exactly (all addends are the stored 2dp values)."""
    code = desk["desk_code"]
    var_hs = next((r["value"] for r in risk_rows
                   if r["desk_code"] == code and r["measure"] == "VAR_HS"
                   and r["horizon_days"] == 1), None)
    by_class: dict[str, float] = {}
    for r in comp_rows:
        by_class[r["factor_class"]] = by_class.get(r["factor_class"], 0.0) + r["standalone_var"]
    buckets = [DeskBucket(factor_class=k, standalone_var=round(v, 2))
               for k, v in sorted(by_class.items(), key=lambda kv: -kv[1])]
    div = (round(var_hs - sum(b.standalone_var for b in buckets), 2)
           if buckets and var_hs is not None else None)
    exposures = [DeskExposure(measure=r["measure"], factor_code=r["factor_code"],
                              tenor_years=(NODE_TENORS.get(r["factor_code"])
                                           if r["measure"] == "KRD_DV01" else None),
                              value=r["value"])
                 for r in exposure_rows if r["desk_code"] == code]
    return DeskDecomposition(as_of=run["run_date"], run_id=run["run_id"], desk_code=code,
                             desk_name=desk["desk_name"], var_hs_1d=var_hs,
                             buckets=buckets, diversification=div, exposures=exposures)


def build_desk_positions(run: dict, desk: dict, comp_rows: list[dict]) -> DeskPositions:
    total = sum(r["component_es"] for r in comp_rows)
    positions = [DeskPosition(**{k: r[k] for k in (
                     "ticker", "instrument_type", "quantity", "factor_class", "factor_code",
                     "option_type", "moneyness", "maturity_years", "standalone_var",
                     "component_es", "marginal_var")},
                 pct_of_desk=round(r["component_es"] / total, 4) if total else None)
                 for r in comp_rows]
    positions.sort(key=lambda p: p.component_es, reverse=True)
    return DeskPositions(as_of=run["run_date"], run_id=run["run_id"],
                         desk_code=desk["desk_code"], desk_name=desk["desk_name"],
                         positions=positions)


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


# ---------------------------------------------------------------- pla

class PlaPoint(BaseModel):
    date: dt.date
    hpl: float
    rtpl: float


class PlaSummary(BaseModel):
    scope: str
    window: int                             # requested; n_obs is what history allowed
    start: dt.date
    end: dt.date
    n_obs: int
    spearman: float
    ks: float
    zone: Literal["GREEN", "AMBER", "RED"]  # MAR32.41 thresholds
    points: list[PlaPoint]


def build_pla_summary(scope: str, window: int, rows: list[dict]) -> PlaSummary:
    """rows: date-ordered {pnl_date, hpl, rtpl} pairs (needs pla.MIN_OBS)."""
    res = pla_test([r["hpl"] for r in rows], [r["rtpl"] for r in rows])
    return PlaSummary(scope=scope, window=window,
                      start=rows[0]["pnl_date"], end=rows[-1]["pnl_date"],
                      n_obs=res.n_obs, spearman=round(res.spearman, 4),
                      ks=round(res.ks, 4), zone=res.zone,
                      points=[PlaPoint(date=r["pnl_date"], hpl=r["hpl"], rtpl=r["rtpl"])
                              for r in rows])


# ---------------------------------------------------------------- scenarios

class ScenarioShock(BaseModel):
    factor_code: str
    shock_type: str                         # RELATIVE / ABSOLUTE_BP / ABSOLUTE
    shock_value: float


class ScenarioSpec(BaseModel):
    scenario_code: str
    scenario_name: str
    scenario_type: str                      # HISTORICAL_REPLAY / HYPOTHETICAL / REGULATORY
    window_start: dt.date | None            # replays only
    window_end: dt.date | None
    description: str | None
    shocks: list[ScenarioShock]             # empty for replays (moves come from the window)


class ScenarioCatalog(BaseModel):
    scenarios: list[ScenarioSpec]


def build_scenario_catalog(rows: list[dict]) -> ScenarioCatalog:
    grouped: dict[str, dict] = {}
    for r in rows:
        g = grouped.setdefault(r["scenario_code"], {"meta": r, "shocks": []})
        if r["factor_code"] is not None:
            g["shocks"].append(ScenarioShock(factor_code=r["factor_code"],
                                             shock_type=r["shock_type"],
                                             shock_value=r["shock_value"]))
    return ScenarioCatalog(scenarios=[
        ScenarioSpec(scenario_code=code, scenario_name=g["meta"]["scenario_name"],
                     scenario_type=g["meta"]["scenario_type"],
                     window_start=g["meta"]["window_start"],
                     window_end=g["meta"]["window_end"],
                     description=g["meta"]["description"], shocks=g["shocks"])
        for code, g in sorted(grouped.items())])


# ---------------------------------------------------------------- what-if

class WhatIfAdjustment(BaseModel):
    ticker: str
    scale: float                            # multiplier on the booked quantity


class WhatIfShock(BaseModel):
    factor_code: str
    shock_type: Literal["RELATIVE", "ABSOLUTE_BP", "ABSOLUTE"]
    value: float                            # in the factor's own convention


class WhatIfRequest(BaseModel):
    # unlisted positions stay at 1.0 and unlisted factors are shocked by zero
    # (the catalog's documented fill rule); the bounds are far above any real
    # book and keep the unauthenticated compute path from swallowing huge bodies
    adjustments: list[WhatIfAdjustment] = Field(default_factory=list, max_length=64)
    shocks: list[WhatIfShock] = Field(default_factory=list, max_length=64)


class WhatIfDesk(BaseModel):
    desk_code: str
    is_aggregate: bool
    var_hs_1d: float                        # risk of the EDITED book at today's levels
    es_975_1d: float
    shock_pnl: float | None                 # instantaneous P&L of the shock on that book
    official_var_hs_1d: float | None        # the batch's number for the delta
    var_delta: float | None


class WhatIfPosition(BaseModel):
    ticker: str
    desk_code: str
    factor_class: str
    quantity: float                         # after scaling
    scale: float
    standalone_var: float
    component_es: float
    marginal_var: float


class WhatIfResult(BaseModel):
    as_of: dt.date
    run_id: int
    hypothetical: Literal[True]             # what-if numbers are never official
    desks: list[WhatIfDesk]
    positions: list[WhatIfPosition]
    zeroed: list[str]
    shocked_factors: list[str]


class FlashDesk(BaseModel):
    desk_code: str
    is_aggregate: bool
    flash_pnl: float                        # indicative P&L since the EOD close


class FlashFactor(BaseModel):
    factor_code: str
    level: float
    close: float
    move: float                             # in the factor's own convention
    carried: bool                           # true when the close was used
    note: str | None                        # why, when carried


class FlashMarks(BaseModel):
    as_of: dt.date
    run_id: int
    indicative: Literal[True]               # never the official record
    quoted_at: dt.datetime | None
    live_factors: int
    total_factors: int
    rejected_factors: list[str]             # quotes refused as implausible
    cached: bool
    desks: list[FlashDesk]
    factors: list[FlashFactor]


class ScenarioShockVector(BaseModel):
    scenario_code: str
    scenario_type: str
    shocks: list[WhatIfShock]               # ready to load into the sandbox


def build_whatif_result(run: dict, computed: dict,
                        official_rows: list[dict]) -> WhatIfResult:
    official = {r["desk_code"]: r["value"] for r in official_rows
                if r["measure"] == "VAR_HS" and r["horizon_days"] == 1}
    desks = []
    for d in computed["desks"]:
        base = official.get(d["desk_code"])
        desks.append(WhatIfDesk(
            **d, official_var_hs_1d=base,
            var_delta=round(d["var_hs_1d"] - base, 2) if base is not None else None))
    desks.sort(key=lambda x: (not x.is_aggregate, x.desk_code))
    return WhatIfResult(as_of=run["run_date"], run_id=run["run_id"], hypothetical=True,
                        desks=desks,
                        positions=[WhatIfPosition(**p) for p in computed["positions"]],
                        zeroed=computed["zeroed"],
                        shocked_factors=computed.get("shocked_factors", []))


# ---------------------------------------------------------------- model doc

class ModelDoc(BaseModel):
    markdown: str


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
