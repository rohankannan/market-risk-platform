// Pure data transforms for the scenarios page - testable without React.
import type { EChartsOption } from "echarts";

import type { ScenarioResult, ScenarioSpec } from "../api/types";
import { DESK_HEX } from "./desk";
import { fmtMoney } from "./format";
import { VAR_HS_COLOR } from "./overview";

export const MAX_COMPARE = 3;

// The catalog's dominant move, formatted by its convention. RELATIVE shocks
// are log returns on LOG factors (stress.py applies exp(r)-1), so they display
// as the arithmetic move the catalog was struck for. Magnitudes only compare
// within a convention - bp, vol points and log returns are incommensurate - so
// the ranking runs inside the scenario's modal shock class, the one it is
// named for, rather than across all shocks.
export function dominantMove(spec: ScenarioSpec | undefined): string | null {
  if (!spec?.shocks.length) return null;
  const counts = new Map<string, number>();
  for (const s of spec.shocks) counts.set(s.shock_type, (counts.get(s.shock_type) ?? 0) + 1);
  const modal = [...counts.entries()].sort((a, b) => b[1] - a[1])[0][0];
  const top = spec.shocks
    .filter((s) => s.shock_type === modal)
    .sort((a, b) => Math.abs(b.shock_value) - Math.abs(a.shock_value))[0];
  const v = top.shock_value;
  if (top.shock_type === "RELATIVE") {
    const pct = Math.expm1(v) * 100;
    return `${top.factor_code} ${pct > 0 ? "+" : ""}${pct.toFixed(0)}%`;
  }
  if (top.shock_type === "ABSOLUTE_BP") {
    return `${top.factor_code} ${v > 0 ? "+" : ""}${Math.round(v)}bp`;
  }
  return `${top.factor_code} ${v > 0 ? "+" : ""}${v.toFixed(1)}pt`;
}

// grouped bars per desk with the firm total as a diamond overlay, worst first
export function compareBarsOption(results: ScenarioResult[]): EChartsOption {
  const canon = Object.keys(DESK_HEX);
  const rank = (c: string) => (canon.indexOf(c) + 1 || canon.length + 1);
  const deskCodes = Array.from(new Set(results.flatMap((r) => Object.keys(r.impacts))))
    .filter((c) => c !== "FIRM")
    .sort((a, b) => rank(a) - rank(b));
  return {
    animation: false,
    grid: { left: 80, right: 16, top: 28, bottom: 24 },
    tooltip: {
      trigger: "axis",
      // a desk absent from a scenario arrives as NaN, not null - never $0
      valueFormatter: (v) => (Number.isFinite(Number(v)) ? fmtMoney(Number(v)) : "-"),
    },
    legend: { top: 0, itemWidth: 14 },
    xAxis: { type: "category", data: results.map((r) => r.scenario_code) },
    yAxis: { type: "value", axisLabel: { formatter: (v: number) => fmtMoney(v) } },
    series: [
      ...deskCodes.map((code) => ({
        name: code,
        type: "bar" as const,
        data: results.map((r) => r.impacts[code] ?? null),
        itemStyle: { color: DESK_HEX[code] ?? "#8A97A5" },
        barMaxWidth: 28,
      })),
      {
        name: "FIRM",
        type: "scatter" as const,
        symbol: "diamond",
        symbolSize: 11,
        data: results.map((r) => r.firm_impact),
        itemStyle: { color: VAR_HS_COLOR },
        z: 5,
      },
    ],
  };
}

// the waterfall's column order, shared by the chart and its data table:
// desk contributions worst first, then the firm total
export function waterfallRows(result: ScenarioResult): [string, number][] {
  const desks = Object.entries(result.impacts)
    .filter(([code]) => code !== "FIRM")
    .sort((a, b) => a[1] - b[1]) as [string, number][];
  const firm = result.firm_impact ?? desks.reduce((s, [, v]) => s + v, 0);
  return [...desks, ["FIRM", firm]];
}

// signed waterfall: desk contributions bridge from zero to the firm impact.
// ECharts stacks same-signed values only, so each column's (base, step) pair
// must share a sign; a column that crosses zero splits its remainder into the
// spill series (usually all zeros).
export function scenarioWaterfallOption(result: ScenarioResult): EChartsOption {
  const rows = waterfallRows(result);
  const desks = rows.slice(0, -1);
  const firm = rows[rows.length - 1][1];
  const labels = rows.map(([code]) => code);
  const base: number[] = [];
  const step: number[] = [];
  const spill: number[] = [];
  const colors: string[] = [];
  let running = 0;
  for (const [code, v] of desks) {
    const from = running;
    const to = running + v;
    const lo = Math.min(from, to);
    const hi = Math.max(from, to);
    if (hi <= 0) {
      base.push(hi);
      step.push(lo - hi);
      spill.push(0);
    } else if (lo >= 0) {
      base.push(lo);
      step.push(hi - lo);
      spill.push(0);
    } else {
      base.push(0);
      step.push(hi);
      spill.push(lo);
    }
    colors.push(DESK_HEX[code] ?? "#8A97A5");
    running = to;
  }
  base.push(0);
  step.push(firm);
  spill.push(0);
  colors.push(VAR_HS_COLOR);

  return {
    animation: false,
    grid: { left: 80, right: 16, top: 24, bottom: 24 },
    xAxis: { type: "category", data: labels },
    yAxis: { type: "value", axisLabel: { formatter: (v: number) => fmtMoney(v) } },
    tooltip: {
      trigger: "axis",
      formatter: (params: unknown) => {
        const p = (params as { dataIndex: number; seriesIndex: number }[]).find(
          (x) => x.seriesIndex === 1,
        );
        if (!p) return "";
        return `${labels[p.dataIndex]}: ${fmtMoney(rows[p.dataIndex][1])}`;
      },
    },
    series: [
      {
        type: "bar",
        stack: "wf",
        data: base,
        itemStyle: { color: "transparent" },
        emphasis: { itemStyle: { color: "transparent" } },
        tooltip: { show: false },
        barMaxWidth: 56,
      },
      {
        type: "bar",
        stack: "wf",
        data: step.map((v, i) => ({
          value: v,
          itemStyle: { color: colors[i] },
          label: { position: v < 0 ? ("bottom" as const) : ("top" as const) },
        })),
        label: {
          show: true,
          formatter: (p: { dataIndex: number }) => fmtMoney(rows[p.dataIndex][1]),
        },
        barMaxWidth: 56,
      },
      {
        type: "bar",
        stack: "wf",
        data: spill.map((v, i) => ({ value: v, itemStyle: { color: colors[i] } })),
        tooltip: { show: false },
        barMaxWidth: 56,
      },
    ],
  };
}
