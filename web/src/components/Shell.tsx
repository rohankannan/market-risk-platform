import { NavLink, Outlet, useSearchParams } from "react-router-dom";

import { useDemoMode } from "../api/client";
import { useAsOf, useMeta } from "../api/queries";
import styles from "./Shell.module.css";

const DESK_COLOR: Record<string, string> = {
  RATES: "var(--desk-rates)",
  FX: "var(--desk-fx)",
  EQUITY: "var(--desk-equity)",
};

function AsOfBadge() {
  const meta = useMeta();
  if (!meta.data?.latest_as_of) return <span className={styles.badge}>—</span>;
  const { latest_as_of, batch_type, batch_completed_at } = meta.data;
  // completed_at arrives with the batch host's offset; report it in UTC
  const at = batch_completed_at
    ? ` · batch ${new Date(batch_completed_at).toISOString().slice(11, 16)} UTC`
    : "";
  return (
    <span className={styles.badge}>
      Data as of <span className="num">{latest_as_of}</span> {batch_type}
      {at}
    </span>
  );
}

function AsOfSelect() {
  const meta = useMeta();
  const [asOf, setAsOf] = useAsOf();
  const dates = [...(meta.data?.available_dates ?? [])].reverse();
  return (
    <select
      className={styles.asOfSelect}
      aria-label="As-of date"
      value={asOf ?? ""}
      onChange={(e) => setAsOf(e.target.value || null)}
    >
      <option value="">latest</option>
      {dates.map((d) => (
        <option key={d} value={d}>
          {d}
        </option>
      ))}
    </select>
  );
}

export function Shell() {
  const meta = useMeta();
  const demo = useDemoMode();
  const [search] = useSearchParams();
  const suffix = search.toString() ? `?${search.toString()}` : "";
  const desks = (meta.data?.desks ?? []).filter((d) => !d.is_aggregate);
  const [asOf] = useAsOf();

  return (
    <div className={styles.layout}>
      <aside className={styles.sidebar}>
        <div className={styles.brand}>RiskDesk</div>
        <nav className={styles.nav} aria-label="Main">
          <NavLink to={`/${suffix}`} end>
            Overview
          </NavLink>
          <span className={styles.navGroup}>Desks</span>
          {desks.map((d) => (
            <NavLink
              key={d.desk_code}
              className={styles.deskLink}
              to={`/desks/${d.desk_code}${suffix}`}
            >
              <span
                className={styles.deskDot}
                style={{ background: DESK_COLOR[d.desk_code] ?? "var(--text-dim)" }}
              />
              {d.desk_name}
            </NavLink>
          ))}
          <NavLink to={`/backtesting${suffix}`}>Backtesting</NavLink>
          <NavLink to={`/scenarios${suffix}`}>Scenarios</NavLink>
          <NavLink to={`/docs${suffix}`}>Model Doc</NavLink>
        </nav>
      </aside>
      <header className={styles.topbar}>
        <span>
          <AsOfBadge />
          {demo && <span className={styles.demoBadge}>demo snapshot</span>}
        </span>
        <AsOfSelect />
      </header>
      <main className={styles.main}>
        <Outlet />
      </main>
      <footer className={styles.footer}>
        <span className="num">{meta.data?.code_version ?? "—"}</span>
        <span className="num">as of {asOf ?? meta.data?.latest_as_of ?? "—"}</span>
      </footer>
    </div>
  );
}
