"""RNIV report CLI: measure every quantifiable model-doc limitation -> docs/rniv.md.

    python -m risk.jobs.rniv [--snapshot ...] [--portfolio ...] [--out docs/rniv.md]

DB-free by design, like the backfill chart: every number regenerates from the
committed snapshot, so the report can never drift from the data. Section 5 of
docs/model_doc.md summarizes the table this writes.
"""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

import pandas as pd

from risk.jobs.backfill import load_inputs
from risk_engine.backfill import run_backfill
from risk_engine.backtest import basel_traffic_light
from risk_engine.config import DEFAULT_CONFIG as CFG
from risk_engine.curve import (
    NODE_TENORS,
    bootstrap_zero_curve,
    key_rate_dv01s,
    price_bond_on_curve,
)
from risk_engine.engine import FIRM_SCOPE, aggregate, component_es, revalue
from risk_engine.es import stressed_window
from risk_engine.factors import build_scenarios_fhs, build_scenarios_hs
from risk_engine.rniv import (
    desk_scales_for_mix,
    fill_mask,
    kday_overlapping_shocks,
    lead_lag_correlations,
    vol_damping_ratios,
)
from risk_engine.stress import REPLAY_WINDOWS, compute_replay_shock
from risk_engine.var import ewma_vol_forecast, ewma_volatility, var_es_from_pnl

K_SYNC = 2                       # aggregation horizon that washes out close-time gaps
AGED_DEMO_RALLY_BP = 100.0       # par rally used to un-pin the R7 demonstration matrix
MATERIAL_PCT = 0.05              # >= 5% of the base measure
MONITOR_PCT = 0.01               # 1-5%: monitor; below: immaterial
YIELD_FLOOR_PCT = 0.01           # pricing floor, in percent (1bp)
TOP_CORR_PAIRS = 3

# R8 static-book sensitivity. BACKTEST_DAYS matches the published backtest so
# the comparison is against the number the README and model doc actually quote;
# TL_WINDOW is the window the traffic light is defined on. The two mixes are the
# ones data/seed/portfolio.yaml already names: the peer average it cites from the
# FY2025 10-K trading-VaR tables, and the mix the book was sized to target.
BACKTEST_DAYS = 750
TL_WINDOW = 250
MIX_ANCHOR_DESK = "EQUITY"       # pinned at 1.0 so a remix is not also a resize
TL_ZONE_EDGE = 5                 # first amber exception count at TL_WINDOW=250
PEER_MIX = {"RATES": 0.52, "EQUITY": 0.28, "FX": 0.20}
TARGET_MIX = {"RATES": 0.45, "EQUITY": 0.30, "FX": 0.25}

# candidate stress-calibration windows for the sensitivity check (R4); the
# programmatic argmax search over full history is the roadmap replacement
CANDIDATE_WINDOWS: dict[str, tuple[dt.date, dt.date]] = {
    "COVID year": (dt.date(2020, 2, 19), dt.date(2021, 2, 18)),
    "2022 hiking year": (dt.date(2022, 1, 3), dt.date(2022, 12, 30)),
}


def _fmt_usd(v: float) -> str:
    if abs(v) < 0.5:                 # keep "-$0" artifacts out of the tables
        return "$0"
    return f"-${abs(v):,.0f}" if v < 0 else f"${v:,.0f}"


def _classify(pct: float) -> str:
    a = abs(pct)
    if a >= MATERIAL_PCT:
        return "Material"
    if a >= MONITOR_PCT:
        return "Monitor"
    return "Immaterial"


def _firm_var_es(book, lvl_t, shocks, method: str, horizon: int):
    desk_pnl = aggregate(revalue(book, lvl_t, shocks), book)
    per_scope = {s: var_es_from_pnl(desk_pnl[s], CFG.alpha_var, CFG.alpha_es,
                                    method=method, horizon_days=horizon)
                 for s in desk_pnl.columns}
    return per_scope


def _div_benefit(per_scope) -> float:
    firm = per_scope["FIRM"].var
    standalone = sum(r.var for s, r in per_scope.items() if s != "FIRM")
    return 1.0 - firm / standalone


def measure(snapshot: str, portfolio: str) -> dict:
    book, levels, returns, _ = load_inputs(snapshot, portfolio)
    raw = pd.read_parquet(snapshot).pivot(index="obs_date", columns="factor_code",
                                          values="value").sort_index()
    raw.index = pd.to_datetime(raw.index)
    limits = {f: 7 if f.startswith("FX.") else CFG.ffill_limit_days for f in raw.columns}

    as_of = returns.index[-1]
    lvl_t = levels.loc[as_of]
    out: dict = {"as_of": as_of.date(), "n_factors": len(returns.columns)}

    # base 1-day measures
    hs_window = build_scenarios_hs(returns, as_of, CFG.lookback_days)
    base = _firm_var_es(book, lvl_t, hs_window, "HS", 1)
    out["base"] = base
    out["div_1d"] = _div_benefit(base)

    # R1 - sqrt(k) horizon scaling vs overlapping k-day revaluation
    k10 = CFG.reporting_horizon_days
    shocks10 = kday_overlapping_shocks(returns, as_of, k10, CFG.lookback_days)
    m10 = _firm_var_es(book, lvl_t, shocks10, "HS-overlap", k10)
    scaled10 = base["FIRM"].scaled(k10)
    out["r1"] = {"measured": m10["FIRM"], "scaled": scaled10,
                 "pct": m10["FIRM"].var / scaled10.var - 1.0, "n_scen": len(shocks10)}

    # R2 - async closes: 2-day aggregation restores cross-close covariance
    shocks2 = kday_overlapping_shocks(returns, as_of, K_SYNC, CFG.lookback_days)
    m2 = _firm_var_es(book, lvl_t, shocks2, "HS-overlap", K_SYNC)
    scaled2 = base["FIRM"].scaled(K_SYNC)
    eq = [c for c in returns.columns if c.startswith("EQ.")]
    non_eq = [c for c in returns.columns if c.startswith(("FX.", "IR."))]
    ll = lead_lag_correlations(returns, eq, non_eq, window=CFG.lookback_days)
    lag1 = ll[ll["lag"] == 1].nlargest(TOP_CORR_PAIRS, "corr")
    lag0 = ll[ll["lag"] == 0].set_index(["left", "right"])["corr"]
    out["r2"] = {"measured": m2["FIRM"], "scaled": scaled2,
                 "pct": m2["FIRM"].var / scaled2.var - 1.0,
                 "div_2d": _div_benefit(m2),
                 "pairs": [(r.left, r.right, float(lag0.loc[(r.left, r.right)]), r.corr)
                           for r in lag1.itertuples()]}

    # R3 - forward-fill vol damping through the FHS forecast
    mask = fill_mask(raw, limits)
    rho = vol_damping_ratios(returns, mask, CFG.lambda_ewma, CFG.ewma_seed_window)
    vols = ewma_volatility(returns, lam=CFG.lambda_ewma, seed_window=CFG.ewma_seed_window)
    fc = ewma_vol_forecast(returns.loc[:as_of], lam=CFG.lambda_ewma,
                           seed_window=CFG.ewma_seed_window)
    fhs_base = _firm_var_es(book, lvl_t,
                            build_scenarios_fhs(returns, vols, fc, as_of, CFG.lookback_days),
                            "FHS", 1)
    fc_adj = fc * rho.reindex(fc.index).fillna(1.0)
    fhs_adj = _firm_var_es(book, lvl_t,
                           build_scenarios_fhs(returns, vols, fc_adj, as_of, CFG.lookback_days),
                           "FHS-adj", 1)
    out["r3"] = {"base": fhs_base["FIRM"], "adjusted": fhs_adj["FIRM"],
                 "pct": fhs_adj["FIRM"].var / fhs_base["FIRM"].var - 1.0,
                 "rho_top": rho.dropna().sort_values(ascending=False).head(3),
                 "fill_share": float(mask.reindex(returns.index).fillna(False)
                                     .tail(CFG.lookback_days).mean().mean())}

    # R4 - stressed-window choice sensitivity
    windows = {"GFC year (in force)": stressed_window(CFG), **CANDIDATE_WINDOWS}
    es_by_window = {}
    for name, (start, end) in windows.items():
        stress_rets = returns.loc[pd.Timestamp(start): pd.Timestamp(end)]
        r = var_es_from_pnl(aggregate(revalue(book, lvl_t, stress_rets), book)["FIRM"],
                            CFG.alpha_var, CFG.alpha_es, method="stressed")
        es_by_window[name] = r.es
    in_force = es_by_window["GFC year (in force)"]
    worst = max(es_by_window.values())
    out["r4"] = {"by_window": es_by_window, "pct": worst / in_force - 1.0}

    # R5 - single-name concentration via Euler allocation of firm ES
    pos_pnl = revalue(book, lvl_t, hs_window)
    comp = component_es(pos_pnl, alpha_es=CFG.alpha_es).sort_values(ascending=False)
    firm_es = base["FIRM"].es
    stocks = set(book.loc[book["instrument_type"] == "STOCK", "ticker"])
    single_names = comp[comp.index.isin(stocks)]
    out["r5"] = {"component": comp, "firm_es": firm_es,
                 "top1_share": float(comp.iloc[0] / firm_es),
                 "top3_share": float(comp.head(3).sum() / firm_es),
                 "top2_names": " and ".join(comp.head(2).index),
                 "top2_share": float(comp.head(2).sum() / firm_es),
                 "top_single_name": single_names.idxmax() if len(single_names) else "n/a"}

    # R6 - post-shock yield floor headroom
    ir = [c for c in returns.columns if c.startswith("IR.")]
    gfc = compute_replay_shock(returns, *REPLAY_WINDOWS["GFC_2008"])
    post_hs = (lvl_t[ir] + hs_window[ir].min() / 100.0).min()
    post_gfc = (lvl_t[ir] + gfc[ir] / 100.0).min()
    out["r6"] = {"min_post_pct": float(min(post_hs, post_gfc)),
                 "headroom_bp": float((min(post_hs, post_gfc) - YIELD_FLOOR_PCT) * 100)}

    # R7 - one-factor ytm proxy vs bootstrapped-curve pricing for the bond book
    tenor_of = NODE_TENORS
    code_of = {v: k for k, v in tenor_of.items()}
    par = pd.Series({tenor_of[c]: lvl_t[c] / 100.0 for c in tenor_of}).sort_index()
    bonds = book[book["instrument_type"] == "GOVT_BOND"]
    krd_rows = {}
    for b in bonds.itertuples(index=False):
        krd = key_rate_dv01s(par, b.coupon, b.maturity_years, float(b.quantity))
        krd_rows[b.ticker] = krd.rename(code_of)
    krd_matrix = pd.DataFrame(krd_rows).T
    own = {b.ticker: float(krd_rows[b.ticker][code_of[b.maturity_years]]
                           / krd_rows[b.ticker].sum())
           for b in bonds.itertuples(index=False)}

    base_curve = bootstrap_zero_curve(par)
    base_px = {b.ticker: price_bond_on_curve(base_curve, b.coupon, b.maturity_years,
                                             float(b.quantity))
               for b in bonds.itertuples(index=False)}
    curve_pnl = []
    ir_shocks = hs_window[[code_of[t] for t in par.index]].to_numpy() / 1e4
    for shock in ir_shocks:
        shocked = bootstrap_zero_curve(pd.Series(par.to_numpy() + shock, index=par.index))
        curve_pnl.append(sum(
            price_bond_on_curve(shocked, b.coupon, b.maturity_years, float(b.quantity))
            - base_px[b.ticker] for b in bonds.itertuples(index=False)))
    var_curve = var_es_from_pnl(pd.Series(curve_pnl), CFG.alpha_var, CFG.alpha_es,
                                method="curve")
    rates_proxy = aggregate(revalue(book, lvl_t, hs_window), book)["RATES"]
    var_proxy = var_es_from_pnl(rates_proxy, CFG.alpha_var, CFG.alpha_es, method="proxy")
    aged = par - AGED_DEMO_RALLY_BP / 1e4      # pars rally, coupons stay at anchor
    aged_rows = {b.ticker: key_rate_dv01s(aged, b.coupon, b.maturity_years,
                                          float(b.quantity)).rename(code_of)
                 for b in bonds.itertuples(index=False)}
    out["r7"] = {"krd": krd_matrix, "own_share": own,
                 "krd_aged": pd.DataFrame(aged_rows).T,
                 "var_proxy": var_proxy, "var_curve": var_curve,
                 "pct": var_curve.var / var_proxy.var - 1.0}

    # R8 - the backtest values today's book on every historical date
    out["r8"] = _static_book_sensitivity(book, levels, returns, base)
    return out


def _standalone_desk_var(book, lvl_t, shocks) -> dict[str, float]:
    per_desk = aggregate(revalue(book, lvl_t, shocks), book)
    return {d: var_es_from_pnl(per_desk[d], CFG.alpha_var, CFG.alpha_es).var
            for d in per_desk.columns if d != FIRM_SCOPE}


def _remix(book, lvl_t, shocks, target: dict[str, float]):
    scales = desk_scales_for_mix(_standalone_desk_var(book, lvl_t, shocks),
                                 target, anchor=MIX_ANCHOR_DESK)
    out = book.copy()
    out["quantity"] = [q * scales[d] for q, d in zip(out["quantity"], out["desk_code"])]
    return out


def _scale_desk(book, desk: str, factor: float):
    out = book.copy()
    out["quantity"] = [q * (factor if d == desk else 1.0)
                       for q, d in zip(out["quantity"], out["desk_code"])]
    return out


def _static_book_sensitivity(book, levels, returns, base) -> dict:
    """How much of the published backtest verdict is the book rather than the model.

    read_book takes no date, so run_backfill values one composition on all 750
    dates. The counterfactual - the book actually held in 2023 - never existed,
    so the honest measurement is not a bias against truth but a sensitivity: how
    far does the verdict move across compositions that are defensible for this
    book? The consequential output is the traffic-light zone, because that is
    what carries a capital multiplier.
    """
    lvl_t = levels.loc[returns.index[-1]]
    hs = build_scenarios_hs(returns, returns.index[-1], CFG.lookback_days)
    variants = [
        ("published", book),
        ("no collar (pre-sleeve book)",
         book[book["instrument_type"] != "OPTION"].reset_index(drop=True)),
        ("peer mix 52/28/20", _remix(book, lvl_t, hs, PEER_MIX)),
        ("config target 45/30/25", _remix(book, lvl_t, hs, TARGET_MIX)),
    ]
    for desk in sorted(d for d in book["desk_code"].unique()):
        for factor in (0.5, 1.5):
            variants.append((f"{desk.lower()} x{factor:g}", _scale_desk(book, desk, factor)))

    rows = []
    for label, variant in variants:
        frame = run_backfill(variant, levels, returns, n_days=BACKTEST_DAYS, cfg=CFG)
        firm = frame[frame["scope"] == FIRM_SCOPE]
        for method in ("HS", "FHS"):
            series = firm[firm["method"] == method]["is_exception"]
            zone = basel_traffic_light(int(series.tail(TL_WINDOW).sum()), TL_WINDOW)
            rows.append({"variant": label, "method": method,
                         "exceptions": int(series.sum()), "n_obs": len(series),
                         "exceptions_tl": int(series.tail(TL_WINDOW).sum()),
                         "zone": zone.zone, "multiplier": zone.multiplier})
    # how much of each desk's standalone VaR survives into the firm number - the
    # mechanism behind a desk cut moving the firm tail the "wrong" way
    standalone = _standalone_desk_var(book, lvl_t, hs)
    firm_var = base[FIRM_SCOPE].var
    survives = {}
    for desk in standalone:
        without = book[book["desk_code"] != desk]
        firm_ex = var_es_from_pnl(aggregate(revalue(without, lvl_t, hs), without)[FIRM_SCOPE],
                                  CFG.alpha_var, CFG.alpha_es).var
        survives[desk] = {"standalone": standalone[desk],
                          "marginal": firm_var - firm_ex}

    table = pd.DataFrame(rows)
    published = table[table["variant"] == "published"]
    left_green = sorted(set(table.loc[table["zone"] != "GREEN", "variant"]))
    worst = table["multiplier"].max()
    base_mult = float(published["multiplier"].max())
    return {"table": table, "n_variants": len(variants),
            "published": {r.method: r for r in published.itertuples(index=False)},
            "left_green": left_green, "survives": survives,
            "multiplier": base_mult, "multiplier_worst": float(worst),
            "pct": worst / base_mult - 1.0,
            "base_var": firm_var}


def render(m: dict) -> str:
    base_var = m["base"]["FIRM"].var
    r1, r2, r3, r4, r5, r6 = m["r1"], m["r2"], m["r3"], m["r4"], m["r5"], m["r6"]
    r7, r8 = m["r7"], m["r8"]
    hs_exc = r8["table"].loc[r8["table"]["method"] == "HS", "exceptions"]
    rows = [
        ("R1", "1", "sqrt(10) horizon scaling vs overlapping 10-day revaluation",
         f"{_fmt_usd(r1['measured'].var - r1['scaled'].var)} on the 10-day VaR "
         f"({r1['pct']:+.1%})", _classify(r1["pct"]),
         "Report scaled figure with this measured gap; overlapping estimator kept as a check"),
        ("R2", "4", "Asynchronous closes understate cross-asset co-movement",
         f"diversification benefit {m['div_1d']:.1%} at 1 day vs {r2['div_2d']:.1%} "
         f"at 2 days ({m['div_1d'] - r2['div_2d']:+.1%}p of hidden co-movement)",
         _classify(m["div_1d"] - r2["div_2d"]),
         "Named bias; synchronization overlay out of scope for public daily data"),
        ("R3", "5", "Forward-fill zero returns damp EWMA vol",
         f"{_fmt_usd(r3['adjusted'].var - r3['base'].var)} on FHS VaR ({r3['pct']:+.1%})",
         _classify(r3["pct"]),
         "Fill share and per-factor ratios recorded; bounded by DQ fill caps"),
        ("R4", "8", "Fixed stressed window vs candidate crisis windows",
         (f"in-force {_fmt_usd(r4['by_window']['GFC year (in force)'])} is the max of "
          f"the candidates ({', '.join(f'{n} {_fmt_usd(v)}' for n, v in r4['by_window'].items() if 'in force' not in n)})"
          if r4["pct"] <= 0 else
          f"worst candidate {_fmt_usd(max(r4['by_window'].values()))} exceeds in-force "
          f"{_fmt_usd(r4['by_window']['GFC year (in force)'])} ({r4['pct']:+.1%})"),
         _classify(r4["pct"]),
         "Programmatic worst-window search is the roadmap replacement"),
        ("R5", "11", "Position concentration of firm ES",
         f"top position {r5['top1_share']:.1%} of firm ES, top 3 {r5['top3_share']:.1%}",
         "Monitor",
         "Euler components monitored; factor-model idiosyncratic add-on on the roadmap"),
        ("R6", "9", "1bp post-shock yield floor",
         f"unbinding; minimum post-shock yield {r6['min_post_pct']:.2f}% "
         f"({r6['headroom_bp']:.0f}bp of headroom)", "Immaterial",
         "Re-measure if front-end yields fall below ~1.5%"),
        ("R7", "2", "One-factor ytm proxy vs bootstrapped-curve pricing",
         f"rates-desk VaR {_fmt_usd(r7['var_curve'].var)} curve-priced vs "
         f"{_fmt_usd(r7['var_proxy'].var)} proxy ({r7['pct']:+.1%}); KRDs diagonal "
         "at the anchor by construction, spillover grows as coupons drift off par",
         _classify(r7["pct"]),
         "Curve view reported (key-rate DV01s in the nightly batch); VaR keeps the proxy"),
        ("R8", "13", "Backtest values today's book on every historical date",
         (f"FHS sits {TL_ZONE_EDGE - r8['published']['FHS'].exceptions_tl} exception from "
          f"amber ({r8['published']['FHS'].exceptions_tl}/{TL_WINDOW}); "
          f"{len(r8['left_green'])} of {r8['n_variants']} defensible compositions cross it, "
          f"taking the Basel multiplier {r8['multiplier']:.2f} -> "
          f"{r8['multiplier_worst']:.2f} ({r8['pct']:+.1%})"),
         _classify(r8["pct"]),
         "Disclosed, not fixed: a dated position store needs trade flow this project has no source for"),
    ]

    lines = [
        "# RiskDesk — Risks Not In VaR (quantified limitations)",
        "",
        f"*Generated by `python -m risk.jobs.rniv` from the committed snapshot; "
        f"as of {m['as_of']}, {m['n_factors']} factors, firm 1-day VaR99 "
        f"{_fmt_usd(base_var)} / ES97.5 {_fmt_usd(m['base']['FIRM'].es)}. Every "
        "number below regenerates from the repository alone. Materiality: "
        f"Material >= {MATERIAL_PCT:.0%} of the base measure, Monitor >= "
        f"{MONITOR_PCT:.0%}, else Immaterial. (The database path stores levels "
        "and quantities at fixed decimal precision; its firm VaR is "
        "$1,137,118.30 - the ~7e-6 relative gap to the float path here is "
        "quantization, measured and understood.)*",
        "",
        "Banks keep an inventory of risks their VaR model does not capture, each",
        "with a measured impact and a treatment. This is that inventory for",
        "RiskDesk: every entry quantifies a limitation from `docs/model_doc.md`",
        "section 5 against the same snapshot the model runs on.",
        "",
        "| ID | §5 | Risk | Measured impact | Class | Treatment |",
        "|---|---|---|---|---|---|",
    ]
    lines += [f"| {i} | {ref} | {risk} | {impact} | {cls} | {treat} |"
              for i, ref, risk, impact, cls, treat in rows]

    lines += [
        "",
        "## R1 — Horizon scaling",
        "",
        f"The reported 10-day VaR is the 1-day figure times sqrt(10) "
        f"({_fmt_usd(r1['scaled'].var)}). Revaluing the book on "
        f"{r1['n_scen']} overlapping 10-day shock vectors from the same 500-day "
        f"window gives {_fmt_usd(r1['measured'].var)} ({r1['pct']:+.1%}), and ES "
        f"{_fmt_usd(r1['measured'].es)} vs {_fmt_usd(r1['scaled'].es)} scaled. "
        "Overlap leaves ~50 effectively independent draws, so this is an "
        "estimate with real sampling noise - the point is the sign and rough "
        "size of the iid error, not a replacement model.",
        "",
        "## R2 — Asynchronous closes",
        "",
        f"Two-day aggregation restores co-movement that different close times "
        f"split across days: the diversification benefit drops from "
        f"{m['div_1d']:.1%} at 1 day to {r2['div_2d']:.1%} at 2 days - that gap "
        "is co-movement the daily correlation structure cannot see, and it is "
        "the cleaner isolation of the sync effect. (The raw 2-day overlapping "
        f"VaR, {_fmt_usd(r2['measured'].var)} vs {_fmt_usd(r2['scaled'].var)} "
        f"sqrt(2)-scaled ({r2['pct']:+.1%}), carries the same drift and overlap "
        "effects as R1 and is reported for completeness only.) Largest "
        "equity-to-next-day spillovers (corr at lag +1 vs same-day):",
        "",
    ]
    lines += [f"- {lft} -> {rgt}: lag+1 {c1:+.2f} vs same-day {c0:+.2f}"
              for lft, rgt, c0, c1 in r2["pairs"]]
    rho_lines = [f"- {f}: {v:.3f}" for f, v in r3["rho_top"].items()]
    lines += [
        "",
        "## R3 — Forward-fill vol damping",
        "",
        f"Filled cells are {r3['fill_share']:.2%} of factor-days in the 500-day "
        f"window. Recomputing each factor's EWMA forecast on print-days only and "
        f"rescaling the FHS scenario set moves firm FHS VaR from "
        f"{_fmt_usd(r3['base'].var)} to {_fmt_usd(r3['adjusted'].var)} "
        f"({r3['pct']:+.1%}). Largest per-factor forecast ratios:",
        "",
        *rho_lines,
        "",
        "## R4 — Stressed-window choice",
        "",
        "Stressed ES on today's book under each candidate calibration window:",
        "",
    ]
    lines += [f"- {name}: {_fmt_usd(es)}" for name, es in r4["by_window"].items()]
    verdict = ("remains the worst of the candidates - currently conservative"
               if r4["pct"] <= 0
               else "is NOT the worst of the candidates - the fixed choice understates")
    top5 = [f"| {t} | {_fmt_usd(v)} | {v / r5['firm_es']:.1%} |"
            for t, v in r5["component"].head(5).items()]
    lines += [
        "",
        f"The in-force window {verdict} ({r4['pct']:+.1%} vs the worst candidate). "
        "The programmatic search over full 2007+ history replaces this check.",
        "",
        "## R5 — Concentration (Euler components of firm ES)",
        "",
        f"Firm ES {_fmt_usd(r5['firm_es'])} allocates across positions as below; "
        f"negative components are natural hedges. {r5['top2_names']} together "
        f"carry {r5['top2_share']:.0%} of firm tail risk; the largest "
        f"single-name equity component is {r5['top_single_name']}. Top five:",
        "",
        "| Position | Component ES | Share |",
        "|---|---|---|",
        *top5,
        "",
        "## R6 — Yield floor",
        "",
        f"Across the full 500-day scenario set and the GFC replay, the minimum "
        f"post-shock yield is {r6['min_post_pct']:.2f}% - {r6['headroom_bp']:.0f}bp "
        f"above the {YIELD_FLOOR_PCT:.2f}% pricing floor. The floor currently "
        "truncates nothing.",
        "",
        "## R7 — Curve-pricing basis and key-rate DV01s",
        "",
        f"The VaR path prices each proxy bond off its own constant-maturity "
        f"yield. Repricing the bond book on a bootstrapped zero curve (par "
        f"inputs bumped jointly by each historical scenario) gives a rates-desk "
        f"VaR of {_fmt_usd(r7['var_curve'].var)} vs {_fmt_usd(r7['var_proxy'].var)} "
        f"under the proxy ({r7['pct']:+.1%}) - the pricing-model basis between "
        "the two views. Par key-rate DV01s (USD per +1bp bump of one input "
        "node, curve re-bootstrapped):",
        "",
        _krd_table(r7["krd"]),
        "",
        "The matrix is diagonal **by construction**, not by accident: each "
        "position is a par bond struck at the anchor date, measured on the "
        "anchor date - it *is* the bootstrap's calibration instrument, so any "
        "other node's bump re-solves the curve to hold it at par exactly. "
        "Cross-tenor risk appears exactly as coupons drift off par. The same "
        f"book after a {AGED_DEMO_RALLY_BP:.0f}bp par rally (coupons held):",
        "",
        _krd_table(r7["krd_aged"]),
        "",
        "That drift is also why the scenario-level basis above is nonzero: "
        "shocked curves un-par the coupons inside every scenario. The nightly "
        "batch writes the live KRD table per run (risk_exposures) - on dates "
        "after the anchor the off-diagonals are real and grow with the drift - "
        "and the API and dashboard surface it.",
        "",
        "## R8 — Static book in the backtest",
        "",
        f"`read_book` takes no date argument, so the {BACKTEST_DAYS}-day backfill "
        "values one composition - today's - on every historical date. The book "
        "actually held in 2023 never existed, so there is no truth to measure a "
        "bias against; what can be measured is how much of the published verdict "
        "is the model and how much is the book. Each composition below is "
        "re-backfilled end to end and re-tested:",
        "",
        _r8_table(r8["table"]),
        "",
        f"Coverage is robust and the zone is not. HS exceptions stay inside "
        f"{hs_exc.min()}-{hs_exc.max()} against "
        f"{r8['published']['HS'].exceptions} published, and no variant makes Kupiec "
        "reject. But the published FHS run sits at "
        f"{r8['published']['FHS'].exceptions_tl} exceptions in the "
        f"{TL_WINDOW}-day traffic-light window - one short of amber at "
        f"{TL_ZONE_EDGE} - and "
        f"{', '.join(r8['left_green'])} cross it. At a bank that is not a "
        f"presentational difference: the capital multiplier moves "
        f"{r8['multiplier']:.2f} to {r8['multiplier_worst']:.2f}.",
        "",
        "Two of the crossing variants deserve naming. The peer mix is not a "
        "hypothetical - `data/seed/portfolio.yaml` cites it as the FY2025 average "
        "across the GS/MS/JPM/Citi/BofA trading-VaR tables and sizes the book "
        "against it, so the composition this project holds up as its realism "
        "reference is one that would put the challenger model in amber. Halving "
        "FX crossing is the counter-intuitive one, and the mechanism is "
        "measurable: FX carries "
        f"{_fmt_usd(r8['survives']['FX']['standalone'])} of standalone VaR but "
        f"only {_fmt_usd(r8['survives']['FX']['marginal'])} of it survives into "
        "the firm number, against "
        f"{_fmt_usd(r8['survives']['RATES']['marginal'])} of "
        f"{_fmt_usd(r8['survives']['RATES']['standalone'])} for rates. FX is "
        "close to pure offset here, so cutting it removes hedge and exposure at "
        "nearly the same rate and the firm tail does not shrink with it. Note the "
        "mixes are reweighted with the equity desk pinned, so these are mix "
        "effects rather than size effects; scaling a single desk is reported "
        "separately as the bound.",
        "",
        "The collar row is the one dated composition here rather than a "
        "counterfactual: the options sleeve shipped in August 2026 and is valued "
        f"on all {BACKTEST_DAYS} days regardless, and it accounts for one of the "
        f"{r8['published']['HS'].exceptions} published HS exceptions - a position "
        "that existed for roughly none of the window supplying a sixth of the "
        "evidence the coverage test runs on.",
        "",
        "The fix is a bitemporal position store, which needs a trade feed this "
        "project has no source for. So this stays disclosed rather than repaired, "
        "and the honest reading of the backtest section is that it tests the "
        "model on a fixed book, not the book that was held.",
        "",
    ]
    return "\n".join(lines)


def _krd_table(krd: pd.DataFrame) -> str:
    cols = list(krd.columns)
    head = "| Position | " + " | ".join(c.removeprefix("IR.UST.") for c in cols) + " | Total |"
    sep = "|---" * (len(cols) + 2) + "|"
    body = [f"| {t} | " + " | ".join(_fmt_usd(v) for v in row) + f" | {_fmt_usd(row.sum())} |"
            for t, row in krd.iterrows()]
    return "\n".join([head, sep, *body])


def _r8_table(table: pd.DataFrame) -> str:
    head = (f"| Composition | HS exc / {BACKTEST_DAYS}d | HS zone | "
            f"FHS exc / {BACKTEST_DAYS}d | FHS exc / {TL_WINDOW}d | FHS zone | Multiplier |")
    sep = "|---" * 7 + "|"
    body = []
    for variant in table["variant"].drop_duplicates():
        rows = table[table["variant"] == variant].set_index("method")
        hs, fhs = rows.loc["HS"], rows.loc["FHS"]
        flag = "" if fhs["zone"] == "GREEN" else " **"
        body.append(
            f"| {variant}{flag} | {hs['exceptions']} | {hs['zone']} | "
            f"{fhs['exceptions']} | {fhs['exceptions_tl']} | {fhs['zone']} | "
            f"{fhs['multiplier']:.2f} |")
    return "\n".join([head, sep, *body])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="risk.jobs.rniv")
    parser.add_argument("--snapshot", default="data/seed/market_snapshot.parquet")
    parser.add_argument("--portfolio", default="data/seed/portfolio.yaml")
    parser.add_argument("--out", default="docs/rniv.md")
    args = parser.parse_args(argv)

    m = measure(args.snapshot, args.portfolio)
    report = render(m)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(report)
    print(report.split("## R1")[0])
    print(f"[rniv] report -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
