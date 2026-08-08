// Pure data transforms for the Overview page - testable without React.
import type { EChartsOption } from "echarts";

import type { DeskRisk, HistoryPoint } from "../api/types";
import { fmtMoney } from "./format";

// literal hexes mirroring the Streamlit charts (dashboard/ui.py VAR_LINES,
// EXCEPTION_DOT). ECharts paints to canvas, which cannot resolve CSS custom
// properties - so no var() references; the P&L pair doubles as tokens.css
// --pnl-pos/--pnl-neg.
export const VAR_HS_COLOR = "#1B2A4A";
export const VAR_FHS_COLOR = "#5B8DEF";
export const EXCEPTION_COLOR = "#C61A1A";
export const PNL_POS = "#3E9C55";
export const PNL_NEG = "#C64545";

export type UtilLevel = "ok" | "warn" | "hot" | "breach";

// the API's limit_status is authoritative for BREACH: server-side utilization
// is rounded to 4dp, so a hairline breach can arrive as exactly 1.0000
export function utilLevel(u: number, status?: DeskRisk["limit_status"]): UtilLevel {
  if (status === "BREACH" || u > 1.0) return "breach";
  if (u >= 0.9) return "hot";
  if (u >= 0.7) return "warn";
  return "ok";
}

export const UTIL_BG: Record<UtilLevel, string> = {
  ok: "var(--util-ok)",
  warn: "var(--util-warn)",
  hot: "var(--util-hot)",
  breach: "var(--util-breach)",
};

// solid fills for the tile's inline bar: red is earned only past the limit
export const UTIL_BAR: Record<UtilLevel, string> = {
  ok: "var(--zone-green)",
  warn: "var(--zone-amber)",
  hot: "var(--zone-amber)",
  breach: "var(--zone-red)",
};

// P&L bars vs the negated VaR lines, exception days as labeled dots
export function pnlVsVarOption(points: HistoryPoint[]): EChartsOption {
  const dates = points.map((p) => p.date);
  const exceptions = points.filter((p) => p.exception_hs || p.exception_fhs);
  return {
    animation: false,
    grid: { left: 70, right: 16, top: 28, bottom: 24 },
    tooltip: { trigger: "axis" },
    legend: { top: 0, itemWidth: 14 },
    xAxis: { type: "category", data: dates },
    yAxis: {
      type: "value",
      axisLabel: { formatter: (v: number) => fmtMoney(v) },
    },
    series: [
      {
        name: "P&L",
        type: "bar",
        color: PNL_POS, // feeds the legend swatch; per-datum itemStyle wins below
        data: points.map((p) => ({
          value: p.pnl,
          itemStyle: { color: (p.pnl ?? 0) >= 0 ? PNL_POS : PNL_NEG },
        })),
        barMaxWidth: 6,
      },
      {
        name: "-VaR (HS)",
        type: "line",
        data: points.map((p) => (p.var_hs == null ? null : -p.var_hs)),
        showSymbol: false,
        lineStyle: { color: VAR_HS_COLOR, width: 1.5 },
        itemStyle: { color: VAR_HS_COLOR },
      },
      {
        name: "-VaR (FHS)",
        type: "line",
        data: points.map((p) => (p.var_fhs == null ? null : -p.var_fhs)),
        showSymbol: false,
        lineStyle: { color: VAR_FHS_COLOR, width: 1.2, type: "dashed" },
        itemStyle: { color: VAR_FHS_COLOR },
      },
      {
        name: "Exception",
        type: "scatter",
        data: exceptions.map((p) => [p.date, p.pnl]),
        symbolSize: 7,
        itemStyle: { color: EXCEPTION_COLOR },
        z: 5,
      },
    ],
  };
}
