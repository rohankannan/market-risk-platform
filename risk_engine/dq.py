"""Data-quality checks (pure functions; the EOD batch wires them to dq_issues).

Policy: FLAG, never delete. Auto-scrubbing genuine crash days destroys the very
tail that VaR feeds on - outliers are WARN + investigate. BLOCK is reserved for
structurally impossible values (unit errors) and gaps beyond the forward-fill
cap; any BLOCK downgrades the run to PARTIAL, never a silent green.
"""

from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

from .var import ewma_volatility

YIELD_BOUNDS_PCT = (-2.0, 25.0)
YIELD_JUMP_BP = 75.0
LOG_OUTLIER_VOL_MULT = 6.0
LOG_OUTLIER_ABS = 0.03
STALE_RUN_LENGTH = 5
FX_MEDIAN_TOLERANCE = 0.50
REVISION_REL_TOL = 1e-9        # below this a re-ingested value is float noise, not a revision
MARKET_VALUE_DECIMALS = 8      # market_data stores numeric(18,8) - compare at storage precision
FLASH_SCOPE = "FIRM"
TOP_VOL_MOVERS = 3


def check_outliers(returns_window: pd.DataFrame, conventions: Mapping[str, str],
                   ewma_lam: float = 0.94, seed_window: int = 30) -> list[dict]:
    """Flag the LAST row's returns that are extreme vs trailing EWMA vol.

    LOG factors: |r| > 6 x trailing vol AND |r| > 3% absolute.
    ABS_BP factors: |change| > 75bp/day. Severity WARN (flag, never delete).
    """
    issues = []
    if len(returns_window) < seed_window + 2:
        return issues
    vols = ewma_volatility(returns_window.iloc[:-1], lam=ewma_lam, seed_window=seed_window)
    last = returns_window.iloc[-1]
    trail = vols.iloc[-1]
    for code, r in last.items():
        if pd.isna(r):
            continue
        conv = conventions[code]
        if conv == "LOG":
            if abs(r) > LOG_OUTLIER_VOL_MULT * trail[code] and abs(r) > LOG_OUTLIER_ABS:
                issues.append({"factor_code": code, "check_name": "OUTLIER_RETURN",
                               "severity": "WARN",
                               "detail": {"return": float(r), "trailing_vol": float(trail[code]),
                                          "zscore": float(r / trail[code])}})
        elif conv == "ABS_BP" and abs(r) > YIELD_JUMP_BP:
            issues.append({"factor_code": code, "check_name": "OUTLIER_RETURN",
                           "severity": "WARN",
                           "detail": {"change_bp": float(r), "threshold_bp": YIELD_JUMP_BP}})
    return issues


def check_staleness(levels_window: pd.DataFrame, factor_types: Mapping[str, str],
                    run_length: int = STALE_RUN_LENGTH) -> list[dict]:
    """PRICE/FX factors repeating the identical value `run_length` times = a dead
    source silently repeating - the failure mode pure gap checks miss."""
    issues = []
    if len(levels_window) < run_length:
        return issues
    tail = levels_window.iloc[-run_length:]
    for code in levels_window.columns:
        if factor_types.get(code) not in ("PRICE", "FX_RATE"):
            continue
        vals = tail[code].dropna()
        if len(vals) == run_length and vals.nunique() == 1:
            issues.append({"factor_code": code, "check_name": "STALE", "severity": "WARN",
                           "detail": {"value": float(vals.iloc[0]), "days": run_length}})
    return issues


def check_bounds(levels_row: pd.Series, factor_types: Mapping[str, str],
                 fx_median_1y: pd.Series | None = None) -> list[dict]:
    """Structurally impossible values (percent-vs-decimal regressions and the
    like) - severity BLOCK."""
    issues = []
    for code, v in levels_row.items():
        if pd.isna(v):
            continue
        ftype = factor_types.get(code)
        bad, detail = False, {"value": float(v)}
        if ftype == "YIELD" and not (YIELD_BOUNDS_PCT[0] <= v <= YIELD_BOUNDS_PCT[1]):
            bad, detail["bounds_pct"] = True, list(YIELD_BOUNDS_PCT)
        elif ftype in ("PRICE", "VOL_INDEX") and v <= 0:
            bad = True
        elif ftype == "FX_RATE":
            if v <= 0:
                bad = True
            elif fx_median_1y is not None and code in fx_median_1y.index:
                med = float(fx_median_1y[code])
                if med > 0 and abs(v / med - 1.0) > FX_MEDIAN_TOLERANCE:
                    bad, detail["median_1y"] = True, med
        if bad:
            issues.append({"factor_code": code, "check_name": "UNIT_BOUND",
                           "severity": "BLOCK", "detail": detail})
    return issues


def fx_trailing_median(levels: pd.DataFrame, factor_types: Mapping[str, str],
                       days: int = 252) -> pd.Series:
    fx_cols = [c for c in levels.columns if factor_types.get(c) == "FX_RATE"]
    return levels[fx_cols].iloc[-days:].median() if fx_cols else pd.Series(dtype=float)


def has_block(issues: list[dict]) -> bool:
    return any(i["severity"] == "BLOCK" for i in issues)


# ------------------------------------------------------- output-side controls

def flash_dod_check(curr_var: Mapping[str, float], prev_var: Mapping[str, float],
                    threshold: float) -> dict | None:
    """Day-over-day movement check on the headline number, run BEFORE anyone
    reads it: a large move is not an error (a real vol spike moves VaR), but
    it must arrive pre-explained. Returns a WARN issue carrying the per-desk
    attribution, or None when the move is inside the threshold or there is no
    prior run to compare against."""
    if FLASH_SCOPE not in curr_var or FLASH_SCOPE not in prev_var:
        return None
    prev = float(prev_var[FLASH_SCOPE])
    if prev <= 0:
        return None
    pct = float(curr_var[FLASH_SCOPE]) / prev - 1.0
    if abs(pct) <= threshold:
        return None
    desk_deltas = {s: round(float(curr_var[s]) - float(prev_var[s]), 2)
                   for s in curr_var if s != FLASH_SCOPE and s in prev_var}
    return {"factor_code": None, "check_name": "FLASH_DOD", "severity": "WARN",
            "detail": {"prev_firm_var": round(prev, 2),
                       "curr_firm_var": round(float(curr_var[FLASH_SCOPE]), 2),
                       "pct_move": round(pct, 4), "threshold": threshold,
                       "desk_var_deltas": desk_deltas}}


def top_vol_movers(fc_now: pd.Series, fc_prev: pd.Series,
                   n: int = TOP_VOL_MOVERS) -> dict[str, float]:
    """Largest relative vol-forecast moves - the usual explanation for a flash
    trip under a filtered model."""
    common = fc_now.index.intersection(fc_prev.index)
    prev = fc_prev[common]
    moves = (fc_now[common] / prev.where(prev > 0) - 1.0).dropna()
    top = moves.reindex(moves.abs().sort_values(ascending=False).index).head(n)
    return {k: round(float(v), 4) for k, v in top.items()}


def detect_revisions(existing: Mapping[tuple, tuple], incoming: list[dict],
                     rel_tol: float = REVISION_REL_TOL) -> list[dict]:
    """History that changed under our feet, classified before the upsert
    overwrites it. `existing` maps (factor_id, obs_date) -> (value, is_ffilled);
    `incoming` are upsert-ready market rows. A synthetic fill replaced by a
    real print is routine (FFILL_REPLACED); a changed REAL print is a vendor
    restatement (VENDOR_REVISION) and the reason yesterday's VaR is no longer
    reproducible from today's database without the log."""
    out = []
    for row in incoming:
        key = (row["factor_id"], row["obs_date"])
        if key not in existing:
            continue
        old, was_ffilled = existing[key]
        # the stored value already lives at numeric(18,8); quantizing the
        # incoming one the same way stops identical vendor values from
        # registering as phantom revisions on small-magnitude factors
        old = float(old)
        new = round(float(row["value"]), MARKET_VALUE_DECIMALS)
        denom = max(abs(old), abs(new), 1e-12)
        if abs(new - old) / denom <= rel_tol:
            continue
        out.append({"factor_id": row["factor_id"], "obs_date": row["obs_date"],
                    "old_value": old, "new_value": new,
                    "revision_type": "FFILL_REPLACED" if was_ffilled else "VENDOR_REVISION",
                    "source": row["source"]})
    return out
