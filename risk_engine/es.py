"""Expected-shortfall calibration windows.

MVP: the stressed window is FIXED to the GFC year (config.RiskConfig).
Winter deliverable: `find_stressed_window` - full-reval today's portfolio on
every daily shock since 2007, take the argmax rolling-250d ES window ("period
of significant stress for THIS portfolio", the FRTB idea made programmatic),
with an integration test asserting it lands in 2008-09 or 2020.
"""

from __future__ import annotations

import datetime as dt

from .config import DEFAULT_CONFIG, RiskConfig


def stressed_window(cfg: RiskConfig = DEFAULT_CONFIG) -> tuple[dt.date, dt.date]:
    """The ES stress-calibration window in force (fixed in MVP)."""
    return cfg.stressed_window_start, cfg.stressed_window_end


def find_stressed_window(portfolio_pnl_history, window: int = 250):
    """Programmatic worst-window search. Scheduled: winter break (needs 2007+ history)."""
    raise NotImplementedError(
        "Winter milestone: rolling-250d ES argmax over full-reval P&L history since 2007. "
        "MVP uses the fixed window from RiskConfig; see docs/model_doc.md limitations."
    )
