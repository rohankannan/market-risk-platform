import type { HistoryPoint } from "../api/types";
import { PNL_POS, pnlVsVarOption } from "./overview";

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
