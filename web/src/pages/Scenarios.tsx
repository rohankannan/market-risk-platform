import { useSearchParams } from "react-router-dom";

import { ApiError } from "../api/client";
import { useScenarioCatalog, useScenarioResults } from "../api/queries";
import type { ScenarioSpec } from "../api/types";
import { EChart } from "../components/EChart";
import { Skeleton } from "../components/Skeleton";
import { StaleBanner } from "../components/StaleBanner";
import table from "../components/DataTable.module.css";
import { fmtMoney, fmtMoneyFull } from "../lib/format";
import {
  compareBarsOption,
  dominantMove,
  MAX_COMPARE,
  scenarioWaterfallOption,
  waterfallRows,
} from "../lib/scenarios";
import styles from "./Scenarios.module.css";

function windowLabel(spec: ScenarioSpec | undefined): string | null {
  if (!spec?.window_start || !spec.window_end) return null;
  return `${spec.window_start} to ${spec.window_end}`;
}

export default function Scenarios() {
  const catalog = useScenarioCatalog();
  const results = useScenarioResults();
  const [search, setSearch] = useSearchParams();

  if (results.isPending || catalog.isPending) {
    return (
      <div className={styles.layout}>
        <Skeleton height={260} />
        <div>
          <Skeleton height={300} />
          <div style={{ height: 16 }} />
          <Skeleton height={200} />
        </div>
      </div>
    );
  }
  if (results.isError) {
    // backfill runs never execute scenarios, so a pin before the first EOD
    // cycle resolves to nothing - a quiet state, not a page failure
    if (results.error instanceof ApiError && results.error.status === 404) {
      return (
        <p className={table.dim} style={{ padding: 12 }}>
          No scenario run on or before this as-of - scenarios arrive with the nightly EOD
          batch. Pick a later date or clear the pin.
        </p>
      );
    }
    throw results.error;
  }
  if (catalog.isError) throw catalog.error;

  const specs = new Map(catalog.data.scenarios.map((s) => [s.scenario_code, s]));
  const ordered = results.data.results; // worst firm impact first, per the API
  const defaultIds = ordered.slice(0, MAX_COMPARE).map((r) => r.scenario_code);
  const idsParam = search.get("ids");
  // an explicit empty ids= means "none selected"; unknown or repeated codes
  // must not consume the compare cap
  const known = new Set(ordered.map((r) => r.scenario_code));
  const selected =
    idsParam !== null
      ? [...new Set(idsParam.split(",").filter((c) => known.has(c)))].slice(0, MAX_COMPARE)
      : defaultIds;

  const setParam = (key: string, value: string) =>
    setSearch(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set(key, value);
        return next;
      },
      { replace: true },
    );

  const toggle = (code: string) => {
    const next = selected.includes(code)
      ? selected.filter((c) => c !== code)
      : [...selected, code].slice(0, MAX_COMPARE);
    setParam("ids", next.join(","));
  };

  const compared = ordered.filter((r) => selected.includes(r.scenario_code));
  const drill = search.get("drill");
  const drillResult = ordered.find((r) => r.scenario_code === drill) ?? ordered[0];
  const drillSpec = specs.get(drillResult?.scenario_code ?? "");
  const comparedDesks = Array.from(
    new Set(compared.flatMap((r) => Object.keys(r.impacts).filter((c) => c !== "FIRM"))),
  );

  return (
    <>
      <StaleBanner resolved={results.data.as_of} />
      <div className={styles.layout}>
        <aside className={styles.rail} aria-label="Scenario selection">
          <div className={styles.railTitle}>Compare (max {MAX_COMPARE})</div>
          {ordered.map((r) => {
            const spec = specs.get(r.scenario_code);
            const checked = selected.includes(r.scenario_code);
            return (
              <label key={r.scenario_code} className={styles.railItem}>
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={!checked && selected.length >= MAX_COMPARE}
                  onChange={() => toggle(r.scenario_code)}
                />
                <span>
                  {r.scenario_name}
                  <span className={`${styles.railMeta} num`}>
                    {windowLabel(spec) ?? dominantMove(spec) ?? ""}
                  </span>
                </span>
              </label>
            );
          })}
        </aside>

        <div>
          <div className={styles.panel}>
            <div className={styles.panelTitle}>Scenario P&amp;L by desk, worst first</div>
            {compared.length ? (
              <>
                <EChart
                  option={compareBarsOption(compared)}
                  height={300}
                  onEvents={{
                    click: (p) => {
                      const name = (p as { name?: string }).name;
                      if (name && known.has(name)) setParam("drill", name);
                    },
                  }}
                />
                <details className={table.chartData}>
                  <summary>Data table</summary>
                  <table className={table.table}>
                    <thead>
                      <tr>
                        <th>Desk</th>
                        {compared.map((r) => (
                          <th key={r.scenario_code} className={table.r}>
                            {r.scenario_code}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {[...comparedDesks, "FIRM"].map((code) => (
                        <tr key={code}>
                          <td>{code}</td>
                          {compared.map((r) => {
                            const v =
                              code === "FIRM" ? r.firm_impact : (r.impacts[code] ?? null);
                            return (
                              <td key={r.scenario_code} className={`${table.r} num`}>
                                {v == null ? "-" : fmtMoneyFull(v)}
                              </td>
                            );
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </details>
              </>
            ) : (
              <p className={table.dim} style={{ padding: 12 }}>
                Select scenarios on the left to compare.
              </p>
            )}
          </div>

          {drillResult && (
            <div className={styles.panel}>
              <div className={styles.panelTitle}>
                {drillResult.scenario_name}: desk contributions to the firm impact
              </div>
              <EChart option={scenarioWaterfallOption(drillResult)} height={260} />
              <div className={`${styles.drillMeta} num`}>
                {drillResult.scenario_type === "HISTORICAL_REPLAY"
                  ? `Replay window ${windowLabel(drillSpec) ?? "unknown"}`
                  : `Dominant move ${dominantMove(drillSpec) ?? "-"}`}
                {drillSpec?.description ? ` · ${drillSpec.description}` : ""}
              </div>
              <details className={table.chartData}>
                <summary>Data table</summary>
                <table className={table.table}>
                  <tbody>
                    {waterfallRows(drillResult).map(([code, v]) => (
                      <tr key={code}>
                        <td>{code === "FIRM" ? <strong>FIRM</strong> : code}</td>
                        <td className={`${table.r} num`}>{fmtMoneyFull(v)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </details>
            </div>
          )}

          <div className={styles.panel}>
            <div className={styles.panelTitle}>All scenarios</div>
            <table className={table.table}>
              <thead>
                <tr>
                  <th>Scenario</th>
                  <th>Type</th>
                  <th>Window / dominant move</th>
                  <th>Worst desk</th>
                  <th className={table.r}>Worst desk P&amp;L</th>
                  <th className={table.r}>Firm P&amp;L</th>
                </tr>
              </thead>
              <tbody>
                {ordered.map((r) => {
                  const spec = specs.get(r.scenario_code);
                  const worst = r.worst_desk ? r.impacts[r.worst_desk] : null;
                  return (
                    <tr key={r.scenario_code}>
                      <td>
                        <button
                          className={styles.drillButton}
                          onClick={() => setParam("drill", r.scenario_code)}
                        >
                          {r.scenario_name}
                        </button>
                      </td>
                      <td className={table.dim}>{r.scenario_type.replace("_", " ")}</td>
                      <td className="num">{windowLabel(spec) ?? dominantMove(spec) ?? "-"}</td>
                      <td>{r.worst_desk ?? "-"}</td>
                      <td className={`${table.r} num`}>{worst == null ? "-" : fmtMoney(worst)}</td>
                      <td className={`${table.r} num`}>
                        {r.firm_impact == null ? "-" : fmtMoney(r.firm_impact)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </>
  );
}
