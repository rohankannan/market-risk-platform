"""Central configuration: every tunable is named here — no magic numbers in engine code."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


@dataclass(frozen=True)
class RiskConfig:
    # Scenario window: Basel minimum is 250 days; 500 stabilizes the 99% quantile
    # (at n=500 the estimator interpolates between the 5th and 6th worst losses).
    # It also buys the ability to put an interval on the number at all: a
    # distribution-free interval for a 99% quantile tops out at ~91.9% coverage
    # at n=250 and reaches 95% only from n=299 (unit-tested in test_inference).
    lookback_days: int = 500

    # RiskMetrics decay factor; half-life = ln(0.5)/ln(0.94) ~ 11 days.
    lambda_ewma: float = 0.94
    ewma_seed_window: int = 30

    alpha_var: float = 0.99
    alpha_es: float = 0.975

    base_horizon_days: int = 1
    # 10-day figures reported via sqrt-of-time scaling; iid caveat documented in the model doc.
    reporting_horizon_days: int = 10

    # Data-quality: forward-fill cap (per-factor override lives on the risk_factors table).
    ffill_limit_days: int = 3

    # Flash check: a day-over-day firm-VaR move beyond this flags the run
    # (WARN with attribution) before the number is read as final.
    flash_dod_threshold: float = 0.25

    # MVP: fixed FRTB-style stressed window (GFC year). The programmatic
    # worst-window search over 2007+ history is a winter deliverable (es.find_stressed_window).
    stressed_window_start: dt.date = dt.date(2008, 9, 12)
    stressed_window_end: dt.date = dt.date(2009, 9, 11)

    pla_window_days: int = 250

    # Single seed for anything stochastic (simulated critical values, synthetic tests).
    seed: int = 42

    # Sampling uncertainty (risk_engine/inference.py). n_bootstrap is the
    # replicate count; bootstrap_block_days is the moving-block length, which
    # keeps runs of adjacent days together because daily P&L is not iid - vol
    # clusters, and an iid resample therefore understates the spread. Tied to the
    # reporting horizon on purpose: the dependence that matters is the dependence
    # over the horizon the number is quoted for.
    n_bootstrap: int = 2000
    bootstrap_block_days: int = 10


DEFAULT_CONFIG = RiskConfig()
