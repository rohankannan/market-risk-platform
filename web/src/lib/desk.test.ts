import type { DeskDecomposition } from "../api/types";
import rates from "../mocks/fixtures/desks_RATES_decomposition.json";
import { exposureGroups, waterfallOption } from "./desk";

const DECOMP: DeskDecomposition = {
  as_of: "2026-08-06",
  run_id: 1,
  desk_code: "EQUITY",
  desk_name: "Cash Equities",
  var_hs_1d: 50.0,
  buckets: [
    { factor_class: "EQ", standalone_var: 75.75 },
    { factor_class: "VOL", standalone_var: 10.1 },
  ],
  diversification: -35.85,
  exposures: [],
};

test("waterfall geometry: bases stack, diversification steps down to the total", () => {
  const opt = waterfallOption(DECOMP)!;
  const series = opt.series as { data: unknown[] }[];
  const base = series[0].data as number[];
  const step = series[1].data as { value: number }[];
  // bucket columns rise cumulatively
  expect(base[0]).toBe(0);
  expect(step[0].value).toBe(75.75);
  expect(base[1]).toBe(75.75);
  expect(step[1].value).toBe(10.1);
  // diversification column: drawn from the desk VaR up to the standalone sum
  expect(base[2]).toBeCloseTo(50.0, 10);
  expect(step[2].value).toBeCloseTo(35.85, 10);
  // total column equals the desk VaR - the identity the chart renders
  expect(base[3]).toBe(0);
  expect(step[3].value).toBe(50.0);
});

test("waterfall reconciles the RATES fixture to the cent", () => {
  const d = rates as DeskDecomposition;
  const total = d.buckets.reduce((s, b) => s + b.standalone_var, 0) + d.diversification!;
  expect(Math.round(total * 100) / 100).toBe(d.var_hs_1d);
  expect(waterfallOption(d)).not.toBeNull();
});

test("waterfall degrades to null for runs without the position step", () => {
  expect(waterfallOption({ ...DECOMP, buckets: [], diversification: null })).toBeNull();
});

test("waterfall stays attached when diversification goes superadditive", () => {
  // empirical VaR is not subadditive: a positive diversification must step UP
  const opt = waterfallOption({ ...DECOMP, var_hs_1d: 95.0, diversification: 9.15 })!;
  const base = (opt.series as { data: unknown[] }[])[0].data as number[];
  const step = (opt.series as { data: unknown[] }[])[1].data as { value: number }[];
  expect(base[2]).toBeCloseTo(85.85, 10); // bridges from the standalone sum...
  expect(step[2].value).toBeCloseTo(9.15, 10); // ...up to the desk VaR
  expect(step[3].value).toBe(95.0);
});

test("exposure groups: tenor order, per-1% delta scaling, empty groups dropped", () => {
  const groups = exposureGroups([
    { measure: "KRD_DV01", factor_code: "IR.UST.10Y", tenor_years: 10, value: 48000 },
    { measure: "KRD_DV01", factor_code: "IR.UST.3M", tenor_years: 0.25, value: 100 },
    { measure: "DELTA_USD", factor_code: "EQ.SPY", tenor_years: null, value: 5_900_000 },
    { measure: "DELTA_USD", factor_code: "EQ.NVDA", tenor_years: null, value: -3_000_000 },
  ]);
  expect(groups.map((g) => g.title)).toEqual([
    "Key-rate DV01 ($ per 1bp)",
    "Equity delta ($ per 1%)",
  ]);
  expect(groups[0].rows.map((r) => r.label)).toEqual(["3M", "10Y"]); // tenor asc
  expect(groups[1].rows[0]).toEqual({ label: "SPY", value: 59_000 }); // $ per 1%
  expect(groups[1].rows[1].value).toBe(-30_000);
});
