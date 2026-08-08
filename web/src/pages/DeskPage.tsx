import { useMemo, useState } from "react";
import { NavLink, useParams, useSearchParams } from "react-router-dom";

import {
  useDeskDecomposition,
  useDeskPositions,
  useMeta,
  useRiskHistory,
} from "../api/queries";
import type { DeskDecomposition, DeskPosition } from "../api/types";
import { EChart } from "../components/EChart";
import { HistoryDataTable } from "../components/HistoryDataTable";
import { Skeleton } from "../components/Skeleton";
import { StaleBanner } from "../components/StaleBanner";
import table from "../components/DataTable.module.css";
import {
  DESK_HEX,
  exposureBarsOption,
  exposureGroups,
  FACTOR_CLASS_LABEL,
  waterfallOption,
} from "../lib/desk";
import { fmtMoney, fmtMoneyFull, fmtPct } from "../lib/format";
import { pnlVsVarOption } from "../lib/overview";
import styles from "./DeskPage.module.css";

const HISTORY_WINDOW = 60;

type SortKey = keyof Pick<
  DeskPosition,
  "ticker" | "instrument_type" | "quantity" | "standalone_var" | "component_es" | "marginal_var" | "pct_of_desk"
>;

const COLUMNS: { key: SortKey; label: string; numeric: boolean }[] = [
  { key: "ticker", label: "Ticker", numeric: false },
  { key: "instrument_type", label: "Type", numeric: false },
  { key: "quantity", label: "Quantity", numeric: true },
  { key: "standalone_var", label: "Standalone VaR", numeric: true },
  { key: "component_es", label: "Component ES", numeric: true },
  { key: "marginal_var", label: "Marginal VaR", numeric: true },
  { key: "pct_of_desk", label: "% of desk", numeric: true },
];

function PositionsTable({ positions, deskColor }: { positions: DeskPosition[]; deskColor: string }) {
  const [sort, setSort] = useState<{ key: SortKey; asc: boolean }>({
    key: "component_es",
    asc: false,
  });

  const sorted = useMemo(() => {
    // decorate with the index so equal keys keep their existing order
    const rows = positions.map((p, i) => ({ p, i }));
    rows.sort((a, b) => {
      const av = a.p[sort.key];
      const bv = b.p[sort.key];
      let cmp: number;
      if (typeof av === "string" && typeof bv === "string") cmp = av.localeCompare(bv);
      else cmp = ((av as number | null) ?? -Infinity) - ((bv as number | null) ?? -Infinity);
      if (cmp === 0) return a.i - b.i;
      return sort.asc ? cmp : -cmp;
    });
    return rows.map((r) => r.p);
  }, [positions, sort]);

  const toggle = (key: SortKey) =>
    setSort((s) => ({ key, asc: s.key === key ? !s.asc : false }));

  return (
    <table className={table.table}>
      <thead>
        <tr>
          {COLUMNS.map((c) => (
            <th
              key={c.key}
              className={c.numeric ? table.r : undefined}
              aria-sort={
                sort.key === c.key ? (sort.asc ? "ascending" : "descending") : undefined
              }
            >
              <button className={styles.sortHeader} onClick={() => toggle(c.key)}>
                {c.label}
                {sort.key === c.key ? (sort.asc ? " ▲" : " ▼") : ""}
              </button>
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {sorted.map((p) => (
          <tr key={p.ticker}>
            <td className="num">
              {p.ticker}
              {p.instrument_type === "OPTION" && (
                <div className={styles.optionMeta}>
                  {p.option_type} @ {p.moneyness?.toFixed(2)}× spot
                </div>
              )}
            </td>
            <td>{p.instrument_type}</td>
            <td className={`${table.r} num`}>
              {p.quantity.toLocaleString("en-US", { maximumFractionDigits: 0 })}
            </td>
            <td className={`${table.r} num`} title={fmtMoneyFull(p.standalone_var)}>
              {fmtMoney(p.standalone_var)}
            </td>
            <td className={`${table.r} num`} title={fmtMoneyFull(p.component_es)}>
              {fmtMoney(p.component_es)}
            </td>
            <td className={`${table.r} num`} title={fmtMoneyFull(p.marginal_var)}>
              {fmtMoney(p.marginal_var)}
            </td>
            <td className={`${table.r} num`}>
              {p.pct_of_desk == null ? (
                "-"
              ) : (
                <>
                  <span className={styles.microBarTrack} aria-hidden>
                    <span
                      className={styles.microBarFill}
                      style={{
                        display: "block",
                        width: `${Math.min(Math.abs(p.pct_of_desk), 1) * 100}%`,
                        background: p.pct_of_desk >= 0 ? deskColor : "var(--text-dim)",
                      }}
                    />
                  </span>
                  {fmtPct(p.pct_of_desk)}
                </>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function WaterfallPanel({ decomp }: { decomp: DeskDecomposition }) {
  const option = waterfallOption(decomp);
  return (
    <div className={styles.panel}>
      <div className={styles.panelTitle}>VaR decomposition (HS, 1d)</div>
      {option ? (
        <>
          <EChart option={option} height={280} />
          <details className={table.chartData}>
            <summary>Data table</summary>
            <table className={table.table}>
              <tbody>
                {decomp.buckets.map((b) => (
                  <tr key={b.factor_class}>
                    <td>{FACTOR_CLASS_LABEL[b.factor_class] ?? b.factor_class} standalone</td>
                    <td className={`${table.r} num`}>{fmtMoneyFull(b.standalone_var)}</td>
                  </tr>
                ))}
                <tr>
                  <td>Diversification</td>
                  <td className={`${table.r} num`}>{fmtMoneyFull(decomp.diversification)}</td>
                </tr>
                <tr>
                  <td>
                    <strong>Desk VaR</strong>
                  </td>
                  <td className={`${table.r} num`}>
                    <strong>{fmtMoneyFull(decomp.var_hs_1d)}</strong>
                  </td>
                </tr>
              </tbody>
            </table>
          </details>
        </>
      ) : (
        <p className={table.dim} style={{ padding: "0 12px 12px" }}>
          No position decomposition for this run - backfill runs skip the position step.
        </p>
      )}
    </div>
  );
}

function DeskTabs({ current }: { current: string }) {
  const meta = useMeta();
  const [search] = useSearchParams();
  const suffix = search.toString() ? `?${search.toString()}` : "";
  const desks = (meta.data?.desks ?? []).filter((d) => !d.is_aggregate);
  if (!desks.length) return null;
  return (
    <nav className={styles.deskTabs} aria-label="Desks">
      {desks.map((d) => (
        <NavLink
          key={d.desk_code}
          to={`/desks/${d.desk_code}${suffix}`}
          className={d.desk_code === current ? styles.activeTab : undefined}
        >
          <span
            className={styles.tabDot}
            style={{ background: DESK_HEX[d.desk_code] ?? "var(--rd-muted)" }}
          />
          {d.desk_name.toUpperCase()}
        </NavLink>
      ))}
    </nav>
  );
}

export default function DeskPage() {
  const { deskCode = "" } = useParams();
  const code = deskCode.toUpperCase();
  const decomp = useDeskDecomposition(code);
  const positions = useDeskPositions(code);
  const history = useRiskHistory(`desk:${code}`, HISTORY_WINDOW);

  if (decomp.isPending) {
    return (
      <>
        <DeskTabs current={code} />
        <Skeleton height={28} width={320} />
        <div style={{ height: 16 }} />
        <div className={styles.panel}>
          <div className={styles.panelTitle}>VaR decomposition (HS, 1d)</div>
          <Skeleton height={280} />
        </div>
        <div className={styles.panel}>
          <div className={styles.panelTitle}>Positions</div>
          <Skeleton height={200} />
        </div>
      </>
    );
  }
  if (decomp.isError) throw decomp.error;

  const d = decomp.data;
  const deskColor = DESK_HEX[d.desk_code] ?? "var(--text)";
  const groups = exposureGroups(d.exposures);

  return (
    <>
      <DeskTabs current={code} />
      <StaleBanner resolved={d.as_of} />
      <div className={styles.header}>
        <span className={styles.deskDot} style={{ background: deskColor }} />
        <h1>{d.desk_name}</h1>
        <span className={`${styles.headerVar} num`} title={fmtMoneyFull(d.var_hs_1d)}>
          {fmtMoney(d.var_hs_1d)} VaR<sub>99</sub>/1d
        </span>
      </div>

      <WaterfallPanel decomp={d} />

      {groups.length > 0 && (
        <div className={styles.exposures}>
          {groups.map((g) => (
            <div key={g.title} className={styles.panel} style={{ marginBottom: 0 }}>
              <div className={styles.panelTitle}>{g.title}</div>
              <EChart option={exposureBarsOption(g, deskColor)} height={180} />
              <details className={table.chartData}>
                <summary>Data table</summary>
                <table className={table.table}>
                  <tbody>
                    {g.rows.map((r) => (
                      <tr key={r.label}>
                        <td className="num">{r.label}</td>
                        <td className={`${table.r} num`}>{fmtMoneyFull(r.value)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </details>
            </div>
          ))}
        </div>
      )}

      <div className={styles.panel}>
        <div className={styles.panelTitle}>Positions</div>
        {positions.isError ? (
          <p className={table.dim} style={{ padding: "0 12px 12px" }}>
            Positions unavailable - {positions.error.message}
          </p>
        ) : positions.isSuccess && positions.data.positions.length > 0 ? (
          <PositionsTable positions={positions.data.positions} deskColor={deskColor} />
        ) : positions.isSuccess ? (
          <p className={table.dim} style={{ padding: "0 12px 12px" }}>
            No position rows for this run.
          </p>
        ) : (
          <Skeleton height={200} />
        )}
      </div>

      <div className={styles.panel}>
        <div className={styles.panelTitle}>
          Desk P&amp;L vs -VaR, trailing {HISTORY_WINDOW} days
        </div>
        {history.isError ? (
          <p className={table.dim} style={{ padding: "0 12px 12px" }}>
            History unavailable - {history.error.message}
          </p>
        ) : history.isSuccess ? (
          <>
            <EChart option={pnlVsVarOption(history.data.points)} height={260} />
            <HistoryDataTable points={history.data.points} />
          </>
        ) : (
          <Skeleton height={260} />
        )}
      </div>
    </>
  );
}
