import { fireEvent, render, screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";

import App from "../App";
import { API_URL } from "../api/client";
import { server } from "../mocks/server";
import { fmtMoneyFull } from "../lib/format";
import scenarios from "../mocks/fixtures/scenarios.json";
import results from "../mocks/fixtures/scenarios_results.json";

function renderAt(path: string) {
  window.history.pushState({}, "", path);
  return render(<App />);
}

test("rail lists every scenario and enforces the max-3 selection in the URL", async () => {
  renderAt("/scenarios");
  const rail = await screen.findByLabelText("Scenario selection");
  const boxes = within(rail).getAllByRole("checkbox");
  expect(boxes).toHaveLength(scenarios.scenarios.length);
  // worst 3 selected by default; the remaining boxes are disabled at the cap
  expect(boxes.filter((b) => (b as HTMLInputElement).checked)).toHaveLength(3);
  expect(boxes.filter((b) => (b as HTMLInputElement).disabled)).toHaveLength(
    scenarios.scenarios.length - 3,
  );
  // unchecking one frees a slot and lands in the URL
  fireEvent.click(boxes.find((b) => (b as HTMLInputElement).checked)!);
  const ids = new URLSearchParams(window.location.search).get("ids")!;
  expect(ids.split(",").filter(Boolean)).toHaveLength(2);
});

test("replay scenarios show their window; hypotheticals their dominant move", async () => {
  renderAt("/scenarios");
  await screen.findByText("All scenarios");
  const replay = scenarios.scenarios.find((s) => s.scenario_type === "HISTORICAL_REPLAY")!;
  expect(
    screen.getAllByText(`${replay.window_start} to ${replay.window_end}`).length,
  ).toBeGreaterThan(0);
  // dominant move ranks inside the modal shock class - see lib/scenarios
  const hypo = scenarios.scenarios.find((s) => s.shocks.length > 0)!;
  const modal = [...hypo.shocks]
    .map((s) => s.shock_type)
    .sort(
      (a, b) =>
        hypo.shocks.filter((s) => s.shock_type === b).length -
        hypo.shocks.filter((s) => s.shock_type === a).length,
    )[0];
  const top = hypo.shocks
    .filter((s) => s.shock_type === modal)
    .sort((a, b) => Math.abs(b.shock_value) - Math.abs(a.shock_value))[0];
  expect(
    screen.getAllByText(new RegExp(top.factor_code.replace(/\./g, "\\."))).length,
  ).toBeGreaterThan(0);
});

test("unchecking every scenario stays empty instead of snapping back to defaults", async () => {
  renderAt("/scenarios");
  // one click per commit: React must flush the URL update before the next
  for (let i = 0; i < 3; i++) {
    const rail = await screen.findByLabelText("Scenario selection");
    const checked = within(rail)
      .getAllByRole("checkbox")
      .filter((b) => (b as HTMLInputElement).checked);
    expect(checked).toHaveLength(3 - i);
    fireEvent.click(checked[0]);
  }
  const rail = await screen.findByLabelText("Scenario selection");
  expect(
    within(rail)
      .getAllByRole("checkbox")
      .filter((b) => (b as HTMLInputElement).checked),
  ).toHaveLength(0);
  expect(screen.getByText("Select scenarios on the left to compare.")).toBeInTheDocument();
});

test("unknown and duplicate ids never consume the compare cap", async () => {
  const real = results.results[0].scenario_code;
  renderAt(`/scenarios?ids=${real},${real},NOPE,ALSO_NOPE`);
  const rail = await screen.findByLabelText("Scenario selection");
  const boxes = within(rail).getAllByRole("checkbox");
  expect(boxes.filter((b) => (b as HTMLInputElement).checked)).toHaveLength(1);
  expect(boxes.filter((b) => (b as HTMLInputElement).disabled)).toHaveLength(0);
});

test("the compare chart carries a data table with the recorded impacts", async () => {
  renderAt("/scenarios");
  await screen.findByText("All scenarios");
  const worst = results.results[0];
  expect(
    screen.getAllByText(fmtMoneyFull(worst.firm_impact)).length,
  ).toBeGreaterThan(0);
});

test("a pin before the first scenario run reads as a quiet state, not a page failure", async () => {
  server.use(
    http.get(`${API_URL}/api/v1/scenarios/results`, () =>
      HttpResponse.json({ detail: "no scenario run on or before 2025-06-01" }, { status: 404 }),
    ),
  );
  renderAt("/scenarios?as_of=2025-06-01");
  expect(await screen.findByText(/No scenario run on or before this as-of/)).toBeInTheDocument();
  expect(screen.queryByRole("alert")).toBeNull();
});

test("clicking a scenario row drills into its waterfall via the URL", async () => {
  renderAt("/scenarios");
  await screen.findByText("All scenarios");
  const target = results.results[1];
  const buttons = screen.getAllByRole("button", { name: target.scenario_name });
  fireEvent.click(buttons[buttons.length - 1]);
  expect(new URLSearchParams(window.location.search).get("drill")).toBe(
    target.scenario_code,
  );
  expect(
    await screen.findByText(`${target.scenario_name}: desk contributions to the firm impact`),
  ).toBeInTheDocument();
});
