"""End-of-day pipeline CLI - the batch job is a plain CLI; the scheduler is thin
and swappable (APScheduler locally, GitHub Actions cron deployed).

    python -m risk.jobs.eod run --date 2026-10-01 [--steps ingest,dq,risk] [--force]
    python -m risk.jobs.eod backfill --start 2026-01-02 --end 2026-10-01 [--resume]

Steps are pure functions registered in STEPS; unimplemented ones fail loudly
with their scheduled milestone - no silent green.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time


def step_ingest(ctx: dict) -> None:
    raise NotImplementedError("milestone 2 (Aug 24-Sep 14): snapshot replay + yfinance/FRED top-up")


def step_dq_gate(ctx: dict) -> None:
    raise NotImplementedError("milestone 2: completeness/ffill/outlier/staleness/bounds checks -> dq_issues")


def step_risk_measures(ctx: dict) -> None:
    raise NotImplementedError("milestone 2: HS + FHS VaR, ES 97.5, stressed ES -> risk_results")


def step_scenarios(ctx: dict) -> None:
    raise NotImplementedError("milestone 3 (Sep 15-28): replays + hypothetical shocks -> scenario_results")


def step_backtest(ctx: dict) -> None:
    raise NotImplementedError("milestone 2: exception detection + Kupiec/Christoffersen/traffic light")


STEPS = {
    "ingest": step_ingest,
    "dq": step_dq_gate,
    "risk": step_risk_measures,
    "scenarios": step_scenarios,
    "backtest": step_backtest,
}


def run_day(run_date: dt.date, steps: list[str], force: bool = False) -> int:
    print(f"[eod] run {run_date} steps={steps} force={force}")
    ctx: dict = {"run_date": run_date}
    for name in steps:
        t0 = time.perf_counter()
        try:
            STEPS[name](ctx)
        except NotImplementedError as exc:
            print(f"[eod] step {name!r}: NOT IMPLEMENTED - {exc}", file=sys.stderr)
            return 2
        print(f"[eod] step {name!r} ok ({time.perf_counter() - t0:.1f}s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="risk.jobs.eod")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="run the EOD cycle for one date")
    p_run.add_argument("--date", type=dt.date.fromisoformat, default=dt.date.today())
    p_run.add_argument("--steps", default=",".join(STEPS))
    p_run.add_argument("--force", action="store_true")

    p_bf = sub.add_parser("backfill", help="seed history: bulk-fetch once, then loop business days")
    p_bf.add_argument("--start", type=dt.date.fromisoformat, required=True)
    p_bf.add_argument("--end", type=dt.date.fromisoformat, required=True)
    p_bf.add_argument("--resume", action="store_true", help="skip days already SUCCESS in risk_runs")

    args = parser.parse_args(argv)
    if args.cmd == "run":
        steps = [s.strip() for s in args.steps.split(",") if s.strip()]
        unknown = set(steps) - set(STEPS)
        if unknown:
            parser.error(f"unknown steps: {sorted(unknown)}; valid: {list(STEPS)}")
        return run_day(args.date, steps, args.force)
    if args.cmd == "backfill":
        print("[eod] backfill: milestone 2", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
