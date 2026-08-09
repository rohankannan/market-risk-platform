"""Intraday flash marks - indicative, never official.

The EOD batch remains the record: limits, backtests and the model doc all read
its tables and nothing here writes to them. This module answers a different
question between batches - "where is the book right now?" - from delayed
public quotes, and every number it produces is labeled indicative and stamped
with the quote time it came from.

Coverage is deliberately partial and says so. Equities and the vol index quote
intraday; Treasury yields quote through their CBOE index proxies (a tenth of a
yield point per index point); FX pairs quote as spot. A factor whose quote is
missing or stale carries its EOD close and is reported as CARRIED, because a
flash mark that silently substitutes yesterday for today is worse than one
that admits the gap.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

# yfinance symbols per factor, with the divisor that converts a vendor quote
# into the factor's own units so the conversion is never an if-statement
# elsewhere. The CBOE yield indices are quoted as the yield in percent today
# (^TNX 4.66 = 4.66%), NOT the ten-times convention they once used - which is
# exactly why no scale here is trusted on faith: implausible marks are
# rejected against the close below rather than priced.
FLASH_SYMBOLS: dict[str, tuple[str, float]] = {
    "EQ.SPY": ("SPY", 1.0),
    "EQ.AAPL": ("AAPL", 1.0),
    "EQ.MSFT": ("MSFT", 1.0),
    "EQ.NVDA": ("NVDA", 1.0),
    "EQ.JPM": ("JPM", 1.0),
    "EQ.XOM": ("XOM", 1.0),
    "EQ.JNJ": ("JNJ", 1.0),
    "VOL.SPX.IV30": ("^VIX", 1.0),
    "IR.UST.3M": ("^IRX", 1.0),      # 13-week bill discount rate, percent
    "IR.UST.5Y": ("^FVX", 1.0),
    "IR.UST.10Y": ("^TNX", 1.0),
    "IR.UST.30Y": ("^TYX", 1.0),
    "FX.EURUSD": ("EURUSD=X", 1.0),
    "FX.GBPUSD": ("GBPUSD=X", 1.0),
    "FX.JPYUSD": ("JPY=X", -1.0),    # quoted JPY per USD; invert to USD per JPY
    "FX.MXNUSD": ("MXN=X", -1.0),
}

# no CBOE index for the 2-year; it carries its close rather than being faked
# from a neighbouring tenor
UNQUOTED = ("IR.UST.2Y",)

STALE_AFTER = dt.timedelta(minutes=30)

# A move past these against the EOD close is a unit or convention error, not a
# market move: intraday rates have never moved 150bp, equities 25%, or VIX 25
# points in a session. Vendors change quote conventions without warning (the
# CBOE yield indices did), and a silently mispriced mark is worse than a
# missing one - so the quote is rejected and the close carried, visibly.
PLAUSIBLE_MOVE = {"LOG": 0.25, "ABS_BP": 150.0, "ABS": 25.0}


@dataclass(frozen=True)
class FlashQuote:
    factor_code: str
    level: float
    quoted_at: dt.datetime | None    # None when the level is a carried close
    carried: bool
    note: str | None = None          # why a level was carried


def move_in_convention(conv: str, close: float, level: float) -> float:
    """The move from close to level in the factor's own units - the same
    conventions to_returns applies to stored history."""
    if conv == "LOG":
        if close <= 0 or level <= 0:
            raise ValueError("log-convention factors need positive levels")
        return math.log(level / close)
    if conv == "ABS_BP":
        return (level - close) * 100.0
    return level - close


def convert(symbol_scale: float, raw: float) -> float:
    """Vendor quote -> the factor's own units. A negative scale marks an
    inverted FX pair (foreign-per-USD quoted, USD-per-foreign stored)."""
    if symbol_scale < 0:
        if raw == 0:
            raise ValueError("cannot invert a zero FX quote")
        return 1.0 / raw
    return raw / symbol_scale


def fetch_flash_quotes(codes: list[str], *, now: dt.datetime | None = None) -> dict[str, FlashQuote]:
    """Delayed quotes for the requested factors, best-effort.

    Network failures are not fatal here - a flash panel that disappears when
    one vendor hiccups is less useful than one that shows what it has and
    names what it is missing. Callers carry the EOD close for anything absent.
    """
    import yfinance as yf

    wanted = [c for c in codes if c in FLASH_SYMBOLS]
    if not wanted:
        return {}
    symbols = [FLASH_SYMBOLS[c][0] for c in wanted]
    now = now or dt.datetime.now(dt.UTC)
    out: dict[str, FlashQuote] = {}
    try:
        tickers = yf.Tickers(" ".join(symbols))
    except Exception:
        return {}
    for code in wanted:
        symbol, scale = FLASH_SYMBOLS[code]
        try:
            info = tickers.tickers[symbol].fast_info
            raw = float(info["last_price"])
            out[code] = FlashQuote(factor_code=code, level=convert(scale, raw),
                                   quoted_at=now, carried=False)
        except Exception:
            continue          # carried by the caller, and reported as such
    return out


def flash_levels(closes: dict[str, float], quotes: dict[str, FlashQuote],
                 convs: dict[str, str]) -> tuple[dict[str, float], list[FlashQuote]]:
    """Merge quotes over the EOD closes, rejecting implausible marks.

    Returns (levels for revaluation, per-factor provenance). Every factor in
    `closes` appears in both - the book must always be fully marked - and each
    entry says whether its mark is live, carried for want of a quote, or
    carried because the quote implied a move no session has ever produced.
    """
    levels: dict[str, float] = {}
    provenance: list[FlashQuote] = []
    for code, close in closes.items():
        q = quotes.get(code)
        note: str | None = None
        if q is not None:
            conv = convs.get(code, "LOG")
            try:
                move = move_in_convention(conv, close, q.level)
                if abs(move) > PLAUSIBLE_MOVE[conv]:
                    note = (f"quote {q.level:g} implies {move:+.4g} vs close {close:g} - "
                            "beyond any observed session, treated as a bad mark")
                    q = None
            except ValueError as exc:
                note, q = str(exc), None
        if q is not None:
            levels[code] = q.level
            provenance.append(q)
        else:
            levels[code] = close
            provenance.append(FlashQuote(factor_code=code, level=close, quoted_at=None,
                                         carried=True, note=note or "no quote"))
    provenance.sort(key=lambda p: p.factor_code)
    return levels, provenance
