// Pure data transforms for the desk drill-down - testable without React.
import type { EChartsOption } from "echarts";

import type { DeskDecomposition, DeskExposure } from "../api/types";
import { fmtMoney } from "./format";
import { VAR_HS_COLOR } from "./overview";

// desk identity colors, canvas literals (tokens.css --desk-*)
export const DESK_HEX: Record<string, string> = {
  RATES: "#5B8DEF",
  FX: "#C08BF0",
  EQUITY: "#3EBFA5",
};
const DIVERSIFICATION_GREY = "#8A97A5";

export const FACTOR_CLASS_LABEL: Record<string, string> = {
  EQ: "Equity",
  FX: "FX",
  IR: "Rates",
  VOL: "Volatility",
};

// classic stacked-bar waterfall: transparent base + visible step per column.
// The identity the chart renders: sum(buckets) + diversification == total.
export function waterfallOption(d: DeskDecomposition): EChartsOption | null {
  if (!d.buckets.length || d.diversification == null || d.var_hs_1d == null) return null;
  const color = DESK_HEX[d.desk_code] ?? VAR_HS_COLOR;
  const labels = [
    ...d.buckets.map((b) => FACTOR_CLASS_LABEL[b.factor_class] ?? b.factor_class),
    "Diversification",
    "Desk VaR",
  ];
  const base: number[] = [];
  const step: number[] = [];
  const colors: string[] = [];
  let running = 0;
  for (const b of d.buckets) {
    base.push(running);
    step.push(b.standalone_var);
    colors.push(color);
    running += b.standalone_var;
  }
  // diversification bridges the standalone sum to the desk VaR - down in the
  // usual case, up if the empirical quantile ever goes superadditive (the API
  // does not fix the sign). Both stacked values stay non-negative: ECharts'
  // samesign stacking would detach a negative step below the axis. The label
  // and tooltip restore the sign from d.diversification directly.
  base.push(Math.min(running, running + d.diversification));
  step.push(Math.abs(d.diversification));
  colors.push(DIVERSIFICATION_GREY);
  base.push(0);
  step.push(d.var_hs_1d);
  colors.push(VAR_HS_COLOR);

  return {
    animation: false,
    grid: { left: 70, right: 16, top: 24, bottom: 24 },
    xAxis: { type: "category", data: labels },
    yAxis: { type: "value", axisLabel: { formatter: (v: number) => fmtMoney(v) } },
    tooltip: {
      trigger: "axis",
      // the visible step is what the reader cares about; sign restored for the
      // diversification column
      formatter: (params: unknown) => {
        const p = (params as { dataIndex: number; seriesIndex: number }[]).find(
          (x) => x.seriesIndex === 1,
        );
        if (!p) return "";
        const i = p.dataIndex;
        const v = i === d.buckets.length ? d.diversification! : step[i];
        return `${labels[i]}: ${fmtMoney(v)}`;
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
        data: step.map((v, i) => ({ value: v, itemStyle: { color: colors[i] } })),
        label: {
          show: true,
          position: "top",
          formatter: (p: { dataIndex: number }) =>
            fmtMoney(p.dataIndex === d.buckets.length ? d.diversification! : step[p.dataIndex]),
        },
        barMaxWidth: 56,
      },
    ],
  };
}

export interface ExposureGroup {
  title: string; // units live here, per the spec
  rows: { label: string; value: number }[];
}

const TENOR_LABEL = (t: number | null | undefined): string =>
  t == null ? "?" : t < 1 ? `${Math.round(t * 12)}M` : `${t}Y`;

// group the desk's exposure rows into the bar lists the page renders;
// empty groups are dropped (a rates desk has no vega, FX has no KRD)
export function exposureGroups(exposures: DeskExposure[], top = 8): ExposureGroup[] {
  const krd = exposures
    .filter((e) => e.measure === "KRD_DV01")
    .sort((a, b) => (a.tenor_years ?? 0) - (b.tenor_years ?? 0))
    .map((e) => ({ label: TENOR_LABEL(e.tenor_years), value: e.value }));
  const vega = exposures
    .filter((e) => e.measure === "VEGA")
    .map((e) => ({ label: e.factor_code, value: e.value }));
  const delta = exposures.filter((e) => e.measure === "DELTA_USD");
  const eqDelta = delta
    .filter((e) => e.factor_code.startsWith("EQ."))
    .sort((a, b) => Math.abs(b.value) - Math.abs(a.value))
    .slice(0, top)
    .map((e) => ({ label: e.factor_code.replace("EQ.", ""), value: e.value / 100 }));
  const fxDelta = delta
    .filter((e) => e.factor_code.startsWith("FX."))
    .sort((a, b) => Math.abs(b.value) - Math.abs(a.value))
    .map((e) => ({ label: e.factor_code.replace("FX.", ""), value: e.value / 100 }));

  const groups: ExposureGroup[] = [
    { title: "Key-rate DV01 ($ per 1bp)", rows: krd },
    { title: "Vega ($ per vol pt)", rows: vega },
    { title: "Equity delta ($ per 1%)", rows: eqDelta },
    { title: "FX delta ($ per 1%)", rows: fxDelta },
  ];
  return groups.filter((g) => g.rows.length > 0);
}

export function exposureBarsOption(group: ExposureGroup, color: string): EChartsOption {
  return {
    animation: false,
    grid: { left: 70, right: 40, top: 8, bottom: 24 },
    xAxis: {
      type: "category",
      data: group.rows.map((r) => r.label),
      axisLabel: { fontSize: 11 },
    },
    yAxis: { type: "value", axisLabel: { formatter: (v: number) => fmtMoney(v) } },
    tooltip: { trigger: "axis", valueFormatter: (v) => fmtMoney(Number(v)) },
    series: [
      {
        type: "bar",
        data: group.rows.map((r) => r.value),
        itemStyle: { color },
        barMaxWidth: 36,
        label: {
          show: group.rows.length <= 6,
          position: "top",
          fontSize: 10,
          formatter: (p) => fmtMoney(Number((p as { value: unknown }).value)),
        },
      },
    ],
  };
}
