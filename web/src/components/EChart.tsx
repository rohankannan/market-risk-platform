// single import point for echarts-for-react: tests alias this module's
// dependency to a stub (jsdom has no canvas), pages import EChart only
import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";

export function EChart({
  option,
  height = 300,
  onEvents,
}: {
  option: EChartsOption;
  height?: number;
  onEvents?: Record<string, (params: unknown) => void>;
}) {
  return <ReactECharts option={option} style={{ height }} notMerge onEvents={onEvents} />;
}
