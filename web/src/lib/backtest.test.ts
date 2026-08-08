import type { HistoryPoint } from "../api/types";
import { linkedBacktestOption, plaScatterOption, rollingExceptionCounts } from "./backtest";

const P = (d: string, exc: boolean): HistoryPoint => ({
  date: d,
  var_hs: 100,
  var_fhs: 110,
  es_975: null,
  pnl: exc ? -150 : 10,
  exception_hs: exc,
  exception_fhs: false,
});

test("rolling exception count slides its window", () => {
  const pts = [P("d1", true), P("d2", false), P("d3", true), P("d4", false)];
  expect(rollingExceptionCounts(pts, "HS", 2)).toEqual([1, 1, 1, 1]);
  expect(rollingExceptionCounts(pts, "HS", 3)).toEqual([1, 1, 2, 1]);
  expect(rollingExceptionCounts(pts, "FHS", 3)).toEqual([0, 0, 0, 0]); // model-scoped
});

test("linked chart shares one zoom across both panels", () => {
  const opt = linkedBacktestOption([P("d1", false), P("d2", true)], "HS");
  const zooms = opt.dataZoom as { xAxisIndex: number[] }[];
  expect(zooms).toHaveLength(2);
  for (const z of zooms) expect(z.xAxisIndex).toEqual([0, 1]); // brushing top moves bottom
  const series = opt.series as { name?: string; xAxisIndex?: number }[];
  expect(series.find((s) => s.name === "Exception")!.xAxisIndex).toBe(0);
  expect(series.find((s) => s.name === "rolling 250d exceptions (HS)")!.xAxisIndex).toBe(1);
});

test("traffic-light bands place integer counts strictly inside their zones", () => {
  const opt = linkedBacktestOption([P("d1", false)], "HS");
  const rolling = (opt.series as { name?: string; markArea?: { data: unknown[] } }[]).find(
    (s) => s.name === "rolling 250d exceptions (HS)",
  )!;
  const bands = rolling.markArea!.data as [{ yAxis: number }, { yAxis: number }][];
  // half-integer edges: a count of exactly 5 sits inside amber, 10 inside red
  expect(bands.map((b) => [b[0].yAxis, b[1].yAxis])).toEqual([
    [0, 4.5],
    [4.5, 9.5],
    [9.5, 12],
  ]);
});

test("warm-up counts mask to null and the display slices to the view window", () => {
  const points = Array.from({ length: 300 }, (_, i) =>
    P(`d${String(i).padStart(3, "0")}`, i === 260),
  );
  const opt = linkedBacktestOption(points, "HS", 40);
  const rolling = (opt.series as { name?: string; data?: unknown[] }[]).find(
    (s) => s.name === "rolling 250d exceptions (HS)",
  )!;
  const counts = rolling.data as (number | null)[];
  expect(counts).toHaveLength(40); // sliced to the view
  expect(counts.every((c) => c != null)).toBe(true); // 300 fetched > 250 lookback
  expect(counts.at(-1)).toBe(1); // the exception at index 260 is in every trailing window
  const dates = (opt.xAxis as { data: string[] }[])[0].data;
  expect(dates).toHaveLength(40);
  expect(dates[0]).toBe("d260");

  // with less history than the lookback, leading counts go quiet - never biased low
  const short = linkedBacktestOption(points.slice(0, 100), "HS", 100);
  const shortCounts = (
    (short.series as { name?: string; data?: unknown[] }[]).find(
      (s) => s.name === "rolling 250d exceptions (HS)",
    )!.data as (number | null)[]
  );
  expect(shortCounts.every((c) => c === null)).toBe(true);
});

test("pla scatter draws the 45-degree reference across the data range", () => {
  const opt = plaScatterOption([
    { date: "d1", hpl: -50, rtpl: -40 },
    { date: "d2", hpl: 30, rtpl: 25 },
  ]);
  const line = (opt.series as { data: unknown[] }[])[0];
  expect(line.data).toEqual([
    [-50, -50],
    [30, 30],
  ]);
});
