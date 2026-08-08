import type { HistoryPoint } from "../api/types";
import { PNL_POS, pnlVsVarOption, UTIL_BAR, utilLevel } from "./overview";

test("utilLevel piecewise boundaries", () => {
  expect(utilLevel(0.58)).toBe("ok");
  expect(utilLevel(0.7)).toBe("warn");
  expect(utilLevel(0.9)).toBe("hot");
  expect(utilLevel(1.0)).toBe("hot"); // at the limit without a BREACH status
  expect(utilLevel(1.01)).toBe("breach");
});

test("the API's BREACH status overrides a 4dp-rounded utilization", () => {
  expect(utilLevel(1.0, "BREACH")).toBe("breach"); // hairline breach rounds to 1.0000
  expect(utilLevel(1.0, "WARN")).toBe("hot"); // sub-breach statuses never escalate
  expect(utilLevel(0.58, "OK")).toBe("ok");
});

test("tile bar earns red only past the limit", () => {
  expect(UTIL_BAR.breach).toBe("var(--zone-red)");
  expect(UTIL_BAR.hot).toBe("var(--zone-amber)");
  expect(UTIL_BAR.ok).toBe("var(--zone-green)");
});

const P = (d: string, pnl: number | null, hs: number | null, exc = false): HistoryPoint => ({
  date: d,
  var_hs: hs,
  var_fhs: hs == null ? null : hs * 1.1,
  es_975: null,
  pnl,
  exception_hs: exc,
  exception_fhs: false,
});

test("pnlVsVarOption negates VaR and marks exception days only", () => {
  const opt = pnlVsVarOption([
    P("2026-08-04", 1000, 5000),
    P("2026-08-05", -6000, 5200, true),
    P("2026-08-06", 200, 5100),
  ]);
  const series = opt.series as { name: string; data: unknown[] }[];
  const hs = series.find((s) => s.name === "-VaR (HS)")!;
  expect(hs.data).toEqual([-5000, -5200, -5100]);
  const exc = series.find((s) => s.name === "Exception")!;
  expect(exc.data).toEqual([["2026-08-05", -6000]]); // the breach day, at its P&L
  const pnl = series.find((s) => s.name === "P&L")! as { color?: string; data: unknown[] };
  expect(pnl.data).toHaveLength(3);
  expect(pnl.color).toBe(PNL_POS); // legend swatch, not the ECharts default blue
});
