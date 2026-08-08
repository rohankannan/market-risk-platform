import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import {
  useBacktestSummary,
  useDeskPositions,
  useFactorsLatest,
  usePla,
  useRiskExposures,
  useRiskHistory,
  useRiskMovers,
  useRiskSummary,
  useScenarioResults,
} from "../api/queries";
import type { DeskRisk, HistoryPoint } from "../api/types";
import { Skeleton } from "../components/Skeleton";
import { StaleBanner } from "../components/StaleBanner";
import table from "../components/DataTable.module.css";
import { fmtMoney, fmtMoneyFull } from "../lib/format";
import {
  buildBook,
  buildTerminalChart,
  CHART_H,
  CHART_W,
  fmtNotional,
} from "../lib/terminal";
import styles from "./Overview.module.css";

const WINDOW = 250;
const WARN_UTIL = 0.8;

const GOLD = "#cfb991";
const GOLD_RUSH = "#daaa00";
const UP = "#5fbf7a";
const DOWN = "#e0563f";
const TEXT = "#e8e4da";

function barColor(util: number): string {
  if (util >= 1) return DOWN;
  if (util >= WARN_UTIL) return GOLD_RUSH;
  return GOLD;
}

function LimitRows({ desks }: { desks: DeskRisk[] }) {
  const [search] = useSearchParams();
  const suffix = search.toString() ? `?${search.toString()}` : "";
  const ordered = [...desks].sort((a, b) => Number(b.is_aggregate) - Number(a.is_aggregate));
  return (
    <>
      {ordered.map((d) => {
        const util = d.utilization;
        return (
          <div key={d.desk_code} className={styles.limitRow}>
            <div className={styles.limitTop}>
              <span
                className={`${styles.limitName} ${d.is_aggregate ? styles.limitFirm : ""}`}
              >
                {d.is_aggregate ? (
                  "FIRM"
                ) : (
                  <Link
                    to={`/desks/${d.desk_code}${suffix}`}
                    style={{ color: "inherit", textDecoration: "none" }}
                  >
                    {d.desk_name.toUpperCase()}
                  </Link>
                )}
              </span>
              <span className={styles.limitVals}>
                {fmtMoney(d.var_hs_1d)}{" "}
                <span style={{ color: "var(--rd-muted)" }}>/ {fmtMoney(d.limit_value)}</span>
              </span>
            </div>
            {util != null && (
              <div className={styles.limitBarRow}>
                <div className={styles.limitTrack}>
                  <div
                    className={styles.limitFill}
                    style={{
                      width: `${Math.min(util * 100, 100)}%`,
                      background: d.limit_status === "BREACH" ? DOWN : barColor(util),
                    }}
                  />
                </div>
                <span
                  className={styles.limitPct}
                  style={{ color: d.limit_status === "BREACH" ? DOWN : barColor(util) }}
                >
                  {Math.round(util * 100)}%
                </span>
              </div>
            )}
          </div>
        );
      })}
    </>
  );
}

function FirmTiles({ firm, div, lastPnl }: { firm: DeskRisk; div: number | null; lastPnl: HistoryPoint | undefined }) {
  const pnl = lastPnl?.pnl ?? null;
  const hadException = lastPnl?.exception_hs || lastPnl?.exception_fhs;
  const tiles = [
    {
      k: "1D VAR 99 (HS)",
      v: fmtMoney(firm.var_hs_1d),
      sub:
        firm.utilization != null && firm.limit_value != null
          ? `${Math.round(firm.utilization * 100)}% of ${fmtMoney(firm.limit_value)} limit`
          : "",
      color: GOLD,
      title: fmtMoneyFull(firm.var_hs_1d),
    },
    {
      k: "ES 97.5",
      v: fmtMoney(firm.es_975_1d),
      sub: `stressed ${fmtMoney(firm.es_stressed_1d)}`,
      color: TEXT,
      title: fmtMoneyFull(firm.es_975_1d),
    },
    {
      k: "DIVERSIFICATION",
      v: div == null ? "-" : `−${Math.round(div * 100)}%`,
      sub: "vs sum of desks",
      color: UP,
      title: undefined,
    },
    {
      k: "CLEAN P&L (T−1)",
      v: fmtMoney(pnl),
      sub: hadException ? "exception" : "no exception",
      color: pnl == null ? TEXT : pnl >= 0 ? UP : DOWN,
      title: pnl == null ? undefined : fmtMoneyFull(pnl),
    },
  ];
  return (
    <div className={styles.tiles}>
      {tiles.map((t) => (
        <div key={t.k} className={styles.tile}>
          <div className={styles.tileLabel}>{t.k}</div>
          <div className={styles.tileValue} style={{ color: t.color }} title={t.title}>
            {t.v}
          </div>
          <div className={styles.tileSub}>{t.sub}</div>
        </div>
      ))}
    </div>
  );
}

function Movers({ desks }: { desks: DeskRisk[] }) {
  const movers = useRiskMovers();
  const names = new Map(desks.map((d) => [d.desk_code, d.desk_name]));
  if (!movers.isSuccess) return null;
  if (!movers.data.rows.length) {
    return <div className={styles.quiet}>NO DAY-OVER-DAY MOVERS</div>;
  }
  return (
    <>
      {movers.data.rows.map((m) => (
        <div key={m.desk_code} className={styles.moverRow}>
          <span className={styles.moverDesk}>
            {(names.get(m.desk_code) ?? m.desk_code).toUpperCase()}
          </span>{" "}
          <span style={{ color: m.delta_usd > 0 ? DOWN : m.delta_usd < 0 ? UP : "var(--rd-muted)" }}>
            {m.delta_usd > 0 ? "+" : ""}
            {fmtMoney(m.delta_usd)}
          </span>
          <span className={styles.moverDriver}> — {m.drivers.join("; ")}</span>
        </div>
      ))}
    </>
  );
}

function CenterChart() {
  const [method, setMethod] = useState<"HS" | "FHS">("HS");
  const history = useRiskHistory("FIRM", WINDOW);
  const backtest = useBacktestSummary("FIRM", method, WINDOW);
  const pla = usePla("FIRM", WINDOW);

  const points = history.data?.points ?? [];
  const chart = buildTerminalChart(points, method);

  return (
    <section className={styles.center} aria-label="Firm clean P&L vs 1d VaR">
      <div className={styles.chartTitleRow}>
        <span className={styles.chartTitle}>FIRM — CLEAN P&amp;L VS 1D VAR 99</span>
        <span className={styles.chartMeta}>
          {points.length ? `${points.length} TRADING DAYS` : ""}
        </span>
      </div>
      <div className={styles.tabRow}>
        {(["HS", "FHS"] as const).map((m) => (
          <button
            key={m}
            className={`${styles.tab} ${method === m ? styles.tabActive : ""}`}
            onClick={() => setMethod(m)}
            aria-pressed={method === m}
          >
            {m === "HS" ? "HISTORICAL SIM" : "FILTERED HS (EWMA)"}
          </button>
        ))}
        <div className={styles.legend}>
          <span>
            <span className={styles.swatch} style={{ background: GOLD }} />
            −VAR BAND
          </span>
          <span>
            <span className={styles.swatch} style={{ background: DOWN }} />
            EXCEPTION
          </span>
        </div>
      </div>
      <div className={styles.chartBox}>
        {history.isSuccess ? (
          <svg
            viewBox={`0 0 ${CHART_W} ${CHART_H}`}
            preserveAspectRatio="none"
            className={styles.chartSvg}
            role="img"
            aria-label={`Daily clean P&L against the ${method} value-at-risk band; data table below`}
          >
            <line x1="0" y1="160" x2={CHART_W} y2="160" stroke="#2a2822" strokeWidth="1" />
            <line x1="0" y1="80" x2={CHART_W} y2="80" stroke="#1c1a16" strokeWidth="1" />
            <line x1="0" y1="240" x2={CHART_W} y2="240" stroke="#1c1a16" strokeWidth="1" />
            {chart.bars.map((b, i) => (
              <rect
                key={i}
                x={b.x - 1.2}
                y={b.y}
                width={2.4}
                height={b.h}
                fill={
                  b.exception
                    ? DOWN
                    : b.positive
                      ? "rgba(95,191,122,0.55)"
                      : "rgba(232,228,218,0.35)"
                }
              />
            ))}
            <path d={chart.varPath} fill="none" stroke={GOLD} strokeWidth="1.6" />
            <path
              d={chart.varPathNeg}
              fill="none"
              stroke={GOLD}
              strokeWidth="1.6"
              strokeDasharray="4 3"
              opacity="0.5"
            />
            {chart.dots.map((d, i) => (
              <circle key={i} cx={d.cx} cy={d.cy} r="3.2" fill={DOWN} />
            ))}
          </svg>
        ) : (
          <Skeleton height={280} />
        )}
        <div className={styles.axisLabel} style={{ top: 8 }}>
          +2.0M
        </div>
        <div className={styles.axisLabel} style={{ bottom: 8 }}>
          −2.0M
        </div>
      </div>
      <div className={styles.statsRow}>
        {backtest.isSuccess ? (
          <>
            <div className={styles.stat}>
              <div className={styles.statLabel}>EXCEPTIONS</div>
              <div className={styles.statValue}>
                {backtest.data.n_exceptions} / {backtest.data.n_obs}
              </div>
            </div>
            <div className={styles.stat}>
              <div className={styles.statLabel}>EXPECTED</div>
              <div className={styles.statValue} style={{ color: "var(--rd-muted)" }}>
                {backtest.data.expected_exceptions}
              </div>
            </div>
            <div className={styles.stat}>
              <div className={styles.statLabel}>KUPIEC P</div>
              <div className={styles.statValue}>{backtest.data.kupiec.p_value.toFixed(2)}</div>
            </div>
            <div className={styles.stat}>
              <div className={styles.statLabel}>CHRISTOFFERSEN CC</div>
              <div className={styles.statValue}>
                {backtest.data.christoffersen_cc.p_value.toFixed(2)}
              </div>
            </div>
            <div className={styles.stat}>
              <div className={styles.statLabel}>BASEL ZONE (250D)</div>
              <div
                className={styles.statValue}
                style={{
                  color:
                    backtest.data.traffic_light.zone === "GREEN"
                      ? UP
                      : backtest.data.traffic_light.zone === "AMBER"
                        ? GOLD_RUSH
                        : DOWN,
                }}
              >
                {backtest.data.traffic_light.zone}
              </div>
            </div>
            <div className={styles.stat}>
              <div className={styles.statLabel}>PLA (SPEARMAN/KS)</div>
              <div
                className={styles.statValue}
                style={{
                  color: !pla.isSuccess
                    ? "var(--rd-muted)"
                    : pla.data.zone === "GREEN"
                      ? UP
                      : pla.data.zone === "AMBER"
                        ? GOLD_RUSH
                        : DOWN,
                }}
              >
                {pla.isSuccess ? pla.data.zone : "—"}
              </div>
            </div>
          </>
        ) : (
          <div className={styles.stat}>
            <div className={styles.statLabel}>BACKTEST</div>
            <div className={styles.statValue} style={{ color: "var(--rd-muted)" }}>
              {backtest.isError ? "UNAVAILABLE" : "…"}
            </div>
          </div>
        )}
      </div>
      {history.isSuccess && (
        <details className={table.chartData}>
          <summary>Data table</summary>
          <table className={table.table}>
            <thead>
              <tr>
                <th>Date</th>
                <th className={table.r}>P&amp;L</th>
                <th className={table.r}>-VaR ({method})</th>
                <th>Exception</th>
              </tr>
            </thead>
            <tbody>
              {points.map((p) => {
                const v = method === "FHS" ? p.var_fhs : p.var_hs;
                const exc = method === "FHS" ? p.exception_fhs : p.exception_hs;
                return (
                  <tr key={p.date}>
                    <td className="num">{p.date}</td>
                    <td className={`${table.r} num`}>{fmtMoney(p.pnl)}</td>
                    <td className={`${table.r} num`}>{v == null ? "-" : fmtMoney(-v)}</td>
                    <td>{exc ? method : ""}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </details>
      )}
    </section>
  );
}

function StressRail() {
  const results = useScenarioResults();
  const [search] = useSearchParams();
  if (!results.isSuccess) return null;
  const rows = results.data.results;
  const maxAbs = Math.max(...rows.map((r) => Math.abs(r.firm_impact ?? 0)), 1);
  return (
    <>
      <div className={styles.sectionHead}>
        <span className={styles.sectionTitle}>STRESS — FIRM P&amp;L</span>
        <span className={styles.sectionMeta}>WORST FIRST</span>
      </div>
      {rows.map((r) => {
        const pl = r.firm_impact ?? 0;
        const color = pl < 0 ? DOWN : UP;
        const drillParams = new URLSearchParams(search);
        drillParams.set("drill", r.scenario_code);
        return (
          <Link
            key={r.scenario_code}
            to={`/scenarios?${drillParams.toString()}`}
            className={styles.stressRow}
            style={{ display: "block", textDecoration: "none", color: "inherit" }}
          >
            <div className={styles.stressTop}>
              <span className={styles.stressName}>{r.scenario_name.toUpperCase()}</span>
              <span className={styles.stressPl} style={{ color }} title={fmtMoneyFull(pl)}>
                {fmtMoney(pl)}
              </span>
            </div>
            <div className={styles.stressBarRow}>
              <div className={styles.stressTrack}>
                <div
                  className={styles.stressFill}
                  style={{ width: `${(Math.abs(pl) / maxAbs) * 100}%`, background: color }}
                />
              </div>
              <span className={styles.stressType}>
                {r.scenario_type === "HISTORICAL_REPLAY" ? "HISTORICAL" : "HYPOTHETICAL"}
              </span>
            </div>
          </Link>
        );
      })}
    </>
  );
}

function BookRail() {
  const equity = useDeskPositions("EQUITY");
  const fx = useDeskPositions("FX");
  const rates = useDeskPositions("RATES");
  const factors = useFactorsLatest();
  const exposures = useRiskExposures();

  const ready =
    equity.isSuccess && fx.isSuccess && rates.isSuccess && factors.isSuccess;
  const levels = Object.fromEntries(
    (factors.data?.ticks ?? []).map((t) => [t.factor_code, t.level]),
  );
  const rows = ready
    ? buildBook(
        {
          EQUITY: equity.data.positions,
          FX: fx.data.positions,
          RATES: rates.data.positions,
        },
        levels,
      )
    : [];

  const ratesDv01 = (exposures.data?.rows ?? [])
    .filter((r) => r.desk_code === "RATES")
    .reduce((s, r) => s + r.value, 0);

  return (
    <>
      <div className={styles.sectionHeadThin} style={{ borderTop: "1px solid var(--rd-divider)" }}>
        <span className={styles.sectionTitle}>BOOK — 3 DESKS</span>
      </div>
      <div className={styles.bookScroll}>
        {rows.length ? (
          rows.map((b) => (
            <div key={b.ticker} className={styles.bookRow}>
              <span className={styles.bookTicker}>{b.ticker}</span>
              <span className={styles.bookDesk}>{b.desk}</span>
              <span
                style={{
                  color:
                    b.notional == null ? GOLD_RUSH : b.notional < 0 ? DOWN : TEXT,
                }}
                title={
                  b.notional == null
                    ? `level unavailable — ${b.factorCode ?? "unmapped factor"}`
                    : undefined
                }
              >
                {fmtNotional(b)}
              </span>
            </div>
          ))
        ) : (
          <div className={styles.quiet}>BOOK UNAVAILABLE FOR THIS RUN</div>
        )}
      </div>
      <div className={styles.dv01Strip}>
        <span className={styles.dv01Label}>NET DV01 (RATES)</span>
        <span className={styles.dv01Value}>
          {exposures.isSuccess && ratesDv01
            ? `$${Math.round(Math.abs(ratesDv01) / 1000)}K / BP`
            : "—"}
        </span>
      </div>
    </>
  );
}

export default function Overview() {
  const summary = useRiskSummary();
  const history = useRiskHistory("FIRM", WINDOW);

  if (summary.isPending) {
    return (
      <div className={styles.grid}>
        <div className={styles.left}>
          <Skeleton height={400} />
        </div>
        <div className={styles.center}>
          <Skeleton height={400} />
        </div>
        <div className={styles.right}>
          <Skeleton height={400} />
        </div>
      </div>
    );
  }
  if (summary.isError) throw summary.error;

  const firm = summary.data.desks.find((d) => d.is_aggregate);
  const lastPoint = history.data?.points.at(-1);

  return (
    <>
      <StaleBanner resolved={summary.data.as_of} />
      <div className={styles.grid}>
        <section className={styles.left} aria-label="Limits and firm measures">
          <div className={styles.sectionHead}>
            <span className={styles.sectionTitle}>LIMIT UTILIZATION</span>
            <span className={styles.sectionMeta}>1D VAR 99 / HS</span>
          </div>
          <LimitRows desks={summary.data.desks} />
          <div className={styles.noteStrip}>
            WARN THRESHOLD {Math.round(WARN_UTIL * 100)}% — ALL DESKS
          </div>
          <div className={styles.sectionHeadThin}>
            <span className={styles.sectionTitle}>FIRM MEASURES</span>
          </div>
          {firm && (
            <FirmTiles
              firm={firm}
              div={summary.data.diversification_benefit}
              lastPnl={lastPoint}
            />
          )}
          <div className={styles.sectionHeadThin}>
            <span className={styles.sectionTitle}>VAR MOVERS — DAY/DAY</span>
          </div>
          <Movers desks={summary.data.desks} />
        </section>

        <CenterChart />

        <section className={styles.right} aria-label="Stress and book">
          <StressRail />
          <BookRail />
        </section>
      </div>
    </>
  );
}
