"""End-of-day pipeline - the nightly cycle a bank's market-risk desk runs.

    python -m risk.jobs.eod run      --date 2026-08-06 [--steps ingest,dq,risk,scenarios]
                                     [--force] [--no-fetch]
    python -m risk.jobs.eod backfill --start 2023-01-02 --end 2026-08-06 [--resume] [--force]

Steps: ingest (top up market_data from sources; snapshot-seeded history never
depends on this) -> dq (checks -> dq_issues; BLOCK downgrades the run to
PARTIAL) -> risk (HS/FHS VaR + ES at 1d/10d, stressed ES, hypothetical and
risk-theoretical P&L, key-rate/vega exposures, flash check, exception check vs
the prior run's VaR) -> scenarios (2008/2020 replays + hypothetical
shocks on today's book). One transaction per run; risk_runs doubles as the
status table; re-runs are idempotent via the (run_date, run_type) claim.

Backfill computes the whole window in memory (the vectorized engine does ~750
days in seconds) and writes one BACKFILL run row per day; --resume skips days
already SUCCESS. DQ and scenarios run only in EOD mode.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time

import pandas as pd
import yaml
from sqlalchemy import text

from risk import db
from risk.marketdata import fetch_fred_series, fetch_stooq_daily, fetch_yfinance_batch
from risk_engine import dq
from risk_engine.backfill import run_backfill
from risk_engine.backtest import basel_traffic_light
from risk_engine.config import DEFAULT_CONFIG as CFG
from risk_engine.curve import NODE_TENORS, key_rate_dv01s
from risk_engine.engine import aggregate, position_components, revalue
from risk_engine.es import stressed_window
from risk_engine.factors import align_levels, build_scenarios_fhs, build_scenarios_hs, to_returns
from risk_engine.options import bs_delta, bs_vega
from risk_engine.stress import REPLAY_WINDOWS, apply_scenario, compute_replay_shock
from risk_engine.var import ewma_vol_forecast, ewma_volatility, var_es_from_pnl

SCENARIOS_YAML = "scenarios/hypothetical_shocks.yaml"


# ---------------------------------------------------------------- steps

def step_ingest(ctx: dict) -> None:
    """Fetch missing observations up to run_date per factor, using each factor's
    configured source with fallback. --no-fetch skips network entirely (the
    seeded history is then all there is - DQ decides if that's good enough)."""
    if not ctx["fetch_live"]:
        print("[eod] ingest: --no-fetch, skipping network top-up")
        return
    conn, run_date = ctx["conn"], ctx["run_date"]
    meta = ctx["factor_meta"]
    # windows anchor at the last REAL print: filled dates stay in the request
    # until the vendor publishes them, so fills are provisional, not permanent
    last = db.last_real_obs(conn)

    rows: list[dict] = []
    eq = meta[meta["source"] == "YFINANCE"]
    need_eq = eq[[last.get(fid, dt.date(1900, 1, 1)) < run_date for fid in eq["factor_id"]]]
    if len(need_eq):
        start = min(last.get(fid, run_date) for fid in need_eq["factor_id"]) + dt.timedelta(days=1)
        try:
            data = fetch_yfinance_batch(list(need_eq["source_symbol"]), start, run_date)
        except Exception as exc:
            print(f"[eod] ingest: yfinance batch failed ({exc}); trying Stooq per ticker",
                  file=sys.stderr)
            data = {}
        for spec in need_eq.itertuples():
            df = data.get(spec.source_symbol)
            if df is not None and not df.empty:
                for ts, v in df["adj_close"].dropna().items():
                    rows.append({"factor_id": spec.factor_id, "obs_date": ts.date(),
                                 "value": float(v), "source": "YFINANCE",
                                 "is_ffilled": False, "ffill_age": 0})
            elif spec.fallback_source == "STOOQ" and spec.fallback_symbol:
                try:
                    s = fetch_stooq_daily(spec.fallback_symbol, start, run_date)
                    rows += [{"factor_id": spec.factor_id, "obs_date": ts.date(),
                              "value": float(v), "source": "STOOQ",
                              "is_ffilled": False, "ffill_age": 0} for ts, v in s.items()]
                except Exception as exc:
                    print(f"[eod] ingest: {spec.factor_code} Stooq fallback failed ({exc})",
                          file=sys.stderr)

    for spec in meta[meta["source"] == "FRED"].itertuples():
        lo = last.get(spec.factor_id, dt.date(1900, 1, 1))
        if lo >= run_date:
            continue
        try:
            s = fetch_fred_series(spec.source_symbol, lo + dt.timedelta(days=1), run_date)
            if spec.invert_on_ingest:
                s = 1.0 / s
            rows += [{"factor_id": spec.factor_id, "obs_date": ts.date(), "value": float(v),
                      "source": "FRED", "is_ffilled": False, "ffill_age": 0}
                     for ts, v in s.items()]
        except Exception as exc:
            print(f"[eod] ingest: {spec.factor_code} FRED fetch failed ({exc})", file=sys.stderr)

    # history that changed under our feet gets logged before the upsert
    # overwrites it - the restatement trail (--force on affected dates restates)
    existing = db.existing_market_values(
        conn, sorted({r["factor_id"] for r in rows}), sorted({r["obs_date"] for r in rows}))
    revisions = dq.detect_revisions(existing, rows)
    db.write_market_revisions(conn, ctx["run_id"], revisions)
    n = db.upsert_market_rows(ctx["conn"], rows)
    vendor = [r for r in revisions if r["revision_type"] == "VENDOR_REVISION"]
    print(f"[eod] ingest: upserted {n} observations, {len(revisions)} revision(s) logged")
    if vendor:
        print(f"[eod] ingest: {len(vendor)} VENDOR revision(s) touch stored history - "
              "restate affected dates with 'run --date <d> --force' (an EOD restatement "
              "outranks a backfill run for the same date in every read)", file=sys.stderr)


def step_dq(ctx: dict) -> None:
    """Completeness + bounded forward-fill (synthetic rows written back so
    downstream reads are simple), outliers, staleness, unit bounds."""
    conn, run_date, meta = ctx["conn"], ctx["run_date"], ctx["factor_meta"]
    fids = dict(zip(meta["factor_code"], meta["factor_id"]))
    ftypes = dict(zip(meta["factor_code"], meta["factor_type"]))
    convs = dict(zip(meta["factor_code"], meta["return_conv"]))
    limits = dict(zip(meta["factor_code"], meta["ffill_limit_days"]))

    levels = db.read_levels(conn, end=run_date)
    issues: list[dict] = []
    synthetic: list[dict] = []
    ts_run = pd.Timestamp(run_date)
    if ts_run not in levels.index:
        levels.loc[ts_run] = float("nan")
        levels = levels.sort_index()

    # re-runs: fills from a prior run already sit in market_data (is_ffilled=true);
    # re-emit their INFO issues so this run's DQ log stays complete after the
    # cascade delete of the old run's issues
    code_of = {fid: code for code, fid in fids.items()}
    for fid, age in conn.execute(text(
            "SELECT factor_id, ffill_age FROM market_data WHERE obs_date=:d AND is_ffilled"),
            {"d": run_date}).all():
        issues.append({"factor_code": code_of[fid], "check_name": "FFILL_LIMIT",
                       "severity": "INFO", "obs_date": run_date,
                       "detail": {"ffill_age": int(age), "carried_from_prior_run": True}})

    # staleness anchors at the last REAL print - measuring against a prior
    # synthetic fill would reset the clock nightly and let a dead source
    # forward-fill forever without ever tripping its cap
    real_last = db.last_real_obs(conn)
    for code in levels.columns:
        if pd.notna(levels.loc[ts_run, code]):
            continue
        prior = levels[code].dropna()
        prior = prior[prior.index < ts_run]
        real_anchor = real_last.get(fids[code])
        if prior.empty or real_anchor is None:
            issues.append({"factor_code": code, "check_name": "GAP", "severity": "BLOCK",
                           "obs_date": run_date, "detail": {"reason": "no history at all"}})
            continue
        age = len(pd.bdate_range(pd.Timestamp(real_anchor), ts_run)) - 1
        if age <= int(limits[code]):
            synthetic.append({"factor_id": fids[code], "obs_date": run_date,
                              "value": float(prior.iloc[-1]), "source": "FFILL",
                              "is_ffilled": True, "ffill_age": age})
            levels.loc[ts_run, code] = float(prior.iloc[-1])
            issues.append({"factor_code": code, "check_name": "FFILL_LIMIT", "severity": "INFO",
                           "obs_date": run_date, "detail": {"ffill_age": age}})
        else:
            issues.append({"factor_code": code, "check_name": "GAP", "severity": "BLOCK",
                           "obs_date": run_date,
                           "detail": {"stale_bdays": age, "limit": int(limits[code])}})

    window = levels.loc[:ts_run].tail(300)
    returns = to_returns(window, convs)
    issues += [{**i, "obs_date": run_date} for i in dq.check_outliers(returns, convs)]
    issues += [{**i, "obs_date": run_date} for i in dq.check_staleness(window, ftypes)]
    fx_med = dq.fx_trailing_median(window.iloc[:-1], ftypes)
    issues += [{**i, "obs_date": run_date}
               for i in dq.check_bounds(window.iloc[-1], ftypes, fx_med)]

    # synthetic rows keep the factor's own source; is_ffilled/ffill_age mark them
    src_of = dict(zip(meta["factor_id"], meta["source"]))
    for row in synthetic:
        row["source"] = src_of[row["factor_id"]]
    db.upsert_market_rows(conn, synthetic)
    db.write_dq_issues(conn, ctx["run_id"], issues, fids)

    ctx["dq_block"] = dq.has_block(issues)
    n_warn = sum(1 for i in issues if i["severity"] == "WARN")
    print(f"[eod] dq: {len(synthetic)} ffilled, {n_warn} WARN, "
          f"{'BLOCK -> run will be PARTIAL' if ctx['dq_block'] else 'no blocks'}")


def step_risk(ctx: dict) -> None:
    """HS + FHS VaR/ES (1d and sqrt-10 10d), stressed ES, clean P&L, exceptions."""
    conn, run_date, meta = ctx["conn"], ctx["run_date"], ctx["factor_meta"]
    book = db.read_book(conn)
    desks = db.desk_id_map(conn)
    convs = dict(zip(meta["factor_code"], meta["return_conv"]))
    limits = dict(zip(meta["factor_code"], meta["ffill_limit_days"]))

    levels_raw = db.read_levels(conn, end=run_date)
    levels, _ = align_levels(levels_raw, limits)
    returns = to_returns(levels, convs).dropna()
    levels = levels.loc[returns.index]
    ts_run = pd.Timestamp(run_date)
    if ts_run not in returns.index:
        raise RuntimeError(f"no aligned market data for {run_date} after DQ")
    lvl_t = levels.loc[ts_run]

    vols = ewma_volatility(returns, lam=CFG.lambda_ewma, seed_window=CFG.ewma_seed_window)
    fc = ewma_vol_forecast(returns.loc[:ts_run], lam=CFG.lambda_ewma,
                           seed_window=CFG.ewma_seed_window)
    scen = {"HS": build_scenarios_hs(returns, ts_run, CFG.lookback_days),
            "FHS": build_scenarios_fhs(returns, vols, fc, ts_run, CFG.lookback_days)}

    results: list[dict] = []
    pos_pnl_hs = None
    for method, s in scen.items():
        pos_pnl = revalue(book, lvl_t, s)
        if method == "HS":
            pos_pnl_hs = pos_pnl
        desk_pnl = aggregate(pos_pnl, book)
        for scope in desk_pnl.columns:
            r = var_es_from_pnl(desk_pnl[scope], CFG.alpha_var, CFG.alpha_es, method=method)
            for hz in (1, CFG.reporting_horizon_days):
                rr = r if hz == 1 else r.scaled(hz)
                results.append({"desk_id": desks[scope], "measure": f"VAR_{method}",
                                "confidence": CFG.alpha_var, "horizon_days": hz,
                                "value": round(rr.var, 2)})
                if method == "HS":
                    results.append({"desk_id": desks[scope], "measure": "ES_975",
                                    "confidence": CFG.alpha_es, "horizon_days": hz,
                                    "value": round(rr.es, 2)})

    sw_start, sw_end = stressed_window(CFG)
    stress_rets = returns.loc[pd.Timestamp(sw_start): pd.Timestamp(sw_end)]
    desk_stress = aggregate(revalue(book, lvl_t, stress_rets), book)
    for scope in desk_stress.columns:
        r = var_es_from_pnl(desk_stress[scope], CFG.alpha_var, CFG.alpha_es, method="stressed")
        results.append({"desk_id": desks[scope], "measure": "ES_STRESSED",
                        "confidence": CFG.alpha_es, "horizon_days": 1, "value": round(r.es, 2)})
    db.write_risk_results(conn, ctx["run_id"], results)

    # per-position decomposition on the HS set (the headline and limit measure):
    # the desk drill-down waterfall and positions table read these rows
    comp = position_components(book, pos_pnl_hs, CFG.alpha_var, CFG.alpha_es)
    db.write_position_components(conn, ctx["run_id"], [
        {"desk_id": desks[r.desk_code], "ticker": r.ticker, "factor_class": r.factor_class,
         "quantity": r.quantity, "instrument_type": r.instrument_type,
         "standalone_var": round(r.standalone_var, 2),
         "component_es": round(r.component_es, 2),
         "marginal_var": round(r.marginal_var, 2)}
        for r in comp.itertuples(index=False)])

    # key-rate DV01s off the bootstrapped par curve (curve view is reporting;
    # VaR pricing keeps the documented one-factor proxy - model doc R7)
    fids = dict(zip(meta["factor_code"], meta["factor_id"]))
    code_of = {t: c for c, t in NODE_TENORS.items()}
    par = pd.Series({t: lvl_t[c] / 100.0 for c, t in NODE_TENORS.items()}).sort_index()
    krd_rows: list[dict] = []
    desk_krd: dict[tuple[str, float], float] = {}
    for b in book[book["instrument_type"] == "GOVT_BOND"].itertuples(index=False):
        krd = key_rate_dv01s(par, b.coupon, b.maturity_years, float(b.quantity))
        for tenor, v in krd.items():
            desk_krd[(b.desk_code, tenor)] = desk_krd.get((b.desk_code, tenor), 0.0) + float(v)
            desk_krd[("FIRM", tenor)] = desk_krd.get(("FIRM", tenor), 0.0) + float(v)
    krd_rows = [{"desk_id": desks[d], "factor_id": fids[code_of[t]],
                 "measure": "KRD_DV01", "value": round(v, 2)}
                for (d, t), v in desk_krd.items()]
    db.write_exposures(conn, ctx["run_id"], krd_rows)

    # vega per desk on each vol factor, dollars per vol point
    desk_vega: dict[tuple[str, str], float] = {}
    for o in book[book["instrument_type"] == "OPTION"].itertuples(index=False):
        spot = float(lvl_t[o.factor_code])
        sigma = float(lvl_t[o.vol_factor_code]) / 100.0
        rate = float(lvl_t[o.rate_factor_code]) / 100.0
        v = float(o.quantity) * float(bs_vega(spot, o.moneyness * spot, sigma,
                                              o.maturity_years, rate)) / 100.0
        for scope in (o.desk_code, "FIRM"):
            desk_vega[(scope, o.vol_factor_code)] = desk_vega.get(
                (scope, o.vol_factor_code), 0.0) + v
    db.write_exposures(conn, ctx["run_id"],
                       [{"desk_id": desks[d], "factor_id": fids[f], "measure": "VEGA",
                         "value": round(v, 2)} for (d, f), v in desk_vega.items()])

    # dollar delta per desk and factor: linear legs are qty * level, options go
    # through BS delta at today's surface; bonds report through KRD_DV01 instead
    desk_delta: dict[tuple[str, str], float] = {}
    for p in book.itertuples(index=False):
        if p.instrument_type in ("STOCK", "ETF", "FX_SPOT"):
            d = float(p.quantity) * float(lvl_t[p.factor_code])
        elif p.instrument_type == "OPTION":
            spot = float(lvl_t[p.factor_code])
            sigma = float(lvl_t[p.vol_factor_code]) / 100.0
            rate = float(lvl_t[p.rate_factor_code]) / 100.0
            d = float(p.quantity) * spot * float(bs_delta(p.option_type, spot,
                                                          p.moneyness * spot, sigma,
                                                          p.maturity_years, rate))
        else:
            continue
        for scope in (p.desk_code, "FIRM"):
            desk_delta[(scope, p.factor_code)] = desk_delta.get((scope, p.factor_code), 0.0) + d
    db.write_exposures(conn, ctx["run_id"],
                       [{"desk_id": desks[s], "factor_id": fids[f], "measure": "DELTA_USD",
                         "value": round(v, 2)} for (s, f), v in desk_delta.items()])

    # flash check on BOTH measures: the headline (HS) and the filtered model
    # (FHS) each explain their own move before publishing. Vol-forecast movers
    # attach only where they are the actual driver - the EWMA forecast enters
    # FHS, not HS, whose moves come from scenario turnover and level changes.
    fids_map = dict(zip(meta["factor_code"], meta["factor_id"]))
    tripped = []
    for flash_measure in ("VAR_HS", "VAR_FHS"):
        prev_m = db.prev_var(conn, run_date, flash_measure)
        curr_m = {code: r["value"] for r in results
                  for code, did in desks.items()
                  if r["desk_id"] == did and r["measure"] == flash_measure
                  and r["horizon_days"] == 1}
        flash = dq.flash_dod_check(curr_m, prev_m, CFG.flash_dod_threshold)
        if flash is None:
            continue
        flash["detail"]["measure"] = flash_measure
        if flash_measure == "VAR_FHS":
            idx_run = returns.index.get_loc(ts_run)
            if idx_run > 0:
                fc_prev = ewma_vol_forecast(returns.loc[:returns.index[idx_run - 1]],
                                            lam=CFG.lambda_ewma,
                                            seed_window=CFG.ewma_seed_window)
                flash["detail"]["vol_forecast_movers"] = dq.top_vol_movers(fc, fc_prev)
        flash["obs_date"] = run_date
        db.write_dq_issues(conn, ctx["run_id"], [flash], fids_map)
        tripped.append(flash_measure)
        print(f"[eod] FLASH: firm {flash_measure} moved "
              f"{flash['detail']['pct_move']:+.1%} day-over-day (threshold "
              f"{CFG.flash_dod_threshold:.0%}) - attribution written to dq_issues",
              file=sys.stderr)
    if not tripped and db.prev_var(conn, run_date, "VAR_HS"):
        print(f"[eod] flash: day-over-day moves within {CFG.flash_dod_threshold:.0%}")

    # clean P&L for run_date (frozen book, prior-day levels, actual move) + exception check
    idx = returns.index.get_loc(ts_run)
    if idx > 0:
        prev_ts = returns.index[idx - 1]
        day_move = returns.loc[[ts_run]]
        hpl = aggregate(revalue(book, levels.loc[prev_ts], day_move), book).iloc[0]
        rtpl = aggregate(revalue(book, levels.loc[prev_ts], day_move,
                                 mode="delta_gamma"), book).iloc[0]
        db.write_pnl(conn, [{"desk_id": desks[s], "pnl_date": run_date,
                             "pnl_type": "HYPOTHETICAL", "amount": round(float(hpl[s]), 2)}
                            for s in hpl.index])
        db.write_pnl(conn, [{"desk_id": desks[s], "pnl_date": run_date,
                             "pnl_type": "RISK_THEORETICAL", "amount": round(float(rtpl[s]), 2)}
                            for s in rtpl.index])
        exceptions = []
        for method in ("HS", "FHS"):
            prev = db.prev_var(conn, run_date, f"VAR_{method}")
            for scope, v in prev.items():
                if float(hpl.get(scope, 0.0)) < -v:
                    exceptions.append({"desk_id": desks[scope], "obs_date": run_date,
                                       "measure": f"VAR_{method}", "var_value": v,
                                       "pnl_value": round(float(hpl[scope]), 2)})
        db.write_exceptions(conn, ctx["run_id"], exceptions)
        firm_var = next(r["value"] for r in results
                        if r["desk_id"] == desks["FIRM"] and r["measure"] == "VAR_HS"
                        and r["horizon_days"] == 1)
        print(f"[eod] risk: firm VaR99 1d ${firm_var:,.0f}, firm P&L ${hpl['FIRM']:,.0f}, "
              f"{len(exceptions)} exception(s)")
    else:
        print("[eod] risk: first day in history - no P&L/exception check")


def step_scenarios(ctx: dict) -> None:
    conn, run_date, meta = ctx["conn"], ctx["run_date"], ctx["factor_meta"]
    fids = dict(zip(meta["factor_code"], meta["factor_id"]))
    convs = dict(zip(meta["factor_code"], meta["return_conv"]))
    limits = dict(zip(meta["factor_code"], meta["ffill_limit_days"]))
    hypos = yaml.safe_load(open(SCENARIOS_YAML))
    sid = db.ensure_scenario_catalog(conn, REPLAY_WINDOWS, hypos, fids)

    book = db.read_book(conn)
    desks = db.desk_id_map(conn)
    levels, _ = align_levels(db.read_levels(conn, end=run_date), limits)
    returns = to_returns(levels, convs).dropna()
    lvl_t = levels.loc[returns.index].iloc[-1]

    rows = []
    for code, (start, end) in REPLAY_WINDOWS.items():
        shock = compute_replay_shock(returns, start, end)
        pnl = apply_scenario(book, lvl_t, shock)
        rows += [{"scenario_id": sid[code], "desk_id": desks[s],
                  "pnl_impact": round(float(pnl[s]), 2)} for s in pnl.index]
    for h in hypos:
        shock = pd.Series({f: s["value"] for f, s in h["shocks"].items()})
        pnl = apply_scenario(book, lvl_t, shock)
        rows += [{"scenario_id": sid[h["code"]], "desk_id": desks[s],
                  "pnl_impact": round(float(pnl[s]), 2)} for s in pnl.index]
    db.write_scenario_results(conn, ctx["run_id"], rows)
    worst = min(rows, key=lambda r: r["pnl_impact"])
    print(f"[eod] scenarios: {len(sid)} scenarios written; worst desk impact "
          f"${worst['pnl_impact']:,.0f}")


STEPS = {"ingest": step_ingest, "dq": step_dq, "risk": step_risk, "scenarios": step_scenarios}


# ---------------------------------------------------------------- drivers

def run_day(engine, run_date: dt.date, steps: list[str], force: bool,
            fetch_live: bool) -> int:
    with engine.begin() as conn:
        run_id = db.claim_run(conn, run_date, "EOD", force=force)
        ctx = {"conn": conn, "run_id": run_id, "run_date": run_date,
               "fetch_live": fetch_live, "dq_block": False,
               "factor_meta": db.read_factor_meta(conn)}
        try:
            for name in steps:
                t0 = time.perf_counter()
                STEPS[name](ctx)
                print(f"[eod] step {name!r} ok ({time.perf_counter() - t0:.1f}s)")
        except Exception as exc:
            db.finish_run(conn, run_id, "FAILED", error_msg=str(exc)[:500])
            conn.commit()          # persist the FAILED status despite the raise
            raise
        db.finish_run(conn, run_id, "PARTIAL" if ctx["dq_block"] else "SUCCESS")
    print(f"[eod] run {run_date}: {'PARTIAL' if ctx['dq_block'] else 'SUCCESS'}")
    return 0


def run_db_backfill(engine, start: dt.date, end: dt.date, resume: bool, force: bool) -> int:
    """Compute the window in memory once, then write per-day BACKFILL runs."""
    with engine.connect() as conn:
        meta = db.read_factor_meta(conn)
        book = db.read_book(conn)
        desks = db.desk_id_map(conn)
        convs = dict(zip(meta["factor_code"], meta["return_conv"]))
        limits = dict(zip(meta["factor_code"], meta["ffill_limit_days"]))
        levels, _ = align_levels(db.read_levels(conn, end=end), limits)
        returns = to_returns(levels, convs).dropna()
        levels = levels.loc[returns.index]
        done = {d for (d,) in conn.execute(text(
            "SELECT run_date FROM risk_runs WHERE run_type='BACKFILL' AND status='SUCCESS'")).all()} \
            if resume else set()

    window = returns.loc[pd.Timestamp(start): pd.Timestamp(end)].index
    if len(window) < 2:
        print("[eod] backfill: nothing to do")
        return 0
    t0 = time.perf_counter()
    res = run_backfill(book, levels, returns, n_days=len(window) - 1)
    print(f"[eod] backfill: computed {res['as_of'].nunique()} days in "
          f"{time.perf_counter() - t0:.1f}s")

    n_written = 0
    for as_of, g in res.groupby("as_of"):
        day = pd.Timestamp(as_of).date()
        if day in done:
            continue
        nxt = res[res["as_of"] > as_of]["as_of"].min()   # hpl_next belongs to the NEXT date
        with engine.begin() as conn:
            try:
                run_id = db.claim_run(conn, day, "BACKFILL", force=force)
            except RuntimeError:
                continue
            rows, pnl_rows, exc_rows = [], [], []
            for r in g.itertuples():
                rows.append({"desk_id": desks[r.scope], "measure": f"VAR_{r.method}",
                             "confidence": CFG.alpha_var, "horizon_days": 1,
                             "value": round(r.var, 2)})
                if r.method == "HS":
                    rows.append({"desk_id": desks[r.scope], "measure": "ES_975",
                                 "confidence": CFG.alpha_es, "horizon_days": 1,
                                 "value": round(r.es, 2)})
                if pd.notna(nxt) and pd.notna(r.hpl_next):
                    if r.method == "HS":
                        pnl_rows.append({"desk_id": desks[r.scope],
                                         "pnl_date": pd.Timestamp(nxt).date(),
                                         "pnl_type": "HYPOTHETICAL",
                                         "amount": round(r.hpl_next, 2)})
                        pnl_rows.append({"desk_id": desks[r.scope],
                                         "pnl_date": pd.Timestamp(nxt).date(),
                                         "pnl_type": "RISK_THEORETICAL",
                                         "amount": round(r.rtpl_next, 2)})
                    if r.is_exception:
                        exc_rows.append({"desk_id": desks[r.scope],
                                         "obs_date": pd.Timestamp(nxt).date(),
                                         "measure": f"VAR_{r.method}",
                                         "var_value": round(r.var, 2),
                                         "pnl_value": round(r.hpl_next, 2)})
            db.write_risk_results(conn, run_id, rows)
            if pnl_rows:
                db.write_pnl(conn, pnl_rows)
            if exc_rows:
                db.write_exceptions(conn, run_id, exc_rows)
            db.finish_run(conn, run_id, "SUCCESS")
            n_written += 1
    print(f"[eod] backfill: wrote {n_written} run days "
          f"({len(window) - 1 - n_written} skipped)")
    with engine.connect() as conn:
        firm = desks["FIRM"]
        x = conn.scalar(text("""SELECT count(*) FROM backtest_exceptions
                                WHERE desk_id=:d AND measure='VAR_HS'"""), {"d": firm})
        tl = basel_traffic_light(int(conn.scalar(text("""
            SELECT count(*) FROM backtest_exceptions
            WHERE desk_id=:d AND measure='VAR_HS'
              AND obs_date > :cut"""), {"d": firm, "cut": end - dt.timedelta(days=365)})))
        print(f"[eod] firm HS exceptions in DB: {x}; trailing zone {tl.zone}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="risk.jobs.eod")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run")
    p_run.add_argument("--date", type=dt.date.fromisoformat, default=dt.date.today())
    p_run.add_argument("--steps", default=",".join(STEPS))
    p_run.add_argument("--force", action="store_true")
    p_run.add_argument("--no-fetch", dest="fetch", action="store_false",
                       help="skip network top-up (tests/CI/demo must use this)")

    p_bf = sub.add_parser("backfill")
    p_bf.add_argument("--start", type=dt.date.fromisoformat, required=True)
    p_bf.add_argument("--end", type=dt.date.fromisoformat, required=True)
    p_bf.add_argument("--resume", action="store_true")
    p_bf.add_argument("--force", action="store_true")

    args = parser.parse_args(argv)
    engine = db.make_engine()
    if args.cmd == "run":
        steps = [s.strip() for s in args.steps.split(",") if s.strip()]
        unknown = set(steps) - set(STEPS)
        if unknown:
            parser.error(f"unknown steps: {sorted(unknown)}; valid: {list(STEPS)}")
        return run_day(engine, args.date, steps, args.force, args.fetch)
    return run_db_backfill(engine, args.start, args.end, args.resume, args.force)


if __name__ == "__main__":
    raise SystemExit(main())
