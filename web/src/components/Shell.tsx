import { NavLink, Outlet, useLocation, useSearchParams } from "react-router-dom";

import { useDemoMode } from "../api/client";
import { useAsOf, useFactorsLatest, useMeta } from "../api/queries";
import type { FactorTick } from "../api/types";
import { sameCommit } from "../lib/build";
import styles from "./Shell.module.css";

const FACTOR_COUNT = 17; // the committed snapshot's factor universe
const TABLE_COUNT = 18; // 15 (initial schema) + risk_exposures + data_revisions + position_components

// header wording per surface convention; the API's PARTIAL passes through
const BATCH_LABEL: Record<string, string> = {
  SUCCESS: "COMPLETE",
  not_yet_run: "PENDING",
};

// tape symbols read like a terminal: "UST 2Y", "EQ.SPY", "VIX 30D"
function tapeSymbol(code: string): string {
  if (code.startsWith("IR.UST.")) return `UST ${code.split(".")[2]}`;
  if (code === "VOL.SPX.IV30") return "VIX 30D";
  return code;
}

function tapeLevel(t: FactorTick): string {
  if (t.level < 0.01) return t.level.toFixed(5);
  return t.level.toLocaleString("en-US", {
    minimumFractionDigits: t.level < 2 ? 4 : 2,
    maximumFractionDigits: t.level < 2 ? 4 : 2,
  });
}

function tapeChange(t: FactorTick): string | null {
  if (t.change == null) return null;
  if (t.unit === "bp") return `${t.change >= 0 ? "+" : ""}${t.change.toFixed(2)}bp`;
  if (t.unit === "pt") return `${t.change >= 0 ? "+" : ""}${t.change.toFixed(2)}pt`;
  return `${t.change >= 0 ? "+" : ""}${(t.change * 100).toFixed(2)}%`;
}

function FactorTape() {
  const factors = useFactorsLatest();
  if (!factors.isSuccess || !factors.data.ticks.length) return null;
  const ticks = factors.data.ticks;
  // content duplicated 2x so the -50% marquee loops seamlessly; the moving
  // copy is decorative - the same levels are served as a static list below
  const cells = [...ticks, ...ticks];
  return (
    <>
      <div className={styles.tape} aria-hidden>
        <div className={styles.tapeTrack}>
          {cells.map((t, i) => (
            <div key={`${t.factor_code}-${i}`} className={styles.tapeCell}>
              <span className={styles.tapeSym}>{tapeSymbol(t.factor_code)}</span>
              <span>{tapeLevel(t)}</span>
              {t.change != null && (
                <span className={t.change >= 0 ? styles.tapeUp : styles.tapeDown}>
                  {tapeChange(t)}
                </span>
              )}
            </div>
          ))}
        </div>
      </div>
      <ul className={styles.srOnly} aria-label="Factor levels">
        {ticks.map((t) => (
          <li key={t.factor_code}>
            {tapeSymbol(t.factor_code)} {tapeLevel(t)}
            {t.change != null ? ` ${tapeChange(t)}` : ""}
          </li>
        ))}
      </ul>
    </>
  );
}

function Logo() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" style={{ flexShrink: 0 }} aria-hidden>
      <rect width="24" height="24" fill="#daaa00" />
      <path d="M3 21 L12 3 L21 21 Z" fill="#0a0a09" />
      <path d="M8 21 L12 13 L16 21 Z" fill="#daaa00" />
    </svg>
  );
}

// DESKS resolves to the first booked desk from /meta - the desk page's own
// tab strip switches between them
const NAV = [
  { to: "/", label: "OVERVIEW", end: true },
  { to: null, label: "DESKS", end: false },
  { to: "/scenarios", label: "SCENARIOS", end: false },
  { to: "/backtesting", label: "BACKTESTING", end: false },
  { to: "/whatif", label: "WHAT-IF", end: false },
  { to: "/docs", label: "MODEL DOC", end: false },
];

export function Shell() {
  const meta = useMeta();
  const demo = useDemoMode();
  const [search] = useSearchParams();
  const location = useLocation();
  const suffix = search.toString() ? `?${search.toString()}` : "";
  const [asOf, setAsOf] = useAsOf();
  const dates = [...(meta.data?.available_dates ?? [])].reverse();

  const batchStatus = meta.data?.batch_status;
  // the footer stamps the RESOLVED run, not the pin: a pinned date between
  // runs resolves to the latest run at or before it (resolve_run's rule,
  // re-derived here from the served date catalog)
  const resolvedAsOf = asOf
    ? (dates.find((d) => d <= asOf) ?? "—")
    : (meta.data?.latest_as_of ?? "—");
  // the footer stamps the RUNNING build, falling back to the batch's commit for
  // snapshot responses recorded before build_version existed
  const buildStamp = meta.data?.build_version ?? meta.data?.code_version ?? "—";
  const buildMatchesBatch = sameCommit(meta.data?.build_version, meta.data?.code_version);
  const onDesks = location.pathname.startsWith("/desks");
  const firstDesk = (meta.data?.desks ?? []).find((d) => !d.is_aggregate)?.desk_code;

  return (
    <div className={styles.layout}>
      <header className={styles.header}>
        <div className={styles.logoBlock}>
          <Logo />
          <div>
            <div className={styles.wordmark}>
              RISKDESK<span className={styles.wordmarkCaret}>_</span>
            </div>
            <div className={styles.subline}>MARKET RISK · EOD</div>
          </div>
          <span className={styles.prod}>PROD</span>
        </div>
        <nav className={styles.nav} aria-label="Main">
          {NAV.map((n) => (
            <NavLink
              key={n.label}
              to={`${n.to ?? `/desks/${firstDesk ?? "RATES"}`}${suffix}`}
              end={n.end}
              className={({ isActive }) =>
                (n.label === "DESKS" ? onDesks : isActive) ? styles.active : ""
              }
            >
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className={styles.headerMeta}>
          {demo && <span className={styles.demoBadge}>DEMO SNAPSHOT</span>}
          <span>
            EOD BATCH{" "}
            <span className={batchStatus === "SUCCESS" ? styles.statusOk : styles.statusWarn}>
              {batchStatus ? (BATCH_LABEL[batchStatus] ?? batchStatus) : "—"}
            </span>
          </span>
          <span>
            AS OF{" "}
            <select
              className={styles.asOfSelect}
              aria-label="As-of date"
              value={asOf ?? ""}
              onChange={(e) => setAsOf(e.target.value || null)}
            >
              <option value="">{meta.data?.latest_as_of ?? "latest"}</option>
              {dates.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
          </span>
        </div>
      </header>

      <FactorTape />

      <main className={location.pathname === "/" ? styles.main : styles.mainPadded}>
        <Outlet />
      </main>

      <footer className={styles.footer}>
        <span>
          RISKDESK {buildStamp} ·{" "}
          {/* the batch's commit shows only when it differs from the running
              build - stated, not flagged. The two legitimately diverge between
              a deploy and the next nightly, so styling this as a fault would
              light it up on every ordinary weekday. Deciding which side is
              BEHIND needs git history, which the scheduled parity check has
              and the browser does not. */}
          {!buildMatchesBatch && <>BATCH {meta.data?.code_version} · </>}
          SNAPSHOT {resolvedAsOf} · {FACTOR_COUNT} FACTORS · {TABLE_COUNT} TABLES
        </span>
        <span>NEXT EOD BATCH 18:30 ET</span>
      </footer>
    </div>
  );
}
