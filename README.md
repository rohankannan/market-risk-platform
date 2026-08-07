# RiskDesk

An end-of-day **market-risk platform** for a mock three-desk trading book (cash equities, FX spot, US rates), built the way a bank's risk desk runs its nightly cycle: ingest market data → data-quality gate → revalue the book → VaR / Expected Shortfall → limit checks → stress replay → regulatory backtesting → dashboard.

> Every night, a bank's market-risk function runs exactly this loop. This is a small but complete version of it — real market data, a real database, tested math, and the validation statistics regulators actually use.

## Architecture

```mermaid
flowchart LR
  subgraph sources[Data sources]
    YF[yfinance equities] --> ING
    ST[Stooq fallback] -.-> ING
    FR[FRED rates, FX, VIX] --> ING
  end
  ING[ingest] --> DQ[data-quality gate] --> ENG[risk engine]
  DQ --> PG[(Postgres 16)]
  ENG --> PG
  SCHED[nightly batch] --> ING
  PG --> API[FastAPI] --> FE[dashboard]
```

## The backtest

750 trading days of out-of-sample 1-day 99% VaR on the firm book, against next-day clean P&L:

![Firm VaR backtest: historical sim vs EWMA-filtered](docs/img/backtest_firm.png)

| method | days | exceptions | expected | Kupiec p | Christoffersen CC p | Basel zone (250d) |
|---|---|---|---|---|---|---|
| Historical sim | 750 | 5 | 7.5 | 0.33 | 0.60 | GREEN |
| Filtered HS (EWMA) | 750 | 10 | 7.5 | 0.38 | 0.60 | GREEN |

The equal-weight 500-day window makes plain historical sim slow in both directions: it stays wide through the calm late-2025 regime (capital inefficiency) and only steps up months after the April 2025 vol spike, once those scenarios finally enter the window. The EWMA-filtered VaR tracks the regime — wider into stress, tighter in calm — with coverage near the nominal 1%. Regenerate with `python -m risk.jobs.backfill`.

## What's implemented (honest status)

| Component | Status |
|---|---|
| Risk engine core: HS VaR, EWMA filtering + FHS scenarios, ES 97.5, bond pricer (DV01/convexity by bump-and-reprice) | ✅ implemented, known-answer tested |
| Backtesting stats: Kupiec POF, Christoffersen (independence + conditional coverage), Basel traffic light with multiplier add-ons | ✅ implemented, known-answer tested |
| Postgres schema (13 tables) + Alembic migration + showcase analytics SQL | ✅ |
| Portfolio / factor definitions (desk mix calibrated to disclosed bank VaR risk-class shares), hypothetical shock catalog | ✅ (`data/seed/`, `scenarios/`) |
| Committed 2007+ market snapshot (17 factors), snapshot pipeline w/ source fallbacks | ✅ (`risk/jobs/snapshot.py`) |
| Seed loader (COPY-based, idempotent), portfolio revaluation engine (full reval + delta-gamma) | ✅ |
| 750-day out-of-sample backfill + backtest chart and stats | ✅ (`risk/jobs/backfill.py`) |
| EOD batch: ingestion w/ fallbacks, DQ gate (`dq_issues`, PARTIAL on blocks), risk + exception writes, scenario runs, idempotent run claims, DB backfill mode | ✅ (`risk/jobs/eod.py`) |
| Stress replays (GFC 2008, COVID 2020) + hypothetical shocks, written per run | ✅ |
| FastAPI read layer over the results tables: meta, risk summary w/ limit utilization + diversification, history, backtest stats, scenario results; typed response models, immutable caching on pinned `as_of` | ✅ (`api/`) |
| Streamlit dashboard, Neon deploy + nightly cron | 🔜 milestone 3 (by Oct 15) |
| Parametric VaR w/ implied vol, options sleeve → PLA test, CCAR-style scenarios, React dashboard | 🔜 winter |

## Quickstart

```bash
make venv        # python3.11+ virtualenv, editable install
make test        # known-answer test suite (no network, no DB needed)
make demo        # db-up + migrate + seed + 300-day backfill + EOD run, all offline
make api         # serve the API; interactive docs at http://localhost:8000/docs
```

## Design rules

- **Hand-roll anything an interviewer could ask you to derive** (EWMA recursion, VaR/ES estimators, Kupiec/Christoffersen likelihood ratios, bond pricing, traffic-light zones); import only optimizers and distribution functions (scipy `chi2`, `spearmanr`).
- **Return conventions are data, not code**: log returns for prices/FX, absolute bp for yields (log yield shocks explode when rates sit near zero, as in 2020) — carried on the `risk_factors` table.
- **P&L is exact, never linearized** in scenarios (`qty·S0·(exp(r)−1)`; bonds full-reval via closed-form). The linear approximation exists only in risk-theoretical P&L, so the future PLA test's HPL−RTPL gap is real and internally generated.
- **No demo, test, or CI run ever depends on a live third-party API** — the market-data snapshot is committed; live fetch is a top-up.
- **Loud failures**: data-quality breaches write `dq_issues` and mark the run `PARTIAL`; unimplemented steps exit non-zero with their scheduled milestone.

## Scope honesty

This implements the **ES piece of FRTB's internal-models approach** — 97.5% ES with stressed-period calibration — not liquidity horizons, NMRF, or (yet) the P&L-attribution test. PLA is deliberately deferred until the book has options: on a purely linear book, risk-theoretical and hypothetical P&L coincide and the test is vacuous. Full limitations: [docs/model_doc.md](docs/model_doc.md).

*Educational demonstration on public data; not investment advice.*
