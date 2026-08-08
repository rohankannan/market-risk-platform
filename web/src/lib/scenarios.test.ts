import type { ScenarioResult, ScenarioSpec } from "../api/types";
import {
  compareBarsOption,
  dominantMove,
  scenarioWaterfallOption,
  waterfallRows,
} from "./scenarios";

const SPEC = (shocks: ScenarioSpec["shocks"]): ScenarioSpec => ({
  scenario_code: "X",
  scenario_name: "X",
  scenario_type: "HYPOTHETICAL",
  window_start: null,
  window_end: null,
  description: null,
  shocks,
});

test("dominantMove formats the largest shock by its convention", () => {
  expect(
    dominantMove(
      SPEC([
        { factor_code: "IR.UST.10Y", shock_type: "ABSOLUTE_BP", shock_value: 75 },
        { factor_code: "IR.UST.2Y", shock_type: "ABSOLUTE_BP", shock_value: 100 },
      ]),
    ),
  ).toBe("IR.UST.2Y +100bp");
  expect(
    dominantMove(
      SPEC([{ factor_code: "VOL.SPX.IV30", shock_type: "ABSOLUTE", shock_value: 10 }]),
    ),
  ).toBe("VOL.SPX.IV30 +10.0pt");
  expect(dominantMove(SPEC([]))).toBeNull(); // replays: window shown instead
  expect(dominantMove(undefined)).toBeNull(); // catalog/results skew
});

test("RELATIVE shocks display the arithmetic move, not the stored log return", () => {
  // the catalog stores ln(0.8) and ln(0.9); stress.py applies exp(r)-1
  expect(
    dominantMove(SPEC([{ factor_code: "EQ.SPY", shock_type: "RELATIVE", shock_value: -0.223 }])),
  ).toBe("EQ.SPY -20%");
  expect(
    dominantMove(
      SPEC([{ factor_code: "FX.EURUSD", shock_type: "RELATIVE", shock_value: -0.105 }]),
    ),
  ).toBe("FX.EURUSD -10%");
});

test("dominantMove ranks inside the modal shock class, never across conventions", () => {
  // EQUITY_DOWN_20's shape: seven equity log returns plus one vol add-on. Raw
  // magnitude would crown the 20 vol points over 0.223, naming the wrong move.
  const equityDown = SPEC([
    ...["EQ.SPY", "EQ.AAPL", "EQ.MSFT", "EQ.NVDA", "EQ.JPM", "EQ.XOM", "EQ.JNJ"].map(
      (factor_code) => ({ factor_code, shock_type: "RELATIVE", shock_value: -0.223 }),
    ),
    { factor_code: "VOL.SPX.IV30", shock_type: "ABSOLUTE", shock_value: 20 },
  ] as ScenarioSpec["shocks"]);
  expect(dominantMove(equityDown)).toBe("EQ.SPY -20%");
});

const RESULT: ScenarioResult = {
  scenario_code: "GFC_2008",
  scenario_name: "Gfc 2008",
  scenario_type: "HISTORICAL_REPLAY",
  window_start: "2008-09-12",
  window_end: "2008-10-10",
  description: null,
  impacts: { FIRM: -11.0, FX: -8.0, EQUITY: -7.0, RATES: 4.0 },
  firm_impact: -11.0,
  worst_desk: "FX",
};

test("scenario waterfall bridges signed desk impacts to the firm total", () => {
  const opt = scenarioWaterfallOption(RESULT);
  const base = (opt.series as { data: unknown[] }[])[0].data as number[];
  const step = (opt.series as { data: unknown[] }[])[1].data as { value: number }[];
  // worst first: FX -8, EQUITY -7, RATES +4, FIRM -11. Same-signed pairs only:
  // negative-territory bars carry a negative base and a negative step.
  expect(base[0]).toBe(0);
  expect(step[0].value).toBe(-8); // 0 down to -8
  expect(base[1]).toBe(-8);
  expect(step[1].value).toBe(-7); // -8 down to -15
  expect(base[2]).toBe(-11);
  expect(step[2].value).toBe(-4); // RATES gains: the bar occupies [-15, -11]
  expect(base[3]).toBe(0);
  expect(step[3].value).toBe(-11); // firm total from zero
});

test("a column crossing zero splits into step and spill halves", () => {
  const opt = scenarioWaterfallOption({
    ...RESULT,
    impacts: { FIRM: 3.0, FX: -5.0, RATES: 8.0 },
    firm_impact: 3.0,
  });
  // FX first (worst): 0 -> -5; RATES crosses: -5 -> +3
  const base = (opt.series as { data: unknown[] }[])[0].data as number[];
  const step = (opt.series as { data: unknown[] }[])[1].data as { value: number }[];
  const spill = (opt.series as { data: unknown[] }[])[2].data as { value: number }[];
  expect(base[1]).toBe(0);
  expect(step[1].value).toBe(3); // the half above zero
  expect(spill[1].value).toBe(-5); // the half below zero
});

test("compare bars keep a canonical desk order and overlay the firm diamond", () => {
  const opt = compareBarsOption([RESULT]);
  const series = opt.series as { name: string; type: string }[];
  // desk order follows the identity palette, not backend dict order
  expect(series.map((s) => s.name)).toEqual(["RATES", "FX", "EQUITY", "FIRM"]);
  expect(series.at(-1)!.type).toBe("scatter");
});

test("waterfallRows is the shared order for the chart and its data table", () => {
  expect(waterfallRows(RESULT)).toEqual([
    ["FX", -8],
    ["EQUITY", -7],
    ["RATES", 4],
    ["FIRM", -11],
  ]);
  // a result without a firm total falls back to the sum of its desks
  expect(waterfallRows({ ...RESULT, firm_impact: null }).at(-1)).toEqual(["FIRM", -11]);
});
