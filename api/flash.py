"""Flash marks endpoint support - indicative intraday P&L over the EOD book.

Two costs, two cache lives. Quotes are cheap and refresh on a short TTL;
the revaluation that turns them into P&L is the expensive part and holds
longer, with an explicit refresh able to bust both. Nothing here writes to
the results tables: the nightly batch stays the record.
"""

from __future__ import annotations

import datetime as dt
import sys
import threading

import pandas as pd
from sqlalchemy.engine import Connection

from api.sandbox import _scenario_set
from risk import db, flash
from risk_engine.engine import aggregate, revalue
from risk_engine.factors import to_returns

QUOTE_TTL = dt.timedelta(seconds=60)     # tape refresh
REVAL_TTL = dt.timedelta(minutes=10)     # the expensive step; refresh busts it

_CACHE: dict[int, tuple[dt.datetime, dict]] = {}
_LOCK = threading.Lock()


def _cached(run_id: int, ttl: dt.timedelta, now: dt.datetime) -> dict | None:
    with _LOCK:
        hit = _CACHE.get(run_id)
    if hit is None:
        return None
    stamped, payload = hit
    return payload if now - stamped < ttl else None


def _store(run_id: int, now: dt.datetime, payload: dict) -> None:
    with _LOCK:
        _CACHE.clear()          # one run at a time; the map is a slot, not a store
        _CACHE[run_id] = (now, payload)


def compute_flash(conn: Connection, run_id: int, run_date: dt.date, *,
                  refresh: bool = False, now: dt.datetime | None = None,
                  fetch=flash.fetch_flash_quotes) -> dict:
    """Indicative P&L of the EOD book under the latest available quotes.

    The book and its EOD closes come from the resolved run; quotes replace the
    closes where they exist. P&L is a full revaluation against the close, so
    it is the same arithmetic the batch's clean P&L uses - only the mark is
    intraday, and the response says which factors are live.
    """
    now = now or dt.datetime.now(dt.UTC)
    if not refresh:
        cached = _cached(run_id, REVAL_TTL, now)
        if cached is not None:
            return {**cached, "cached": True}

    market = _scenario_set(conn, run_id, run_date)
    book = db.read_book(conn)
    closes = {code: float(market.levels[code]) for code in market.levels.index}

    quotes = fetch(list(closes))
    levels, provenance = flash.flash_levels(closes, quotes, market.convs)
    live = [p for p in provenance if not p.carried]
    rejected = [p for p in provenance if p.carried and p.note and p.note != "no quote"]
    for p in rejected:                       # a bad vendor mark is never silent
        print(f"[flash] rejected {p.factor_code}: {p.note}", file=sys.stderr)

    # the move each factor has made since the close, in its own convention -
    # to_returns is the same converter the batch uses, so bp stays bp
    frame = pd.DataFrame([closes, levels], index=pd.to_datetime(
        [pd.Timestamp(run_date), pd.Timestamp(run_date) + pd.Timedelta(days=1)]))
    moves = to_returns(frame, market.convs).dropna().iloc[0]

    pnl = aggregate(revalue(book, pd.Series(closes), moves.to_frame().T), book).iloc[0]
    desks = [{"desk_code": scope, "is_aggregate": scope == "FIRM",
              "flash_pnl": round(float(pnl[scope]), 2)} for scope in pnl.index]
    desks.sort(key=lambda d: (not d["is_aggregate"], d["desk_code"]))

    payload = {
        "as_of": run_date,
        "run_id": run_id,
        "indicative": True,
        "quoted_at": max((p.quoted_at for p in live), default=None),
        "live_factors": len(live),
        "total_factors": len(provenance),
        "desks": desks,
        "rejected_factors": [p.factor_code for p in rejected],
        "factors": [{"factor_code": p.factor_code, "level": round(p.level, 6),
                     "close": round(closes[p.factor_code], 6),
                     "move": round(float(moves[p.factor_code]), 6),
                     "carried": p.carried, "note": p.note} for p in provenance],
        "cached": False,
    }
    _store(run_id, now, payload)
    return payload
