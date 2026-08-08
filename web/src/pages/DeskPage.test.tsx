import { fireEvent, render, screen, within } from "@testing-library/react";

import App from "../App";
import { fmtMoney, fmtMoneyFull } from "../lib/format";
import equityDecomp from "../mocks/fixtures/desks_EQUITY_decomposition.json";
import equityPositions from "../mocks/fixtures/desks_EQUITY_positions.json";

function renderAt(path: string) {
  window.history.pushState({}, "", path);
  return render(<App />);
}

test("header VaR reproduces the decomposition fixture to the cent", async () => {
  renderAt("/desks/EQUITY");
  expect(await screen.findByTitle(fmtMoneyFull(equityDecomp.var_hs_1d))).toHaveTextContent(
    fmtMoney(equityDecomp.var_hs_1d),
  );
});

test("waterfall data table reconciles buckets + diversification to the desk VaR", async () => {
  renderAt("/desks/EQUITY");
  expect(await screen.findByText("Diversification")).toBeInTheDocument();
  const total =
    equityDecomp.buckets.reduce((s, b) => s + b.standalone_var, 0) +
    equityDecomp.diversification;
  expect(Math.round(total * 100) / 100).toBe(equityDecomp.var_hs_1d);
  expect(screen.getByText(fmtMoneyFull(equityDecomp.diversification))).toBeInTheDocument();
});

test("collar legs render with their option metadata", async () => {
  renderAt("/desks/EQUITY");
  const put = await screen.findByText("SPY_PUT_95");
  expect(within(put.closest("td")!).getByText(/PUT @ 0\.95× spot/)).toBeInTheDocument();
  expect(screen.getByText(/CALL @ 1\.03× spot/)).toBeInTheDocument();
});

test("positions sort toggles stably by any column", async () => {
  renderAt("/desks/EQUITY");
  await screen.findByText("SPY_PUT_95");
  const tickers = () =>
    within(screen.getByText("Positions").closest("div")!.parentElement!)
      .getAllByRole("row")
      .slice(1)
      .map((r) => within(r).getAllByRole("cell")[0].textContent!.split("\n")[0]);

  // default: component ES desc - biggest contributor first, per the fixture
  const byComponent = [...equityPositions.positions]
    .sort((a, b) => b.component_es - a.component_es)
    .map((p) => p.ticker);
  expect(tickers().map((t) => t.replace(/PUT @.*|CALL @.*/, ""))).toEqual(byComponent);

  // clicking Standalone VaR re-sorts desc by that column
  fireEvent.click(screen.getByRole("button", { name: /Standalone VaR/ }));
  const byStandalone = [...equityPositions.positions]
    .sort((a, b) => b.standalone_var - a.standalone_var)
    .map((p) => p.ticker);
  expect(tickers().map((t) => t.replace(/PUT @.*|CALL @.*/, ""))).toEqual(byStandalone);

  // second click flips to ascending
  fireEvent.click(screen.getByRole("button", { name: /Standalone VaR/ }));
  expect(tickers().map((t) => t.replace(/PUT @.*|CALL @.*/, ""))).toEqual(
    [...byStandalone].reverse(),
  );
});

test("exposure bar groups carry units in their titles and a reachable data table", async () => {
  renderAt("/desks/RATES");
  expect(await screen.findByText("Key-rate DV01 ($ per 1bp)")).toBeInTheDocument();
  const ratesDecomp = (await import("../mocks/fixtures/desks_RATES_decomposition.json"))
    .default;
  const krd = ratesDecomp.exposures.find((e) => e.measure === "KRD_DV01")!;
  // the chart's text alternative carries the exact figure
  expect(screen.getByText(fmtMoneyFull(krd.value))).toBeInTheDocument();
});
