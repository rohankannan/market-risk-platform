// single import point for echarts-for-react: tests alias this module's
// dependency to a stub (jsdom has no canvas), pages import EChart only
import * as echarts from "echarts";
import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";

// axis/legend/tooltip chrome for the dark terminal ground - series colors
// stay explicit per chart
echarts.registerTheme("riskdesk", {
  textStyle: { color: "#8a857a", fontFamily: "IBM Plex Mono, ui-monospace, monospace" },
  legend: { textStyle: { color: "#8a857a" } },
  tooltip: {
    backgroundColor: "#0f0e0c",
    borderColor: "#2a2822",
    textStyle: { color: "#e8e4da" },
  },
  categoryAxis: {
    axisLine: { lineStyle: { color: "#2a2822" } },
    axisTick: { lineStyle: { color: "#2a2822" } },
    axisLabel: { color: "#8a857a" },
  },
  valueAxis: {
    axisLine: { lineStyle: { color: "#2a2822" } },
    axisLabel: { color: "#8a857a" },
    splitLine: { lineStyle: { color: "#1c1a16" } },
    nameTextStyle: { color: "#8a857a" },
  },
  dataZoom: {
    borderColor: "#2a2822",
    backgroundColor: "#0f0e0c",
    fillerColor: "rgba(207,185,145,0.12)",
    handleStyle: { color: "#cfb991" },
    textStyle: { color: "#8a857a" },
  },
});

export function EChart({
  option,
  height = 300,
  onEvents,
}: {
  option: EChartsOption;
  height?: number;
  onEvents?: Record<string, (params: unknown) => void>;
}) {
  return (
    <ReactECharts theme="riskdesk" option={option} style={{ height }} notMerge onEvents={onEvents} />
  );
}
