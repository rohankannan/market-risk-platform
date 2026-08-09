import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";

import App from "../App";
import { API_URL } from "../api/client";
import { server } from "../mocks/server";
import { fmtMoney } from "../lib/format";

interface Adjustment {
  ticker: string;
  scale: number;
}

const OFFICIAL = 1_137_118.3;

const POSITION = {
  ticker: "UST_5Y",
  desk_code: "RATES",
  factor_class: "IR",
  quantity: -60_000_000,
  scale: 1.0,
  standalone_var: 295_000.0,
  component_es: -260_000.0,
  marginal_var: -304_000.0,
};

// mirrors the real contract: zeroed rows drop from positions, out-of-range
// scales 422 with a detail, deltas ride per desk
interface Shock {
  factor_code: string;
  shock_type: string;
  value: number;
}

const PRESET_SHOCKS: Shock[] = [
  { factor_code: "IR.UST.10Y", shock_type: "ABSOLUTE_BP", value: 258.0 },
  { factor_code: "EQ.SPY", shock_type: "RELATIVE", value: -0.19532847 },
];

function presetHandlers() {
  return [
    // the recorded catalog fixture predates these scenarios, so the preset
    // list is served explicitly here
    http.get(`${API_URL}/api/v1/scenarios`, () =>
      HttpResponse.json({
        scenarios: [
          {
            scenario_code: "RATES_2022",
            scenario_name: "Rates 2022",
            scenario_type: "HISTORICAL_REPLAY",
            window_start: "2022-01-03",
            window_end: "2022-10-31",
            description: null,
            shocks: [],
          },
        ],
      }),
    ),
    http.get(`${API_URL}/api/v1/scenarios/:code/shocks`, ({ params }) =>
      HttpResponse.json({
        scenario_code: params.code,
        scenario_type: "HISTORICAL_REPLAY",
        shocks: PRESET_SHOCKS,
      }),
    ),
  ];
}

function whatifHandler(bodies: Adjustment[][], shockBodies: Shock[][] = []) {
  return http.post(`${API_URL}/api/v1/whatif`, async ({ request }) => {
    const body = (await request.json()) as { adjustments: Adjustment[]; shocks?: Shock[] };
    bodies.push(body.adjustments);
    shockBodies.push(body.shocks ?? []);
    const bad = body.adjustments.filter((a) => Math.abs(a.scale) > 10);
    if (bad.length) {
      return HttpResponse.json(
        { detail: `scales outside +-10: ${bad.map((b) => b.ticker).join(", ")}` },
        { status: 422 },
      );
    }
    const zeroed = body.adjustments.filter((a) => a.scale === 0).map((a) => a.ticker);
    const edited = body.adjustments.length > 0;
    const shocked = (body.shocks ?? []).length > 0;
    return HttpResponse.json({
      as_of: "2026-08-06",
      run_id: 7,
      hypothetical: true,
      desks: [
        {
          desk_code: "FIRM",
          is_aggregate: true,
          var_hs_1d: edited ? 990_000.0 : OFFICIAL,
          es_975_1d: 1_150_000.0,
          shock_pnl: shocked ? -21_828_197.27 : null,
          official_var_hs_1d: OFFICIAL,
          var_delta: edited ? -147_118.3 : 0.0,
        },
        {
          desk_code: "RATES",
          is_aggregate: false,
          var_hs_1d: 868_455.07,
          es_975_1d: 920_191.41,
          shock_pnl: shocked ? -17_900_000.0 : null,
          official_var_hs_1d: 868_455.07,
          var_delta: 0.0,
        },
      ],
      positions: zeroed.includes("UST_5Y") ? [] : [POSITION],
      zeroed,
      shocked_factors: (body.shocks ?? []).map((s) => s.factor_code).sort(),
    });
  });
}

function renderAt(path: string) {
  window.history.pushState({}, "", path);
  return render(<App />);
}

test("what-if page marks its numbers hypothetical and starts at the official book", async () => {
  const bodies: Adjustment[][] = [];
  server.use(whatifHandler(bodies));
  renderAt("/whatif");
  // await the page's own hint, not the nav link that shares the WHAT-IF text
  expect(await screen.findByText(/HYPOTHETICAL — scaled book/)).toBeInTheDocument();
  expect(screen.getAllByText("WHAT-IF").length).toBeGreaterThan(1); // nav + badge
  expect(await screen.findByTitle("$1,137,118.30")).toBeInTheDocument();
  expect(bodies[0]).toEqual([]);
});

test("zeroing keeps the row editable and the restore button brings it back", async () => {
  const bodies: Adjustment[][] = [];
  server.use(whatifHandler(bodies));
  renderAt("/whatif");
  const input = await screen.findByLabelText("Scale UST_5Y");
  fireEvent.change(input, { target: { value: "0" } });
  await waitFor(() => expect(bodies.at(-1)).toEqual([{ ticker: "UST_5Y", scale: 0 }]), {
    timeout: 3000,
  });
  expect(await screen.findByText(/ZEROED: UST_5Y/)).toBeInTheDocument();
  expect(await screen.findByText(fmtMoney(-147_118.3))).toBeInTheDocument(); // delta chip
  // the zeroed row is still on the page with its restore action; restoring
  // to scale 1 answers from the query cache (same key as the initial book)
  fireEvent.click(screen.getByRole("button", { name: "Restore UST_5Y" }));
  await waitFor(() => expect(screen.queryByText(/ZEROED: UST_5Y/)).toBeNull(), {
    timeout: 3000,
  });
  expect(await screen.findByText(fmtMoney(POSITION.standalone_var))).toBeInTheDocument();
});

test("a preset loads its shocks, posts them unedited, and reports shock P&L", async () => {
  const bodies: Adjustment[][] = [];
  const shockBodies: Shock[][] = [];
  server.use(whatifHandler(bodies, shockBodies), ...presetHandlers());
  renderAt("/whatif");
  const select = await screen.findByLabelText("Scenario preset");
  fireEvent.change(select, { target: { value: "RATES_2022" } });

  // the preset's moves land in the editor, formatted per convention
  const rates = await screen.findByLabelText("Shock IR.UST.10Y");
  expect(rates).toHaveValue("258.0");
  expect(screen.getByLabelText("Shock EQ.SPY")).toHaveValue("-17.74"); // log -> percent

  // posted back untouched at FULL precision - the identity that makes a
  // preset reproduce the batch's scenario P&L to the cent
  await waitFor(() => expect(shockBodies.at(-1)).toEqual(PRESET_SHOCKS), { timeout: 3000 });
  expect(await screen.findByText("−$21.83M")).toBeInTheDocument();
  expect(screen.getAllByText(/SHOCK P&L/).length).toBeGreaterThan(1); // firm + desk
});

test("editing one factor keeps the others at the preset's precision", async () => {
  const bodies: Adjustment[][] = [];
  const shockBodies: Shock[][] = [];
  server.use(whatifHandler(bodies, shockBodies), ...presetHandlers());
  renderAt("/whatif");
  fireEvent.change(await screen.findByLabelText("Scenario preset"), {
    target: { value: "RATES_2022" },
  });
  const rates = await screen.findByLabelText("Shock IR.UST.10Y");
  fireEvent.change(rates, { target: { value: "400" } });
  await waitFor(
    () =>
      expect(shockBodies.at(-1)).toEqual([
        { factor_code: "IR.UST.10Y", shock_type: "ABSOLUTE_BP", value: 400 },
        PRESET_SHOCKS[1], // untouched, still full precision
      ]),
    { timeout: 3000 },
  );
  expect(await screen.findByText(/EDITED FROM RATES_2022/)).toBeInTheDocument();
});

test("a rejected scale surfaces the detail without destroying the book or edits", async () => {
  const bodies: Adjustment[][] = [];
  server.use(whatifHandler(bodies));
  renderAt("/whatif");
  const input = await screen.findByLabelText("Scale UST_5Y");
  fireEvent.change(input, { target: { value: "99" } });
  expect(await screen.findByText(/NOT REVALUED — scales outside/)).toBeInTheDocument();
  // the page did not crash into the boundary; the edit and table survive
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByLabelText("Scale UST_5Y")).toHaveValue("99");
  // fixing the input recovers
  fireEvent.change(screen.getByLabelText("Scale UST_5Y"), { target: { value: "2" } });
  await waitFor(() => expect(bodies.at(-1)).toEqual([{ ticker: "UST_5Y", scale: 2 }]), {
    timeout: 3000,
  });
  await waitFor(() => expect(screen.queryByText(/NOT REVALUED/)).toBeNull());
});
