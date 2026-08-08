import { render, screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";

import App from "../App";
import { API_URL } from "../api/client";
import { server } from "../mocks/server";
import { fmtMoney, fmtMoneyFull, fmtPct } from "../lib/format";
import backtest from "../mocks/fixtures/backtest_summary__model_HS__scope_FIRM__window_250.json";
import history from "../mocks/fixtures/risk_history__scope_FIRM__window_90.json";
import movers from "../mocks/fixtures/risk_movers.json";
import summary from "../mocks/fixtures/risk_summary.json";

const firm = summary.desks.find((d) => d.is_aggregate)!;
const rates = summary.desks.find((d) => d.desk_code === "RATES")!;

test("tiles reproduce /risk/summary to the cent", async () => {
  render(<App />);
  // headline shows the compact form; the full-precision value rides the title
  expect(await screen.findByTitle(fmtMoneyFull(firm.var_hs_1d))).toHaveTextContent(
    fmtMoney(firm.var_hs_1d),
  );
  expect(screen.getByTitle(fmtMoneyFull(firm.es_975_1d))).toBeInTheDocument();
  // the utilization figure appears in the tile and again in the heatmap row
  expect(screen.getAllByText(fmtPct(firm.utilization)).length).toBeGreaterThan(0);
  expect(await screen.findByText(backtest.traffic_light.zone)).toBeInTheDocument();
  expect(
    screen.getByText(`${backtest.n_exceptions} exceptions / ${backtest.n_obs}d (HS)`),
  ).toBeInTheDocument();
});

test("heatmap desk and utilization cells route to the desk page", async () => {
  render(<App />);
  // scope to the content region - the sidebar carries the same desk name
  const main = within(screen.getByRole("main"));
  const link = await main.findByRole("link", { name: rates.desk_name });
  expect(link).toHaveAttribute("href", "/desks/RATES");
  const utilCell = main.getByRole("link", { name: fmtPct(rates.utilization) });
  expect(utilCell).toHaveAttribute("href", "/desks/RATES");
});

test("movers table renders the recorded rows with driver strings", async () => {
  render(<App />);
  const first = movers.rows[0];
  const row = (await screen.findByText(first.drivers.join(" · "))).closest("tr")!;
  expect(within(row).getByText(first.desk_code)).toBeInTheDocument();
});

test("an empty movers day renders a quiet row, not an empty panel", async () => {
  server.use(
    http.get(`${API_URL}/api/v1/risk/movers`, () =>
      HttpResponse.json({ ...movers, rows: [] }),
    ),
  );
  render(<App />);
  expect(await screen.findByText(/No day-over-day movers/)).toBeInTheDocument();
});

test("the 90d chart marks exactly the recorded exception days", async () => {
  render(<App />);
  const chart = await screen.findByTestId("echart");
  const option = JSON.parse(chart.getAttribute("data-option")!);
  const exc = (option.series as { name: string; data: unknown[] }[]).find(
    (s) => s.name === "Exception",
  )!;
  const expected = history.points.filter((p) => p.exception_hs || p.exception_fhs);
  expect(exc.data).toHaveLength(expected.length);
});
