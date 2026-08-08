// Pure data transforms for the backtesting page - testable without React.
import type { EChartsOption } from "echarts";

import type { HistoryPoint, PlaPoint } from "../api/types";
import { fmtMoney } from "./format";
import { EXCEPTION_COLOR, PNL_NEG, PNL_POS, VAR_FHS_COLOR, VAR_HS_COLOR } from "./overview";

// Basel traffic-light boundaries on a 250d window (backtest.py)
export const ROLLING_WINDOW = 250;
export const AMBER_FROM = 5;
export const RED_FROM = 10;

// zone band fills, canvas literals kept deliberately faint behind the count line
const BAND_GREEN = "rgba(95,191,122,0.08)";
const BAND_AMBER = "rgba(218,170,0,0.10)";
const BAND_RED = "rgba(224,86,63,0.10)";

export function exceptionFlag(p: HistoryPoint, model: string): boolean {
  return model === "FHS" ? p.exception_fhs : p.exception_hs;
}

// trailing count of exceptions over the last `window` observations, per date
export function rollingExceptionCounts(
  points: HistoryPoint[],
  model: string,
  window = ROLLING_WINDOW,
): number[] {
  const counts: number[] = [];
  let running = 0;
  for (let i = 0; i < points.length; i++) {
    running += exceptionFlag(points[i], model) ? 1 : 0;
    if (i >= window) running -= exceptionFlag(points[i - window], model) ? 1 : 0;
    counts.push(running);
  }
  return counts;
}

// two linked panels sharing one zoom: P&L vs -VaR on top, the rolling
// exception count over the traffic-light bands below. Callers over-fetch by
// ROLLING_WINDOW and pass the view size as `display`: the count at each shown
// date then covers a full trailing window, and dates whose lookback predates
// the fetched history render as a gap rather than a low-biased number.
export function linkedBacktestOption(
  points: HistoryPoint[],
  model: string,
  display = points.length,
): EChartsOption {
  const fullCounts = rollingExceptionCounts(points, model).map((c, i) =>
    i >= ROLLING_WINDOW - 1 ? c : null,
  );
  const shown = points.slice(-display);
  const counts = fullCounts.slice(-display);
  const dates = shown.map((p) => p.date);
  const varKey = model === "FHS" ? ("var_fhs" as const) : ("var_hs" as const);
  const lineColor = model === "FHS" ? VAR_FHS_COLOR : VAR_HS_COLOR;
  const maxCount = Math.max(RED_FROM + 2, ...counts.filter((c): c is number => c != null));
  const exceptions = shown.filter((p) => exceptionFlag(p, model));

  return {
    animation: false,
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    tooltip: { trigger: "axis" },
    legend: { top: 0, itemWidth: 14 },
    grid: [
      { left: 70, right: 16, top: 28, height: "48%" },
      { left: 70, right: 16, bottom: 56, height: "22%" },
    ],
    xAxis: [
      { type: "category", data: dates, gridIndex: 0, axisLabel: { show: false } },
      { type: "category", data: dates, gridIndex: 1 },
    ],
    yAxis: [
      {
        type: "value",
        gridIndex: 0,
        axisLabel: { formatter: (v: number) => fmtMoney(v) },
      },
      {
        type: "value",
        gridIndex: 1,
        max: maxCount,
        minInterval: 1,
        name: `exceptions / ${ROLLING_WINDOW}d`,
        nameTextStyle: { fontSize: 10 },
      },
    ],
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1] },
      { type: "slider", xAxisIndex: [0, 1], bottom: 8, height: 20 },
    ],
    series: [
      {
        name: "P&L",
        type: "bar",
        xAxisIndex: 0,
        yAxisIndex: 0,
        color: PNL_POS,
        data: shown.map((p) => ({
          value: p.pnl,
          itemStyle: { color: (p.pnl ?? 0) >= 0 ? PNL_POS : PNL_NEG },
        })),
        barMaxWidth: 5,
      },
      {
        name: `-VaR (${model})`,
        type: "line",
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: shown.map((p) => (p[varKey] == null ? null : -(p[varKey] as number))),
        showSymbol: false,
        lineStyle: { color: lineColor, width: 1.5 },
        itemStyle: { color: lineColor },
      },
      {
        name: "Exception",
        type: "scatter",
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: exceptions.map((p) => [p.date, p.pnl]),
        symbolSize: 8,
        itemStyle: { color: EXCEPTION_COLOR },
        label: {
          show: true,
          position: "bottom",
          fontSize: 9,
          formatter: (p) => String((p as unknown as { value: [string, number] }).value[0]),
        },
        labelLayout: { hideOverlap: true }, // clustered exceptions must not smear
        z: 5,
      },
      {
        name: `rolling ${ROLLING_WINDOW}d exceptions (${model})`,
        type: "line",
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: counts,
        showSymbol: false,
        step: "end",
        lineStyle: { color: lineColor, width: 1.5 },
        itemStyle: { color: lineColor },
        markArea: {
          silent: true,
          // integer counts on a continuous axis: half-integer edges put each
          // count strictly inside its zone (amber begins AT 5, red AT 10)
          data: [
            [{ yAxis: 0, itemStyle: { color: BAND_GREEN } }, { yAxis: AMBER_FROM - 0.5 }],
            [
              { yAxis: AMBER_FROM - 0.5, itemStyle: { color: BAND_AMBER } },
              { yAxis: RED_FROM - 0.5 },
            ],
            [{ yAxis: RED_FROM - 0.5, itemStyle: { color: BAND_RED } }, { yAxis: maxCount }],
          ],
        },
      },
    ],
  };
}

// HPL vs RTPL on a 45-degree reference: a perfect attribution sits on the line
export function plaScatterOption(points: PlaPoint[]): EChartsOption {
  const vals = points.flatMap((p) => [p.hpl, p.rtpl]);
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  return {
    animation: false,
    grid: { left: 80, right: 24, top: 16, bottom: 40 },
    tooltip: {
      trigger: "item",
      formatter: (p) => {
        const { value } = p as unknown as { value: [number, number, string] };
        return `${value[2]}<br/>HPL ${fmtMoney(value[0])} · RTPL ${fmtMoney(value[1])}`;
      },
    },
    xAxis: {
      type: "value",
      name: "HPL",
      axisLabel: { formatter: (v: number) => fmtMoney(v) },
    },
    yAxis: {
      type: "value",
      name: "RTPL",
      axisLabel: { formatter: (v: number) => fmtMoney(v) },
    },
    series: [
      {
        type: "line",
        data: [
          [lo, lo],
          [hi, hi],
        ],
        showSymbol: false,
        lineStyle: { color: "#8A97A5", width: 1, type: "dashed" },
        tooltip: { show: false },
      },
      {
        type: "scatter",
        data: points.map((p) => [p.hpl, p.rtpl, p.date]),
        symbolSize: 5,
        itemStyle: { color: VAR_HS_COLOR, opacity: 0.55 },
      },
    ],
  };
}
