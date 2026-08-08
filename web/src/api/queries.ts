// TanStack Query hooks, one per endpoint. Query keys are [path, as_of, params];
// staleTime mirrors the API's cache headers (resolve_run): a pinned as_of
// response is immutable once the batch completes (Cache-Control: immutable) so
// it never refetches, while un-pinned "latest" is no-cache on the server and
// goes stale on a slow clock - a parked tab picks up the nightly batch on the
// next focus or navigation without a hard reload.
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";

import { getJson, type Params } from "./client";
import type {
  BacktestSummary,
  DeskDecomposition,
  DeskPositions,
  FactorsLatest,
  KeyRateExposures,
  Meta,
  ModelDoc,
  PlaSummary,
  RiskHistory,
  RiskMovers,
  RiskSummary,
  ScenarioCatalog,
  ScenarioResults,
} from "./types";

// the global as-of pin lives in the URL (?as_of=YYYY-MM-DD) so views are linkable
export function useAsOf(): [string | null, (d: string | null) => void] {
  const [search, setSearch] = useSearchParams();
  const asOf = search.get("as_of");
  const setAsOf = (d: string | null) => {
    setSearch(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (d) next.set("as_of", d);
        else next.delete("as_of");
        return next;
      },
      { replace: true },
    );
  };
  return [asOf, setAsOf];
}

const LATEST_STALE_MS = 5 * 60 * 1000; // once-daily batch: minutes are plenty

function useApi<T>(path: string, params?: Params, enabled = true): UseQueryResult<T> {
  const [asOf] = useAsOf();
  const merged = asOf ? { ...params, as_of: asOf } : params;
  return useQuery<T>({
    queryKey: [path, asOf ?? "latest", merged ?? {}],
    queryFn: () => getJson<T>(path, merged),
    staleTime: asOf ? Infinity : LATEST_STALE_MS,
    enabled,
  });
}

// /meta is deliberately un-pinned: it is the bootstrap payload that lists the
// available dates the as-of select offers
export function useMeta(): UseQueryResult<Meta> {
  return useQuery<Meta>({
    queryKey: ["/api/v1/meta"],
    queryFn: () => getJson<Meta>("/api/v1/meta"),
    staleTime: LATEST_STALE_MS,
  });
}

export const useRiskSummary = () => useApi<RiskSummary>("/api/v1/risk/summary");
export const useRiskHistory = (scope: string, window: number) =>
  useApi<RiskHistory>("/api/v1/risk/history", { scope, window });
export const useRiskMovers = () => useApi<RiskMovers>("/api/v1/risk/movers");
export const useRiskExposures = () => useApi<KeyRateExposures>("/api/v1/risk/exposures");
export const useFactorsLatest = () => useApi<FactorsLatest>("/api/v1/factors/latest");
export const useDeskDecomposition = (desk: string) =>
  useApi<DeskDecomposition>(`/api/v1/desks/${desk}/decomposition`);
export const useDeskPositions = (desk: string) =>
  useApi<DeskPositions>(`/api/v1/desks/${desk}/positions`);
export const useBacktestSummary = (scope: string, model: string, window: number) =>
  useApi<BacktestSummary>("/api/v1/backtest/summary", { scope, model, window });
export const usePla = (scope: string, window: number) =>
  useApi<PlaSummary>("/api/v1/backtest/pla", { scope, window });
export const useScenarioCatalog = () => useApi<ScenarioCatalog>("/api/v1/scenarios");
export const useScenarioResults = () => useApi<ScenarioResults>("/api/v1/scenarios/results");
export const useModelDoc = () => useApi<ModelDoc>("/api/v1/modeldoc");
