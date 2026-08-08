// MSW handlers built from the recorded snapshot: one catch-all resolver that
// canonicalizes the request (path + sorted query) and looks up the recorded
// payload - the same key scheme record_fixtures.py writes and client.ts reads.
import { http, HttpResponse } from "msw";

import snapshot from "../../public/snapshot.json";

import { API_URL } from "../api/client";

const recorded = snapshot as Record<string, unknown>;

export function canonicalFromUrl(url: URL): string {
  const keys = [...url.searchParams.keys()].sort();
  if (!keys.length) return url.pathname;
  const query = keys.map((k) => `${k}=${url.searchParams.get(k)}`).join("&");
  return `${url.pathname}?${query}`;
}

export const handlers = [
  http.get(`${API_URL}/api/v1/*`, ({ request }) => {
    const key = canonicalFromUrl(new URL(request.url));
    if (key in recorded) return HttpResponse.json(recorded[key] as Record<string, unknown>);
    return HttpResponse.json({ detail: `no fixture recorded for ${key}` }, { status: 404 });
  }),
];
