import { useSearchParams } from "react-router-dom";

import { ApiError } from "../api/client";
import { useBacktestPower, useBacktestSummary, useMeta, usePla, useRiskHistory } from "../api/queries";
import type { LRTest } from "../api/types";
import { EChart } from "../components/EChart";
import { HistoryDataTable } from "../components/HistoryDataTable";
import { Skeleton } from "../components/Skeleton";
import { StaleBanner } from "../components/StaleBanner";
import { ZoneBadge } from "../components/ZoneBadge";
import table from "../components/DataTable.module.css";
import { linkedBacktestOption, plaScatterOption, ROLLING_WINDOW } from "../lib/backtest";
import { fmtMoney } from "../lib/format";
import styles from "./Backtesting.module.css";

const WINDOWS = [250, 500, 750];

// one-sentence null hypotheses - interviewers hover
const NULLS = {
  kupiec:
    "H0: the true exception probability is 1% - unconditional coverage, judged from the count alone.",
  independence:
    "H0: exceptions arrive independently - a violation today says nothing about tomorrow.",
  cc: "H0: correct coverage and independence hold jointly.",
  zone: "Not a hypothesis test: the Basel capital-multiplier add-on from the trailing 250-day exception count.",
};

// per-scope decomposition honesty (model doc section 4): what actually drives
// the HPL-RTPL gap on this book
const PLA_GAP_NOTE: Record<string, string> = {
  FIRM: "The linear legs' log-linearization dominates; the collar and the bonds' delta-gamma residual add the rest.",
  EQUITY: "Most of the gap is the linear legs' log-linearization; the collar adds the rest.",
  RATES: "The gap is the delta-gamma approximation against full bond revaluation.",
  FX: "The gap is the log-linearization of the spot legs.",
};

function StatCard({ title, hint, test }: { title: string; hint: string; test: LRTest }) {
  return (
    <div className={styles.card}>
      <div className={styles.cardTitle}>
        {title}
        <i className={styles.info} title={hint} aria-label={hint} role="img">
          i
        </i>
      </div>
      <div className={`${styles.stat} num`}>LR {test.statistic.toFixed(2)}</div>
      <div className={`${styles.cardSub} num`}>
        p = {test.p_value.toFixed(3)} ·{" "}
        <span className={test.reject_5pct ? styles.reject : styles.pass}>
          {test.reject_5pct ? "reject at 5%" : "not rejected"}
        </span>
      </div>
    </div>
  );
}

export default function Backtesting() {
  const meta = useMeta();
  const [search, setSearch] = useSearchParams();
  const scope = search.get("scope") ?? "FIRM";
  const model = search.get("model") ?? "HS";
  const rawWindow = Number(search.get("window") ?? "250");
  const windowDays = WINDOWS.includes(rawWindow) ? rawWindow : 250; // NaN never matches

  const setParam = (key: string, value: string) =>
    setSearch(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set(key, value);
        return next;
      },
      { replace: true },
    );

  const summary = useBacktestSummary(scope, model, windowDays);
  const power = useBacktestPower(scope, model, windowDays);
  // over-fetch by the rolling lookback so every displayed count covers a full
  // trailing window (the API caps history at 1000 = 750 + 250 exactly)
  const fetchWindow = Math.min(windowDays + ROLLING_WINDOW, 1000);
  const history = useRiskHistory(scope === "FIRM" ? "FIRM" : `desk:${scope}`, fetchWindow);
  const pla = usePla(scope, windowDays);

  const desks = (meta.data?.desks ?? []).filter((d) => !d.is_aggregate);

  return (
    <>
      {summary.isSuccess && <StaleBanner resolved={summary.data.end} />}
      <div className={styles.controls}>
        <label>
          Scope
          <select value={scope} onChange={(e) => setParam("scope", e.target.value)}>
            <option value="FIRM">FIRM</option>
            {desks.map((d) => (
              <option key={d.desk_code} value={d.desk_code}>
                {d.desk_code}
              </option>
            ))}
          </select>
        </label>
        <label>
          Model
          <select value={model} onChange={(e) => setParam("model", e.target.value)}>
            <option value="HS">HS</option>
            <option value="FHS">FHS</option>
          </select>
        </label>
        <label>
          Window
          <select value={windowDays} onChange={(e) => setParam("window", e.target.value)}>
            {WINDOWS.map((w) => (
              <option key={w} value={w}>
                {w}d
              </option>
            ))}
          </select>
        </label>
      </div>

      {summary.isError ? (
        <div className={styles.panel}>
          <p className={table.dim} style={{ padding: 12 }}>
            Backtest unavailable - {summary.error.message}
          </p>
        </div>
      ) : summary.isSuccess ? (
        <div className={styles.cards}>
          <StatCard title="Kupiec POF" hint={NULLS.kupiec} test={summary.data.kupiec} />
          <StatCard
            title="Christoffersen ind."
            hint={NULLS.independence}
            test={summary.data.christoffersen_independence}
          />
          <StatCard
            title="Christoffersen cc"
            hint={NULLS.cc}
            test={summary.data.christoffersen_cc}
          />
          <div className={styles.card}>
            <div className={styles.cardTitle}>
              Basel zone
              <i className={styles.info} title={NULLS.zone} aria-label={NULLS.zone} role="img">
                i
              </i>
            </div>
            <div className={styles.stat}>
              <ZoneBadge zone={summary.data.traffic_light.zone} />
            </div>
            <div className={`${styles.cardSub} num`}>
              {summary.data.n_exceptions} exc / {summary.data.n_obs}d (exp{" "}
              {summary.data.expected_exceptions}) · multiplier{" "}
              {summary.data.traffic_light.multiplier.toFixed(2)}
              {summary.data.traffic_light.regulatory_window ? "" : " · non-regulatory window"}
            </div>
          </div>
        </div>
      ) : (
        <div className={styles.cards}>
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} height={96} />
          ))}
        </div>
      )}

      {power.isLoading ? (
        <Skeleton height={32} />
      ) : power.isSuccess ? (
        <div
          role="note"
          className={styles.resolution}
          aria-label="Backtest resolution"
          title={`What the tests at this realized window (${power.data.n_obs} days) can detect. The Kupiec acceptance band is the range of exception counts that survives; the 1.2% alternative is an exception rate 20% above target - under normality only a ~${Math.round(power.data.var_understatement_equiv * 100)}% VaR understatement.`}
        >
          TEST RESOLUTION ({power.data.n_obs}D) — ACCEPTS {power.data.accept_low}–
          {power.data.accept_high} EXC ({(power.data.implied_rate_low * 100).toFixed(2)}%–
          {(power.data.implied_rate_high * 100).toFixed(2)}%) · REALIZED SIZE{" "}
          {(power.data.realized_size * 100).toFixed(1)}% · POWER VS{" "}
          {(power.data.p_alternative * 100).toFixed(1)}% RATE{" "}
          {(power.data.power * 100).toFixed(1)}% · 80% POWER ≈{" "}
          {Math.round(power.data.years_for_80pct_power)}Y OF DAILY DATA
        </div>
      ) : null}

      <div className={styles.panel}>
        <div className={styles.panelTitle}>
          {scope} P&amp;L vs -VaR ({model}) with the rolling 250d exception count
        </div>
        {history.isError ? (
          <p className={table.dim} style={{ padding: "0 12px 12px" }}>
            History unavailable - {history.error.message}
          </p>
        ) : history.isSuccess ? (
          <>
            <EChart
              option={linkedBacktestOption(history.data.points, model, windowDays)}
              height={430}
            />
            <HistoryDataTable points={history.data.points.slice(-windowDays)} />
          </>
        ) : (
          <Skeleton height={464} /> /* 430 chart + collapsed data-table strip */
        )}
      </div>

      <div className={styles.panel}>
        <div className={styles.panelTitle}>P&amp;L attribution: HPL vs RTPL</div>
        {pla.isError ? (
          <p className={table.dim} style={{ padding: 12 }}>
            {pla.error instanceof ApiError && pla.error.status === 404
              ? "No P&L-attribution pairs for this scope yet - RTPL history arrives with the nightly batch."
              : `PLA unavailable - ${pla.error.message}`}
          </p>
        ) : pla.isSuccess ? (
          <>
            <div className={`${styles.plaStats} num`}>
              <span>
                Spearman ρ <strong>{pla.data.spearman.toFixed(4)}</strong>
              </span>
              <span>
                KS <strong>{pla.data.ks.toFixed(4)}</strong>
              </span>
              <ZoneBadge zone={pla.data.zone} />
              <span className={table.dim}>
                {pla.data.n_obs} paired days, {pla.data.start} to {pla.data.end}
              </span>
            </div>
            <EChart option={plaScatterOption(pla.data.points)} height={320} />
            <p className={styles.verdict}>
              {pla.data.zone === "GREEN"
                ? "RTPL tracks HPL through the MAR32-style test."
                : "RTPL diverges from HPL under the MAR32-style test."}{" "}
              {PLA_GAP_NOTE[scope] ?? PLA_GAP_NOTE.FIRM}
            </p>
            <details className={table.chartData}>
              <summary>Data table</summary>
              <table className={table.table}>
                <thead>
                  <tr>
                    <th>Date</th>
                    <th className={table.r}>HPL</th>
                    <th className={table.r}>RTPL</th>
                  </tr>
                </thead>
                <tbody>
                  {pla.data.points.map((p) => (
                    <tr key={p.date}>
                      <td className="num">{p.date}</td>
                      <td className={`${table.r} num`}>{fmtMoney(p.hpl)}</td>
                      <td className={`${table.r} num`}>{fmtMoney(p.rtpl)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          </>
        ) : (
          <Skeleton height={438} /> /* stats row + scatter + verdict + table strip */
        )}
      </div>
    </>
  );
}
