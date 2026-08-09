"""Flash-mark tests. The vendor is always mocked - the suite and CI never
make a network call, and the flash tier must degrade rather than disappear
when quotes are missing."""

import datetime as dt

import pytest

from risk.flash import (
    FLASH_SYMBOLS,
    UNQUOTED,
    FlashQuote,
    convert,
    flash_levels,
    move_in_convention,
)

NOW = dt.datetime(2026, 8, 7, 15, 30, tzinfo=dt.UTC)


def test_quote_conversion_per_symbol_kind():
    # equities, the vol index and the CBOE yield indices quote in their own units
    assert convert(1.0, 637.10) == pytest.approx(637.10)
    assert convert(1.0, 4.66) == pytest.approx(4.66)
    # a scale is still supported for any vendor that quotes a multiple
    assert convert(10.0, 41.8) == pytest.approx(4.18)
    # inverted FX: JPY per USD quoted, USD per JPY stored
    assert convert(-1.0, 148.0) == pytest.approx(1 / 148.0)
    with pytest.raises(ValueError, match="zero FX quote"):
        convert(-1.0, 0.0)


def test_every_mapped_symbol_declares_a_scale_and_2y_is_honestly_absent():
    """The 2Y has no CBOE index; carrying its close is correct, inventing it
    from a neighbouring tenor would not be."""
    assert "IR.UST.2Y" not in FLASH_SYMBOLS
    assert "IR.UST.2Y" in UNQUOTED
    for code, (symbol, scale) in FLASH_SYMBOLS.items():
        assert symbol and scale != 0, code


def test_flash_levels_marks_what_it_has_and_carries_the_rest():
    closes = {"EQ.SPY": 637.10, "IR.UST.2Y": 4.18, "FX.EURUSD": 1.1519}
    convs = {"EQ.SPY": "LOG", "IR.UST.2Y": "ABS_BP", "FX.EURUSD": "LOG"}
    quotes = {"EQ.SPY": FlashQuote("EQ.SPY", 645.00, NOW, carried=False)}
    levels, provenance = flash_levels(closes, quotes, convs)

    # the book is always fully marked: a missing quote carries its close
    assert levels == {"EQ.SPY": 645.00, "IR.UST.2Y": 4.18, "FX.EURUSD": 1.1519}
    by_code = {p.factor_code: p for p in provenance}
    assert by_code["EQ.SPY"].carried is False and by_code["EQ.SPY"].quoted_at == NOW
    assert by_code["IR.UST.2Y"].carried is True and by_code["IR.UST.2Y"].quoted_at is None
    assert [p.factor_code for p in provenance] == sorted(closes)


def test_flash_levels_with_no_quotes_at_all_is_the_close():
    """A dead vendor produces a flat flash panel labeled entirely carried -
    not an error, and never a silently stale mark."""
    closes = {"EQ.SPY": 637.10, "EQ.AAPL": 312.41}
    levels, provenance = flash_levels(closes, {}, {"EQ.SPY": "LOG", "EQ.AAPL": "LOG"})
    assert levels == closes
    assert all(p.carried for p in provenance)


def test_fetch_returns_empty_when_the_vendor_is_unreachable(monkeypatch):
    """Best-effort by design: the panel shows what it has rather than 500ing
    the page when a quote source hiccups."""
    import risk.flash as flash_mod

    class _Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("network down")

    monkeypatch.setitem(__import__("sys").modules, "yfinance",
                        type("m", (), {"Tickers": _Boom})())
    assert flash_mod.fetch_flash_quotes(["EQ.SPY"]) == {}
    assert flash_mod.fetch_flash_quotes([]) == {}


def test_move_in_convention_matches_the_stored_history_conventions():
    assert move_in_convention("ABS_BP", 4.18, 4.36) == pytest.approx(18.0)
    assert move_in_convention("ABS", 14.62, 16.62) == pytest.approx(2.0)
    assert move_in_convention("LOG", 100.0, 110.0) == pytest.approx(0.0953102, abs=1e-6)
    with pytest.raises(ValueError, match="positive levels"):
        move_in_convention("LOG", 100.0, 0.0)


def test_an_implausible_quote_is_refused_not_priced():
    """The CBOE yield indices once quoted ten times the yield and now quote it
    directly; a vendor changing convention must never reprice the book. A
    -416bp 'move' is rejected and the close carried, with the reason kept."""
    closes = {"IR.UST.10Y": 4.63, "EQ.SPY": 637.10}
    convs = {"IR.UST.10Y": "ABS_BP", "EQ.SPY": "LOG"}
    quotes = {
        "IR.UST.10Y": FlashQuote("IR.UST.10Y", 0.466, NOW, carried=False),   # /10 bug
        "EQ.SPY": FlashQuote("EQ.SPY", 645.00, NOW, carried=False),          # fine
    }
    levels, provenance = flash_levels(closes, quotes, convs)
    by_code = {p.factor_code: p for p in provenance}

    assert levels["IR.UST.10Y"] == 4.63                  # close carried, not 0.466
    assert by_code["IR.UST.10Y"].carried is True
    assert "beyond any observed session" in by_code["IR.UST.10Y"].note
    assert levels["EQ.SPY"] == 645.00 and by_code["EQ.SPY"].carried is False
