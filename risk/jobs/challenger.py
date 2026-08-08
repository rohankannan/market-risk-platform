"""Champion/challenger parallel run: EWMA-FHS vs GARCH-FHS -> change-control report.

    python -m risk.jobs.challenger [--window 250] [--out docs/challenger_garch.md]

Model change control in miniature: the candidate runs beside the champion over
a regulatory window on the same historical return windows, book, and clean
P&L - the scenario sets differ only by each model's vol rescaling, which is
exactly the treatment under test - against promotion criteria stated BEFORE
the results, and the report carries the provenance a model change pack needs
(git SHA with a dirty-tree marker, candidate parameter hash, fit cutoff). GARCH
parameters are fitted once on history up to the window start and frozen - no
look-ahead into the evaluation window; a production adoption would refit on a
schedule. DB-free like the other report jobs: everything regenerates from the
committed snapshot.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import pandas as pd

from risk.db import code_version
from risk.jobs.backfill import load_inputs
from risk_engine.backfill import run_backfill
from risk_engine.backtest import (
    basel_traffic_light,
    christoffersen_conditional_coverage,
    kupiec_pof,
)
from risk_engine.config import DEFAULT_CONFIG as CFG
from risk_engine.garch import fit_garch, garch_vol_forecast_series, garch_volatility

CHAMPION = "FHS"                     # EWMA lambda=0.94 (RiskConfig)
CHALLENGER = "FHS_GARCH"

# pre-registered promotion criteria - fixed before any result is computed
PROMOTE_MAX_PERSISTENCE = 0.999      # boundary-stuck fits (IGARCH territory) fail health
PROMOTE_MIN_CC_P = 0.05              # conditional coverage must not reject at 5%
PROMOTE_MAX_MEAN_ABS_DELTA = 0.25    # daily |VaR delta| must average under 25%


def decide_promotion(champ: dict, chall: dict, mean_abs_delta: float,
                     unhealthy_fits: list[str]) -> tuple[bool, list[str]]:
    """The pre-registered rule, applied. Returns (promote, failed-criteria)."""
    failures = []
    if unhealthy_fits:
        failures.append("fit health: non-converged or boundary-persistence factors: "
                        + ", ".join(unhealthy_fits))
    if chall["tl_n"] < 250:
        failures.append(f"traffic-light window is {chall['tl_n']} days; zone "
                        "boundaries are calibrated to 250 - evaluate a full window")
    if chall["zone"] != "GREEN":
        failures.append(f"challenger zone is {chall['zone']}, requires GREEN")
    if abs(chall["x"] - chall["expected"]) > abs(champ["x"] - champ["expected"]):
        failures.append(
            f"coverage error worsens: |{chall['x']} - {chall['expected']:.1f}| vs "
            f"champion |{champ['x']} - {champ['expected']:.1f}|")
    if chall["cc_p"] < PROMOTE_MIN_CC_P:
        failures.append(f"conditional coverage rejects (p = {chall['cc_p']:.3f} "
                        f"< {PROMOTE_MIN_CC_P})")
    if mean_abs_delta > PROMOTE_MAX_MEAN_ABS_DELTA:
        failures.append(f"mean |VaR delta| {mean_abs_delta:.1%} exceeds "
                        f"{PROMOTE_MAX_MEAN_ABS_DELTA:.0%}")
    return not failures, failures


def _firm_stats(g: pd.DataFrame, p: float) -> dict:
    g = g.sort_values("as_of")
    n, x = len(g), int(g["is_exception"].sum())
    pof = kupiec_pof(x, n, p=p)
    cc = christoffersen_conditional_coverage(g["is_exception"].to_numpy(), p=p)
    tail = g.tail(250)
    tl = basel_traffic_light(int(tail["is_exception"].sum()), n_obs=len(tail))
    return {"n": n, "x": x, "expected": n * p, "kupiec_p": pof.p_value, "cc_p": cc.p_value,
            "zone": tl.zone, "multiplier": tl.multiplier, "tl_n": len(tail),
            "avg_var": float(g["var"].mean()),
            "exception_dates": set(g.loc[g["is_exception"], "as_of"])}


def measure(snapshot: str, portfolio: str, window: int) -> dict:
    book, levels, returns, _ = load_inputs(snapshot, portfolio)
    dates = returns.index[-(window + 1):]
    fit_returns = returns.loc[:dates[0]]
    fits = {c: fit_garch(fit_returns[c]) for c in returns.columns}

    gvols = garch_volatility(returns, fits, seed_window=CFG.ewma_seed_window)
    gfc = garch_vol_forecast_series(returns, fits, seed_window=CFG.ewma_seed_window)
    results = run_backfill(book, levels, returns, n_days=window,
                           methods=(CHAMPION, CHALLENGER),
                           vol_models={CHALLENGER: (gvols, gfc)})

    firm = results[results["scope"] == "FIRM"]
    p = 1.0 - CFG.alpha_var
    champ = _firm_stats(firm[firm["method"] == CHAMPION], p)
    chall = _firm_stats(firm[firm["method"] == CHALLENGER], p)

    wide = firm.pivot(index="as_of", columns="method", values="var")
    delta = wide[CHALLENGER] / wide[CHAMPION] - 1.0
    impact = {"mean": float(delta.mean()), "mean_abs": float(delta.abs().mean()),
              "max_abs": float(delta.abs().max()),
              "max_abs_date": delta.abs().idxmax().date()}
    unhealthy = [code for code, f in fits.items()
                 if not f.converged or f.persistence > PROMOTE_MAX_PERSISTENCE]
    promote, failures = decide_promotion(champ, chall, impact["mean_abs"], unhealthy)

    params = repr(sorted((c, f.omega, f.alpha, f.beta) for c, f in fits.items()))
    return {"as_of": returns.index[-1].date(), "window": window,
            "fit_cutoff": dates[0].date(), "fits": fits,
            "champ": champ, "chall": chall, "impact": impact,
            "promote": promote, "failures": failures,
            "candidate_hash": hashlib.sha256(f"{params}|{window}".encode()).hexdigest()[:12],
            "sample_vol": fit_returns.std()}


def render(m: dict) -> str:
    c, g, imp = m["champ"], m["chall"], m["impact"]
    verdict = "PROMOTE" if m["promote"] else "HOLD"
    lines = [
        "# Model change pack — challenger vol filter for FHS VaR",
        "",
        f"*Champion: EWMA (lambda = {CFG.lambda_ewma}). Candidate: GARCH(1,1), "
        f"Gaussian QMLE per factor, parameters frozen at {m['fit_cutoff']} (no "
        f"look-ahead into the {m['window']}-day evaluation window ending "
        f"{m['as_of']}). Candidate hash `{m['candidate_hash']}`, code "
        f"`{code_version()}`. Regenerate: `python -m risk.jobs.challenger`.*",
        "",
        "## Promotion criteria (pre-registered)",
        "",
        "Fixed before results are computed; the verdict is mechanical:",
        "",
        f"1. Fit health: every factor converges with persistence <= {PROMOTE_MAX_PERSISTENCE}",
        "   (a boundary-stuck fit has a meaningless unconditional level - promoting",
        "   it would be adopting IGARCH by accident).",
        "2. Challenger 250-day Basel zone is GREEN.",
        "3. Coverage error |exceptions - expected| does not worsen vs champion.",
        f"4. Christoffersen conditional coverage does not reject (p >= {PROMOTE_MIN_CC_P}).",
        f"5. Mean daily |VaR delta| <= {PROMOTE_MAX_MEAN_ABS_DELTA:.0%} (adoption stability).",
        "",
        "## Candidate parameters",
        "",
        "| Factor | alpha | beta | persistence | half-life (d) | uncond vol | sample vol | fit health |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for code, f in m["fits"].items():
        health = ("no convergence" if not f.converged
                  else "BOUNDARY" if f.persistence > PROMOTE_MAX_PERSISTENCE else "ok")
        lines.append(
            f"| {code} | {f.alpha:.3f} | {f.beta:.3f} | {f.persistence:.4f} "
            f"| {f.half_life_days:.0f} | {f.uncond_var ** 0.5:.4g} "
            f"| {m['sample_vol'][code]:.4g} | {health} |")
    both = c["exception_dates"] & g["exception_dates"]
    lines += [
        "",
        "EWMA is the IGARCH boundary (omega 0, alpha 1-lambda = 0.06, beta = "
        "lambda): every fitted persistence below 1 is the candidate disagreeing "
        "with the champion's infinite-memory assumption.",
        "",
        "## Outcomes (firm, out-of-sample)",
        "",
        "| | Champion (EWMA-FHS) | Challenger (GARCH-FHS) |",
        "|---|---|---|",
        f"| Exceptions / expected | {c['x']} / {c['expected']:.1f} | {g['x']} / {g['expected']:.1f} |",
        f"| Kupiec p | {c['kupiec_p']:.3f} | {g['kupiec_p']:.3f} |",
        f"| Conditional coverage p | {c['cc_p']:.3f} | {g['cc_p']:.3f} |",
        f"| Basel zone, {g['tl_n']}d (multiplier) | {c['zone']} ({c['multiplier']:.2f}) | {g['zone']} ({g['multiplier']:.2f}) |",
        f"| Average firm VaR | ${c['avg_var']:,.0f} | ${g['avg_var']:,.0f} |",
        "",
        f"Exception days: {len(both)} shared, "
        f"{len(c['exception_dates'] - g['exception_dates'])} champion-only, "
        f"{len(g['exception_dates'] - c['exception_dates'])} challenger-only.",
        "",
        "## Impact analysis",
        "",
        f"Daily VaR delta (challenger vs champion): mean {imp['mean']:+.1%}, "
        f"mean absolute {imp['mean_abs']:.1%}, worst {imp['max_abs']:.1%} "
        f"on {imp['max_abs_date']}.",
        "",
        f"## Verdict: {verdict}",
        "",
    ]
    if m["failures"]:
        lines += ["Failed criteria:", ""] + [f"- {f}" for f in m["failures"]]
    else:
        lines += ["All pre-registered criteria pass. Adoption would still gate on "
                  "a refit schedule and a second evaluation window."]
    lines += [
        "",
        "## Caveats",
        "",
        "- Parameters are frozen at the window start; production would refit on a",
        "  schedule (monthly) with the same pack regenerated per refit.",
        "- Both models see the same forward-fill-imprinted returns (RNIV R3), so",
        "  the comparison is fair but inherits that damping.",
        "- One window, one book: a promotion decision would want this pack over",
        "  several windows (the 750-day chart covers the champion only).",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="risk.jobs.challenger")
    parser.add_argument("--snapshot", default="data/seed/market_snapshot.parquet")
    parser.add_argument("--portfolio", default="data/seed/portfolio.yaml")
    parser.add_argument("--window", type=int, default=250)
    parser.add_argument("--out", default="docs/challenger_garch.md")
    args = parser.parse_args(argv)

    m = measure(args.snapshot, args.portfolio, args.window)
    report = render(m)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(report)
    print(report.split("## Candidate parameters")[0])
    print(f"[challenger] verdict: {'PROMOTE' if m['promote'] else 'HOLD'}; "
          f"report -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
