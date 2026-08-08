// Shared chart palette and the P&L-vs-VaR option builder - testable without React.
import type { EChartsOption } from "echarts";

import type { HistoryPoint } from "../api/types";
import { fmtMoney } from "./format";

// terminal-palette literals (tokens.css). ECharts paints to canvas, which
// cannot resolve CSS custom properties - so no var() references. The HS series
// takes the working gold; red/green stay reserved for P&L sign and exceptions.
export const VAR_HS_COLOR = "#cfb991";
export const VAR_FHS_COLOR = "#6f9bf2";
export const EXCEPTION_COLOR = "#e0563f";
export const PNL_POS = "#5fbf7a";
export const PNL_NEG = "#e0563f";

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
