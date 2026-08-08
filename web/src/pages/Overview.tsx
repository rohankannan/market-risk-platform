import { Link, useSearchParams } from "react-router-dom";

import {
  useBacktestSummary,
  useRiskHistory,
  useRiskMovers,
  useRiskSummary,
} from "../api/queries";
import type { DeskRisk, HistoryPoint } from "../api/types";
import { EChart } from "../components/EChart";
import { KpiTile } from "../components/KpiTile";
import { Skeleton } from "../components/Skeleton";
import { Sparkline } from "../components/Sparkline";
import { StaleBanner } from "../components/StaleBanner";
import { ZoneBadge } from "../components/ZoneBadge";
import table from "../components/DataTable.module.css";
import { fmtMoney, fmtMoneyFull, fmtPct, fmtSignedPct } from "../lib/format";
import { pnlVsVarOption, UTIL_BAR, UTIL_BG, utilLevel } from "../lib/overview";
import styles from "./Overview.module.css";

const BACKTEST_WINDOW = 250;

function Tiles({ firm, points }: { firm: DeskRisk; points: HistoryPoint[] }) {
  const backtest = useBacktestSummary("FIRM", "HS", BACKTEST_WINDOW);
  const dod = firm.var_dod;
  const util = firm.utilization;
  return (
    <div className={styles.tiles}>
      <KpiTile
        label="Firm VaR 99 / 1d"
        value={<span title={fmtMoneyFull(firm.var_hs_1d)}>{fmtMoney(firm.var_hs_1d)}</span>}
        delta={dod == null ? undefined : `${dod > 0 ? "+" : ""}${fmtMoney(dod)}`}
        deltaColor={
          dod == null
            ? undefined
            : dod > 0
              ? "var(--pnl-neg)" // rising VaR reads as risk-up
              : dod < 0
                ? "var(--pnl-pos)"
                : "var(--text-dim)"
        }
        sub="historical simulation"
      >
        <Sparkline values={points.map((p) => p.var_hs)} />
      </KpiTile>
      <KpiTile
        label="Firm ES 97.5 / 1d"
        value={<span title={fmtMoneyFull(firm.es_975_1d)}>{fmtMoney(firm.es_975_1d)}</span>}
        sub="expected shortfall"
      >
        <Sparkline values={points.map((p) => p.es_975)} />
      </KpiTile>
      <KpiTile
        label="Limit utilization"
        value={fmtPct(util)}
        sub={firm.limit_value != null ? `of ${fmtMoney(firm.limit_value)} limit` : undefined}
      >
        {util != null && (
          <div className={styles.utilBar}>
            <div
              className={styles.utilFill}
              style={{
                width: `${Math.min(util, 1) * 100}%`,
                background: UTIL_BAR[utilLevel(util, firm.limit_status)],
              }}
            />
          </div>
        )}
      </KpiTile>
      <KpiTile
        label={`Backtest / ${BACKTEST_WINDOW}d`}
        value={
          backtest.isSuccess ? <ZoneBadge zone={backtest.data.traffic_light.zone} /> : "—"
        }
        sub={
          backtest.isSuccess
            ? `${backtest.data.n_exceptions} exceptions / ${backtest.data.n_obs}d (HS)`
            : backtest.isError
              ? "backtest unavailable"
              : undefined
        }
      />
    </div>
  );
}

function LimitHeatmap({ desks }: { desks: DeskRisk[] }) {
  const [search] = useSearchParams();
  const suffix = search.toString() ? `?${search.toString()}` : "";
  return (
    <div className={styles.panel}>
      <div className={styles.panelTitle}>Limits &amp; utilization</div>
      <table className={table.table}>
        <thead>
          <tr>
            <th>Desk</th>
            <th className={table.r}>VaR HS 1d</th>
            <th className={table.r}>VaR FHS 1d</th>
            <th className={table.r}>ES 97.5</th>
            <th className={table.r}>Stressed ES</th>
            <th className={table.r}>Limit</th>
            <th className={table.r}>Utilization</th>
          </tr>
        </thead>
        <tbody>
          {desks.map((d) => {
            const to = d.is_aggregate ? null : `/desks/${d.desk_code}${suffix}`;
            return (
              <tr key={d.desk_code}>
                <td>
                  {to ? (
                    <Link className={styles.deskCell} to={to}>
                      {d.desk_name}
                    </Link>
                  ) : (
                    <strong>{d.desk_name}</strong>
                  )}
                </td>
                <td className={`${table.r} num`}>{fmtMoney(d.var_hs_1d)}</td>
                <td className={`${table.r} num`}>{fmtMoney(d.var_fhs_1d)}</td>
                <td className={`${table.r} num`}>{fmtMoney(d.es_975_1d)}</td>
                <td className={`${table.r} num`}>{fmtMoney(d.es_stressed_1d)}</td>
                <td className={`${table.r} num`}>{fmtMoney(d.limit_value)}</td>
                <td className={`${table.r} num`}>
                  {d.utilization == null ? (
                    "-"
                  ) : to ? (
                    <Link
                      className={styles.utilCell}
                      style={{ background: UTIL_BG[utilLevel(d.utilization, d.limit_status)] }}
                      to={to}
                    >
                      {fmtPct(d.utilization)}
                    </Link>
                  ) : (
                    <span
                      className={styles.utilCell}
                      style={{ background: UTIL_BG[utilLevel(d.utilization, d.limit_status)] }}
                    >
                      {fmtPct(d.utilization)}
                    </span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function MoversTable() {
  const movers = useRiskMovers();
  return (
    <div className={styles.panel}>
      <div className={styles.panelTitle}>Day-over-day movers</div>
      <table className={table.table}>
        <thead>
          <tr>
            <th>Desk</th>
            <th className={table.r}>ΔVaR</th>
            <th className={table.r}>Δ%</th>
            <th>Drivers</th>
          </tr>
        </thead>
        <tbody>
          {movers.isError && (
            <tr>
              <td colSpan={4} className={table.dim}>
                Movers unavailable - {movers.error.message}
              </td>
            </tr>
          )}
          {movers.isSuccess && movers.data.rows.length === 0 && (
            <tr>
              <td colSpan={4} className={table.dim}>
                No day-over-day movers{movers.data.prev_date ? "" : " - first run in history"}
              </td>
            </tr>
          )}
          {movers.isSuccess &&
            movers.data.rows.map((r) => (
              <tr key={r.desk_code}>
                <td>{r.desk_code}</td>
                <td className={`${table.r} num`}>
                  {r.delta_usd >= 0 ? "+" : ""}
                  {fmtMoney(r.delta_usd)}
                </td>
                <td className={`${table.r} num`}>{fmtSignedPct(r.delta_pct)}</td>
                <td className={styles.drivers}>{r.drivers.join(" · ")}</td>
              </tr>
            ))}
        </tbody>
      </table>
    </div>
  );
}

export default function Overview() {
  const summary = useRiskSummary();
  const history = useRiskHistory("FIRM", 90);

  if (summary.isPending) {
    // mirrors the loaded layout so nothing shifts when data lands
    return (
      <>
        <div className={styles.tiles}>
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} height={108} />
          ))}
        </div>
        <div className={styles.panel}>
          <div className={styles.panelTitle}>Limits &amp; utilization</div>
          <Skeleton height={160} />
        </div>
        <div className={styles.panel}>
          <div className={styles.panelTitle}>Day-over-day movers</div>
          <Skeleton height={128} />
        </div>
        <div className={styles.panel}>
          <div className={styles.panelTitle}>Firm P&amp;L vs -VaR, trailing 90 days</div>
          <Skeleton height={300} />
        </div>
      </>
    );
  }
  if (summary.isError) throw summary.error;

  const firm = summary.data.desks.find((d) => d.is_aggregate);
  const points = history.data?.points ?? [];
  return (
    <>
      <StaleBanner resolved={summary.data.as_of} />
      {firm && <Tiles firm={firm} points={points} />}
      <LimitHeatmap desks={summary.data.desks} />
      <MoversTable />
      <div className={styles.panel}>
        <div className={styles.panelTitle}>Firm P&amp;L vs -VaR, trailing 90 days</div>
        {history.isError ? (
          <p className={`${table.dim}`} style={{ padding: "0 12px 12px" }}>
            History unavailable - {history.error.message}
          </p>
        ) : history.isSuccess ? (
          <>
            <EChart option={pnlVsVarOption(points)} height={300} />
            <details className={styles.chartData}>
              <summary>Data table</summary>
              <table className={table.table}>
                <thead>
                  <tr>
                    <th>Date</th>
                    <th className={table.r}>P&amp;L</th>
                    <th className={table.r}>-VaR (HS)</th>
                    <th className={table.r}>-VaR (FHS)</th>
                    <th>Exception</th>
                  </tr>
                </thead>
                <tbody>
                  {points.map((p) => (
                    <tr key={p.date}>
                      <td className="num">{p.date}</td>
                      <td className={`${table.r} num`}>{fmtMoney(p.pnl)}</td>
                      <td className={`${table.r} num`}>
                        {p.var_hs == null ? "-" : fmtMoney(-p.var_hs)}
                      </td>
                      <td className={`${table.r} num`}>
                        {p.var_fhs == null ? "-" : fmtMoney(-p.var_fhs)}
                      </td>
                      <td>
                        {[p.exception_hs && "HS", p.exception_fhs && "FHS"]
                          .filter(Boolean)
                          .join("+")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          </>
        ) : (
          <Skeleton height={300} />
        )}
      </div>
    </>
  );
}
