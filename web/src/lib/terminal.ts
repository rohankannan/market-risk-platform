// Pure builders for the terminal overview - testable without React.
import type { DeskPosition, HistoryPoint } from "../api/types";

// chart geometry per the design reference: 800x320 viewBox, zero line at
// y=160, 62px per $1M, bar width 2.4
export const CHART_W = 800;
export const CHART_H = 320;
export const CHART_MID_Y = 160;
export const PX_PER_M = 62;

export interface TerminalBar {
  x: number;
  y: number;
  h: number;
  exception: boolean;
  positive: boolean;
}

export interface TerminalChart {
  bars: TerminalBar[];
  dots: { cx: number; cy: number }[];
  varPath: string;
  varPathNeg: string;
}

const toY = (usd: number): number => CHART_MID_Y - (usd / 1e6) * PX_PER_M;

export function buildTerminalChart(points: HistoryPoint[], model: "HS" | "FHS"): TerminalChart {
  const varKey = model === "FHS" ? ("var_fhs" as const) : ("var_hs" as const);
  const excKey = model === "FHS" ? ("exception_fhs" as const) : ("exception_hs" as const);
  const n = points.length;
  const bars: TerminalBar[] = [];
  const dots: { cx: number; cy: number }[] = [];
  const varPts: [number, number, number][] = [];

  points.forEach((p, i) => {
    const x = n === 1 ? 0 : (i / (n - 1)) * CHART_W;
    const pl = p.pnl ?? 0;
    const y1 = toY(pl);
    if (p.pnl != null) {
      bars.push({
        x,
        y: Math.min(CHART_MID_Y, y1),
        h: Math.abs(y1 - CHART_MID_Y) || 0.5,
        exception: p[excKey],
        positive: pl >= 0,
      });
      if (p[excKey]) dots.push({ cx: x, cy: y1 + 6 });
    }
    const v = p[varKey];
    if (v != null) varPts.push([x, toY(v), toY(-v)]);
  });

  const path = (idx: 1 | 2) =>
    varPts.length ? "M" + varPts.map((p) => `${p[0].toFixed(1)},${p[idx].toFixed(1)}`).join("L") : "";
  return { bars, dots, varPath: path(1), varPathNeg: path(2) };
}

// ---------------------------------------------------------------- book rail

export interface BookRow {
  ticker: string;
  factorCode: string | null;
  desk: string;
  notional: number | null; // signed USD; bonds carry face; null when unlevelled
  face: boolean;
}

const BOOK_DESK_ORDER = ["EQUITY", "FX", "RATES"];

// the book's display order (data/seed/portfolio.yaml); tickers the API serves
// but the seed no longer books append after, in API order
const BOOK_TICKER_ORDER = [
  "SPY", "AAPL", "MSFT", "NVDA", "JPM", "XOM", "JNJ",
  "EURUSD_SPOT", "JPYUSD_SPOT", "GBPUSD_SPOT", "MXNUSD_SPOT",
  "UST_2Y", "UST_5Y", "UST_10Y", "UST_30Y",
];

const bookTicker = (t: string): string => t.replace(/_SPOT$/, "").replace(/^UST_/, "UST ");

// notional-equivalents for the book rail: linear legs qty x level, bonds their
// face, a missing level renders null (shown as unavailable, never $0). Option
// legs fold into their underlier's row ("+COLLAR") - their risk lives in the
// vega/greeks views, not a notional column.
export function buildBook(
  positionsByDesk: Record<string, DeskPosition[]>,
  levels: Record<string, number>,
): BookRow[] {
  const rows: BookRow[] = [];
  for (const desk of BOOK_DESK_ORDER) {
    const positions = [...(positionsByDesk[desk] ?? [])].sort((a, b) => {
      const ra = BOOK_TICKER_ORDER.indexOf(a.ticker);
      const rb = BOOK_TICKER_ORDER.indexOf(b.ticker);
      return (ra === -1 ? BOOK_TICKER_ORDER.length : ra) - (rb === -1 ? BOOK_TICKER_ORDER.length : rb);
    });
    const optionFactors = new Set(
      positions.filter((p) => p.instrument_type === "OPTION").map((p) => p.factor_code),
    );
    for (const p of positions) {
      if (p.instrument_type === "OPTION") continue;
      const face = p.instrument_type === "GOVT_BOND";
      const level = p.factor_code != null ? levels[p.factor_code] : undefined;
      const notional = face ? p.quantity : level != null ? p.quantity * level : null;
      const hasCollar = p.factor_code != null && optionFactors.has(p.factor_code);
      rows.push({
        ticker: hasCollar ? `${bookTicker(p.ticker)} +COLLAR` : bookTicker(p.ticker),
        factorCode: p.factor_code,
        desk,
        notional,
        face,
      });
    }
  }
  return rows;
}

export function fmtNotional(row: BookRow): string {
  if (row.notional == null) return "—";
  const m = Math.abs(row.notional) / 1e6;
  const sign = row.notional < 0 ? "−" : "";
  return `${sign}$${m.toLocaleString("en-US", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  })}M${row.face ? " FACE" : ""}`;
}
