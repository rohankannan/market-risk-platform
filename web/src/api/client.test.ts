import { http, HttpResponse } from "msw";

import { canonical, getJson, ApiError, API_URL } from "./client";
import { server } from "../mocks/server";
import snapshot from "../../public/snapshot.json";
import type { Meta, RiskHistory, RiskSummary } from "./types";

test("canonical sorts query keys to match the recorder", () => {
  expect(canonical("/api/v1/meta")).toBe("/api/v1/meta");
  expect(canonical("/api/v1/risk/history", { window: 90, scope: "FIRM" })).toBe(
    "/api/v1/risk/history?scope=FIRM&window=90",
  );
});

test("getJson returns recorded payloads through MSW", async () => {
  const meta = await getJson<Meta>("/api/v1/meta");
  expect(meta.latest_as_of).toBe("2026-08-06");
  const h = await getJson<RiskHistory>("/api/v1/risk/history", {
    window: 90,
    scope: "FIRM",
  });
  expect(h.scope).toBe("FIRM");
  expect(h.points.length).toBeGreaterThan(80);
});

test("pinned as_of still falls back: the snapshot is one frozen day", async () => {
  server.use(
    http.get(`${API_URL}/api/v1/*`, () => HttpResponse.error()),
    http.get("/snapshot.json", () => HttpResponse.json(snapshot)),
  );
  const s = await getJson<RiskSummary>("/api/v1/risk/summary", { as_of: "2026-07-01" });
  expect(s.desks.find((d) => d.is_aggregate)?.var_hs_1d).toBeCloseTo(1137118.3, 1);
});

test("HTTP errors surface the API detail and do NOT fall back", async () => {
  server.use(
    http.get(`${API_URL}/api/v1/risk/summary`, () =>
      HttpResponse.json({ detail: "no completed runs yet" }, { status: 404 }),
    ),
  );
  await expect(getJson("/api/v1/risk/summary")).rejects.toThrowError(ApiError);
  await expect(getJson("/api/v1/risk/summary")).rejects.toThrow(/no completed runs yet/);
});
