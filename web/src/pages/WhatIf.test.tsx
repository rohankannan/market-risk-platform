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
function whatifHandler(bodies: Adjustment[][]) {
  return http.post(`${API_URL}/api/v1/whatif`, async ({ request }) => {
    const body = (await request.json()) as { adjustments: Adjustment[] };
    bodies.push(body.adjustments);
    const bad = body.adjustments.filter((a) => Math.abs(a.scale) > 10);
    if (bad.length) {
      return HttpResponse.json(
        { detail: `scales outside +-10: ${bad.map((b) => b.ticker).join(", ")}` },
        { status: 422 },
      );
    }
    const zeroed = body.adjustments.filter((a) => a.scale === 0).map((a) => a.ticker);
    const edited = body.adjustments.length > 0;
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
          official_var_hs_1d: OFFICIAL,
          var_delta: edited ? -147_118.3 : 0.0,
        },
        {
          desk_code: "RATES",
          is_aggregate: false,
          var_hs_1d: 868_455.07,
          es_975_1d: 920_191.41,
          official_var_hs_1d: 868_455.07,
          var_delta: 0.0,
        },
      ],
      positions: zeroed.includes("UST_5Y") ? [] : [POSITION],
      zeroed,
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
