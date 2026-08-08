import { fireEvent, render, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";

import App from "../App";
import { API_URL } from "../api/client";
import { server } from "../mocks/server";
import backtestHS from "../mocks/fixtures/backtest_summary__model_HS__scope_FIRM__window_250.json";

function renderAt(path: string) {
  window.history.pushState({}, "", path);
  return render(<App />);
}

test("stats cards reproduce /backtest/summary exactly", async () => {
  renderAt("/backtesting");
  expect(await screen.findByText(`LR ${backtestHS.kupiec.statistic.toFixed(2)}`)).toBeInTheDocument();
  expect(
    screen.getByText(new RegExp(`p = ${backtestHS.kupiec.p_value.toFixed(3)}`)),
  ).toBeInTheDocument();
  // the zone text can appear on both the Basel card and the PLA badge
  expect(screen.getAllByText(backtestHS.traffic_light.zone).length).toBeGreaterThan(0);
  expect(
    screen.getByText(new RegExp(`${backtestHS.n_exceptions} exc / ${backtestHS.n_obs}d`)),
  ).toBeInTheDocument();
});

test("every stat card carries its null hypothesis", async () => {
  renderAt("/backtesting");
  await screen.findAllByText(/LR /);
  expect(screen.getByLabelText(/unconditional coverage/)).toBeInTheDocument();
  expect(screen.getByLabelText(/arrive independently/)).toBeInTheDocument();
  expect(screen.getByLabelText(/hold jointly/)).toBeInTheDocument();
});

test("scope, model and window live in the URL as query params", async () => {
  renderAt("/backtesting");
  const model = await screen.findByLabelText("Model");
  fireEvent.change(model, { target: { value: "FHS" } });
  expect(new URLSearchParams(window.location.search).get("model")).toBe("FHS");
  const win = screen.getByLabelText("Window");
  fireEvent.change(win, { target: { value: "500" } });
  expect(new URLSearchParams(window.location.search).get("window")).toBe("500");
});

test("PLA panel is gracefully absent when pairs are insufficient", async () => {
  server.use(
    http.get(`${API_URL}/api/v1/backtest/pla`, () =>
      HttpResponse.json({ detail: "insufficient paired P&L" }, { status: 404 }),
    ),
  );
  renderAt("/backtesting");
  expect(await screen.findByText(/No P&L-attribution pairs/)).toBeInTheDocument();
});

test("PLA panel shows the statistics and the decomposition-honesty verdict", async () => {
  renderAt("/backtesting");
  expect(await screen.findByText(/log-linearization/)).toBeInTheDocument();
  expect(screen.getByText(/Spearman/)).toBeInTheDocument();
  expect(screen.getByText(/RTPL tracks HPL/)).toBeInTheDocument(); // fixture zone is GREEN
});

test("a non-green PLA zone flips the verdict - no pass language beside a RED badge", async () => {
  const pla = await import(
    "../mocks/fixtures/backtest_pla__scope_FIRM__window_250.json"
  );
  server.use(
    http.get(`${API_URL}/api/v1/backtest/pla`, () =>
      HttpResponse.json({ ...pla.default, zone: "RED", spearman: 0.55, ks: 0.15 }),
    ),
  );
  renderAt("/backtesting");
  expect(await screen.findByText(/RTPL diverges from HPL/)).toBeInTheDocument();
  expect(screen.queryByText(/RTPL tracks HPL/)).toBeNull();
});
