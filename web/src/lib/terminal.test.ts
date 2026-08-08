import type { DeskPosition, HistoryPoint } from "../api/types";
import { buildBook, buildTerminalChart, CHART_MID_Y, fmtNotional, PX_PER_M } from "./terminal";

const P = (pnl: number | null, varHs: number | null, exc = false): HistoryPoint => ({
  date: "2026-08-06",
  var_hs: varHs,
  var_fhs: varHs == null ? null : varHs * 1.1,
  es_975: null,
  pnl,
  exception_hs: exc,
  exception_fhs: false,
});

test("chart geometry: 62px per $1M around the y=160 zero line", () => {
  const c = buildTerminalChart([P(1_000_000, 1_140_000)], "HS");
  expect(c.bars[0].y).toBeCloseTo(CHART_MID_Y - PX_PER_M, 6); // +$1M rises 62px
  expect(c.bars[0].h).toBeCloseTo(PX_PER_M, 6);
  expect(c.bars[0].positive).toBe(true);
  // the -VaR mirror sits below the zero line by var/1M * 62
  expect(c.varPathNeg).toContain(`,${(CHART_MID_Y + 1.14 * PX_PER_M).toFixed(1)}`);
});

test("exception days carry a dot just below the bar tip", () => {
  const c = buildTerminalChart([P(-1_300_000, 1_140_000, true), P(200_000, 1_140_000)], "HS");
  expect(c.dots).toHaveLength(1);
  expect(c.dots[0].cy).toBeCloseTo(CHART_MID_Y + 1.3 * PX_PER_M + 6, 6);
  expect(c.bars[0].exception).toBe(true);
});

test("null P&L days draw no bar; null VaR days leave the band", () => {
  const c = buildTerminalChart([P(null, 1_000_000), P(100, null)], "HS");
  expect(c.bars).toHaveLength(1);
  expect(c.varPath.match(/L/g) ?? []).toHaveLength(0); // single VaR point, no segment
});

const POS = (
  ticker: string,
  type: string,
  qty: number,
  factor: string | null,
): DeskPosition => ({
  ticker,
  instrument_type: type,
  quantity: qty,
  factor_class: "EQ",
  factor_code: factor,
  option_type: null,
  moneyness: null,
  maturity_years: null,
  standalone_var: 0,
  component_es: 0,
  marginal_var: 0,
  pct_of_desk: null,
});

test("book rows: linear qty x level, bonds face, collar folds into its underlier", () => {
  const rows = buildBook(
    {
      EQUITY: [
        POS("XOM", "STOCK", -21_000, "EQ.XOM"), // API serves component order...
        POS("SPY", "ETF", 9_400, "EQ.SPY"),
        POS("SPY_PUT_95", "OPTION", 7_800, "EQ.SPY"),
      ],
      // ...and the rail re-sorts to the book order, tenor-ordered for rates
      RATES: [
        POS("UST_10Y", "GOVT_BOND", 60_000_000, "IR.UST.10Y"),
        POS("UST_2Y", "GOVT_BOND", 90_000_000, "IR.UST.2Y"),
      ],
      FX: [POS("EURUSD_SPOT", "FX_SPOT", 19_000_000, "FX.EURUSD")],
    },
    { "EQ.SPY": 637.1, "EQ.XOM": 118.42, "FX.EURUSD": 1.0942 },
  );
  expect(rows.map((r) => r.ticker)).toEqual([
    "SPY +COLLAR", // option leg folded in, no standalone row
    "XOM",
    "EURUSD", // display names drop _SPOT and space the UST tenors
    "UST 2Y",
    "UST 10Y",
  ]);
  expect(rows[0].notional).toBeCloseTo(9_400 * 637.1);
  expect(rows[3].face).toBe(true);
  expect(fmtNotional(rows[3])).toBe("$90.0M FACE");
  expect(fmtNotional(rows[1])).toBe("−$2.5M"); // short XOM, minus not parentheses
});

test("a missing factor level reads as unavailable, never $0", () => {
  const rows = buildBook(
    { EQUITY: [POS("SPY", "ETF", 9_400, "EQ.SPY")], FX: [], RATES: [] },
    {}, // no levels served
  );
  expect(rows[0].notional).toBeNull();
  expect(fmtNotional(rows[0])).toBe("—");
});
