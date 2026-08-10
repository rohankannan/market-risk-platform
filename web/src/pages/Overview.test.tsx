import { fireEvent, render, screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";

import App from "../App";
import { API_URL } from "../api/client";
import { canonicalFromUrl } from "../mocks/handlers";
import { server } from "../mocks/server";
import snapshotJson from "../../public/snapshot.json";

const recorded = snapshotJson as Record<string, unknown>;
import { fmtMoney, fmtMoneyFull } from "../lib/format";
import backtestHS from "../mocks/fixtures/backtest_summary__model_HS__scope_FIRM__window_250.json";
import movers from "../mocks/fixtures/risk_movers.json";
import results from "../mocks/fixtures/scenarios_results.json";
import summary from "../mocks/fixtures/risk_summary.json";
import uncertainty from "../mocks/fixtures/risk_uncertainty.json";

const firm = summary.desks.find((d) => d.is_aggregate)!;

beforeEach(() => {
  window.history.pushState({}, "", "/"); // a failed test must not leak its pin
});

test("limit rail reproduces /risk/summary: every desk row shows VaR / limit and %", async () => {
  render(<App />);
  const rail = await screen.findByRole("region", { name: "Limits and firm measures" });
  expect(within(rail).getByText("FIRM")).toBeInTheDocument();
  for (const d of summary.desks.filter((x) => !x.is_aggregate)) {
    // the desk name also appears in the movers rail below
    expect(within(rail).getAllByText(d.desk_name.toUpperCase()).length).toBeGreaterThan(0);
    if (d.utilization != null) {
      expect(
        within(rail).getAllByText(`${Math.round(d.utilization * 100)}%`).length,
      ).toBeGreaterThan(0);
    }
  }
  // firm tile carries the full-precision value
  expect(within(rail).getByTitle(fmtMoneyFull(firm.var_hs_1d))).toBeInTheDocument();
});

test("movers lead with the full desk name and carry driver strings", async () => {
  render(<App />);
  const first = movers.rows[0];
  const name = summary.desks.find((d) => d.desk_code === first.desk_code)!.desk_name;
  const row = (
    await screen.findByText(new RegExp(`— ${first.drivers[0].replace(/[.+]/g, "\\$&")}`))
  ).closest("div")!;
  expect(within(row).getByText(name.toUpperCase())).toBeInTheDocument();
});

test("a pinned as-of rides every overview link", async () => {
  // pinned queries carry ?as_of=; serve them from the frozen snapshot like
  // the offline fallback does
  server.use(
    http.get(`${API_URL}/api/v1/*`, ({ request }) => {
      const url = new URL(request.url);
      url.searchParams.delete("as_of");
      const key = canonicalFromUrl(url);
      if (key in recorded) {
        return HttpResponse.json(recorded[key] as Record<string, never>);
      }
      return HttpResponse.json({ detail: `no fixture for ${key}` }, { status: 404 });
    }),
  );
  window.history.pushState({}, "", "/?as_of=2026-06-30");
  render(<App />);
  const rail = await screen.findByRole("region", { name: "Limits and firm measures" });
  const desk = summary.desks.find((d) => !d.is_aggregate)!;
  expect(
    within(rail).getByRole("link", { name: desk.desk_name.toUpperCase() }),
  ).toHaveAttribute("href", `/desks/${desk.desk_code}?as_of=2026-06-30`);
  const stress = await screen.findByRole("region", { name: "Stress and book" });
  const worst = results.results[0];
  const link = within(stress).getByText(worst.scenario_name.toUpperCase()).closest("a")!;
  expect(link.getAttribute("href")).toContain("as_of=2026-06-30");
  expect(link.getAttribute("href")).toContain(`drill=${worst.scenario_code}`);
});

test("stats row serves the backtest verbatim and flips with the method tabs", async () => {
  render(<App />);
  expect(
    await screen.findByText(`${backtestHS.n_exceptions} / ${backtestHS.n_obs}`),
  ).toBeInTheDocument();
  expect(screen.getByText(backtestHS.kupiec.p_value.toFixed(2))).toBeInTheDocument();
  const fhsTab = screen.getByRole("button", { name: "FILTERED HS (EWMA)" });
  fireEvent.click(fhsTab);
  expect(fhsTab).toHaveAttribute("aria-pressed", "true");
  const fhs = await import(
    "../mocks/fixtures/backtest_summary__model_FHS__scope_FIRM__window_250.json"
  );
  expect(
    await screen.findByText(`${fhs.default.n_exceptions} / ${fhs.default.n_obs}`),
  ).toBeInTheDocument();
});

test("stress rail lists scenarios worst first and links into the drill-down", async () => {
  render(<App />);
  const rail = await screen.findByRole("region", { name: "Stress and book" });
  const worst = results.results[0];
  const row = await within(rail).findByText(worst.scenario_name.toUpperCase());
  expect(row.closest("a")).toHaveAttribute(
    "href",
    `/scenarios?drill=${worst.scenario_code}`,
  );
  expect(
    within(rail).getByTitle(fmtMoneyFull(worst.firm_impact)),
  ).toHaveTextContent(fmtMoney(worst.firm_impact));
});

test("book rail folds the collar into SPY and marks bond face", async () => {
  render(<App />);
  const rail = await screen.findByRole("region", { name: "Stress and book" });
  expect(await within(rail).findByText("SPY +COLLAR")).toBeInTheDocument();
  expect(within(rail).queryByText("SPY_PUT_95")).toBeNull();
  expect(within(rail).getByText("$90.0M FACE")).toBeInTheDocument(); // UST 2Y face
  expect(within(rail).getByText(/NET DV01/)).toBeInTheDocument();
  expect(within(rail).getByText(/\$\d+K \/ BP/)).toBeInTheDocument();
});

test("an empty movers day renders a quiet row", async () => {
  server.use(
    http.get(`${API_URL}/api/v1/risk/movers`, () =>
      HttpResponse.json({ ...movers, rows: [] }),
    ),
  );
  render(<App />);
  expect(await screen.findByText("NO DAY-OVER-DAY MOVERS")).toBeInTheDocument();
});

test("the sampling strip quotes the recorded interval", async () => {
  // absence is the component's null branch (a snapshot recorded before the
  // endpoint existed): the client falls back to the snapshot only on network
  // failure - HTTP errors stay loud by design - so the strip's presence here
  // rides the recorded fixture like every other assertion in this file
  render(<App />);
  const strip = await screen.findByLabelText("Firm VaR sampling uncertainty");
  const firmU = uncertainty.desks.find((d) => d.is_aggregate)!;
  expect(strip.textContent).toContain(fmtMoney(firmU.ci_low));
  expect(strip.textContent).toContain(fmtMoney(firmU.ci_high));
  expect(strip.textContent).toContain(
    `RANKS ${firmU.rank_low}–${firmU.rank_high} OF ${uncertainty.n_scenarios}`,
  );
  // the tooltip carries the exact-interval story: ranks and achieved coverage
  expect(strip.getAttribute("title")).toContain(
    `coverage ${(firmU.coverage * 100).toFixed(1)}%`,
  );
});
