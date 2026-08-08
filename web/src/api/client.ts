// Fetch wrapper with the offline demo fallback: on a NETWORK failure (server
// unreachable, not an HTTP error) responses come from the bundled
// public/snapshot.json and the demo badge turns on. HTTP errors stay loud.
import { useSyncExternalStore } from "react";

export const API_URL: string =
  (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8000";

export type Params = Record<string, string | number>;

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(`HTTP ${status}: ${detail}`);
    this.status = status;
    this.detail = detail;
  }
}

// snapshot lookup key: path + sorted query string - must match
// web/scripts/record_fixtures.py::canonical
export function canonical(path: string, params?: Params): string {
  const keys = Object.keys(params ?? {}).sort();
  if (!keys.length) return path;
  const query = keys.map((k) => `${k}=${(params as Params)[k]}`).join("&");
  return `${path}?${query}`;
}

// tiny external store so any component can subscribe to demo mode
let demoMode = false;
const demoListeners = new Set<() => void>();
function setDemoMode(on: boolean): void {
  if (demoMode === on) return;
  demoMode = on;
  demoListeners.forEach((l) => l());
}
export function subscribeDemoMode(listener: () => void): () => void {
  demoListeners.add(listener);
  return () => demoListeners.delete(listener);
}
export function isDemoMode(): boolean {
  return demoMode;
}
export function useDemoMode(): boolean {
  return useSyncExternalStore(subscribeDemoMode, isDemoMode);
}

let snapshotPromise: Promise<Record<string, unknown>> | null = null;
function loadSnapshot(): Promise<Record<string, unknown>> {
  snapshotPromise ??= fetch("/snapshot.json")
    .then((r) => {
      if (!r.ok) throw new Error(`snapshot.json missing (HTTP ${r.status})`);
      return r.json() as Promise<Record<string, unknown>>;
    })
    .catch((err: unknown) => {
      snapshotPromise = null; // a failed load must not poison later retries
      throw err;
    });
  return snapshotPromise;
}

// POST for the what-if sandbox: compute requests never fall back to the
// snapshot (a frozen day cannot answer a hypothetical book)
export async function postJson<T>(path: string, body: unknown, params?: Params): Promise<T> {
  const url = new URL(path, API_URL);
  for (const k of Object.keys(params ?? {}).sort()) {
    url.searchParams.set(k, String((params as Params)[k]));
  }
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = ((await res.json()) as { detail?: string }).detail ?? detail;
    } catch {
      // non-JSON error body: keep the status text
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export async function getJson<T>(path: string, params?: Params): Promise<T> {
  const url = new URL(path, API_URL);
  for (const k of Object.keys(params ?? {}).sort()) {
    url.searchParams.set(k, String((params as Params)[k]));
  }
  let res: Response;
  try {
    res = await fetch(url);
  } catch (err) {
    // network down: serve the recorded snapshot if it covers this request
    const snap = await loadSnapshot().catch(() => {
      throw err;
    });
    let key = canonical(path, params);
    if (!(key in snap) && params?.as_of !== undefined) {
      // the snapshot is one frozen day: a pinned as_of never matches a
      // recorded key - serve the frozen day and let the demo badge say so
      const unpinned = { ...params };
      delete unpinned.as_of;
      key = canonical(path, unpinned);
    }
    if (key in snap) {
      setDemoMode(true);
      return snap[key] as T;
    }
    throw err;
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = ((await res.json()) as { detail?: string }).detail ?? detail;
    } catch {
      // non-JSON error body: keep the status text
    }
    throw new ApiError(res.status, detail);
  }
  setDemoMode(false);
  return (await res.json()) as T;
}
