import { fireEvent, render, screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { vi } from "vitest";

import App from "../App";
import { API_URL } from "../api/client";
import { server } from "../mocks/server";
import snapshot from "../../public/snapshot.json";

// the recorded fixtures drive every assertion - same numbers the API served
test("terminal header: nav, batch status, as-of select, footer stamp", async () => {
  render(<App />);
  const nav = await screen.findByRole("navigation", { name: "Main" });
  const labels = ["OVERVIEW", "DESKS", "SCENARIOS", "BACKTESTING", "MODEL DOC"];
  for (const label of labels) {
    expect(within(nav).getByRole("link", { name: label })).toBeInTheDocument();
  }
  expect(within(nav).getByRole("link", { name: "MODEL DOC" })).toHaveAttribute(
    "href",
    "/docs",
  );
  expect(await screen.findByText("COMPLETE")).toBeInTheDocument(); // EOD BATCH status
  expect(screen.getByLabelText("As-of date")).toBeInTheDocument();
  expect(screen.getByText(/RISKDESK .* · SNAPSHOT/)).toBeInTheDocument(); // footer
  expect(screen.getByText("NEXT EOD BATCH 18:30 ET")).toBeInTheDocument();
});

test("factor tape ticks with convention-suffixed day moves", async () => {
  render(<App />);
  // rates tick in bp; the tape duplicates content for the marquee loop
  expect((await screen.findAllByText("UST 2Y")).length).toBeGreaterThanOrEqual(2);
  expect(screen.getAllByText("VIX 30D").length).toBeGreaterThanOrEqual(2);
  expect(screen.getAllByText(/bp$/).length).toBeGreaterThan(0);
});

test("overview shows the firm VaR to the cent", async () => {
  render(<App />);
  expect(await screen.findByTitle("$1,137,118.30")).toHaveTextContent("$1.14M");
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
  fireEvent.click(await screen.findByRole("link", { name: "BACKTESTING" }));
  expect(await screen.findByLabelText("Scope")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).toBeNull();

  // ...and once the API recovers, revisiting the errored route recovers too
  server.resetHandlers();
  fireEvent.click(screen.getByRole("link", { name: "OVERVIEW" }));
  expect(await screen.findByTitle("$1,137,118.30")).toBeInTheDocument();
  spy.mockRestore();
});

test("network failure falls back to the snapshot with the demo badge", async () => {
  window.history.pushState({}, "", "/"); // a prior test may have navigated away
  server.use(
    http.get(`${API_URL}/api/v1/*`, () => HttpResponse.error()),
    http.get("/snapshot.json", () => HttpResponse.json(snapshot)),
  );
  render(<App />);
  expect(await screen.findByText("DEMO SNAPSHOT")).toBeInTheDocument();
  expect(await screen.findByTitle("$1,137,118.30")).toBeInTheDocument(); // data still renders
});
