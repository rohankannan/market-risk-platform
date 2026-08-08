import { fireEvent, render, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { vi } from "vitest";

import App from "../App";
import { API_URL } from "../api/client";
import { server } from "../mocks/server";
import snapshot from "../../public/snapshot.json";

// the recorded fixtures drive every assertion - same numbers the API served
test("shell renders the as-of badge, desk nav and footer from /meta", async () => {
  render(<App />);
  expect(await screen.findByText(/Data as of/)).toBeInTheDocument();
  expect(document.querySelector("header")?.textContent).toMatch(
    /Data as of 2026-08-06 EOD · batch \d{2}:\d{2} UTC/,
  );
  expect(await screen.findByRole("link", { name: /US Rates/ })).toHaveAttribute(
    "href",
    "/desks/RATES",
  );
  expect(screen.getByRole("link", { name: /Cash Equities/ })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Model Doc" })).toHaveAttribute("href", "/docs");
  expect(screen.getByText("bbf728d")).toBeInTheDocument(); // footer git SHA
});

test("overview stub shows the firm VaR to the fixture's value", async () => {
  render(<App />);
  expect(await screen.findByText("$1.14M")).toBeInTheDocument(); // 1,137,118.30
});

test("an errored page does not brick the shell: boundary resets on navigation", async () => {
  const spy = vi.spyOn(console, "error").mockImplementation(() => {}); // boundary noise
  server.use(
    http.get(`${API_URL}/api/v1/risk/summary`, () =>
      HttpResponse.json({ detail: "no run at or before this date" }, { status: 404 }),
    ),
  );
  render(<App />);
  expect(await screen.findByRole("alert")).toBeInTheDocument();

  // other routes stay healthy...
  fireEvent.click(await screen.findByRole("link", { name: "Backtesting" }));
  expect(await screen.findByRole("heading", { name: "Backtesting" })).toBeInTheDocument();
  expect(screen.queryByRole("alert")).toBeNull();

  // ...and once the API recovers, revisiting the errored route recovers too
  server.resetHandlers();
  fireEvent.click(screen.getByRole("link", { name: "Overview" }));
  expect(await screen.findByText("$1.14M")).toBeInTheDocument();
  spy.mockRestore();
});

test("network failure falls back to the snapshot with the demo badge", async () => {
  server.use(
    http.get(`${API_URL}/api/v1/*`, () => HttpResponse.error()),
    http.get("/snapshot.json", () => HttpResponse.json(snapshot)),
  );
  render(<App />);
  expect(await screen.findByText("demo snapshot")).toBeInTheDocument();
  expect(await screen.findByText("$1.14M")).toBeInTheDocument(); // data still renders
});
