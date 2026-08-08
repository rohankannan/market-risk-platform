import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { ApiError, postJson } from "../api/client";
import { useAsOf } from "../api/queries";
import type { WhatIfResult } from "../api/types";
import { Skeleton } from "../components/Skeleton";
import { StaleBanner } from "../components/StaleBanner";
import table from "../components/DataTable.module.css";
import { fmtMoney, fmtMoneyFull } from "../lib/format";
import styles from "./WhatIf.module.css";

const DEBOUNCE_MS = 400;

// scales the user is editing (strings, so "0." and "-" survive typing);
// committed scales feed the query key after the debounce
type Draft = Record<string, string>;

function committedScales(draft: Draft): Record<string, number> {
  const out: Record<string, number> = {};
  for (const [ticker, raw] of Object.entries(draft)) {
    const v = Number(raw);
    if (raw.trim() !== "" && Number.isFinite(v) && v !== 1) out[ticker] = v;
  }
  return out;
}

function useWhatIf(scales: Record<string, number>) {
  const [asOf] = useAsOf();
  return useQuery<WhatIfResult>({
    // identical books dedupe client-side; the server never caches sandbox output
    queryKey: ["/api/v1/whatif", asOf ?? "latest", scales],
    queryFn: () =>
      postJson<WhatIfResult>(
        "/api/v1/whatif",
        { adjustments: Object.entries(scales).map(([ticker, scale]) => ({ ticker, scale })) },
        asOf ? { as_of: asOf } : undefined,
      ),
    staleTime: Infinity,
    placeholderData: (prev) => prev, // keep the last book on screen while typing
  });
}

function Delta({ value }: { value: number | null | undefined }) {
  if (value == null || value === 0) return null;
  return (
    <span
      style={{ color: value > 0 ? "var(--rd-down)" : "var(--rd-up)", marginLeft: 6 }}
      title={fmtMoneyFull(value)}
    >
      {value > 0 ? "+" : ""}
      {fmtMoney(value)}
    </span>
  );
}

export default function WhatIf() {
  const [draft, setDraft] = useState<Draft>({});
  const [scales, setScales] = useState<Record<string, number>>({});
  const timer = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    timer.current = setTimeout(() => setScales(committedScales(draft)), DEBOUNCE_MS);
    return () => clearTimeout(timer.current);
  }, [draft]);

  const result = useWhatIf(scales);

  // the last good result carries the page through errors: a 422 must never
  // cost the user their edits, and the untouched first response is the book
  // of record the editor rows iterate (zeroed rows stay editable)
  const lastGood = useRef<WhatIfResult>();
  const baseline = useRef<WhatIfResult>();
  if (result.data) {
    lastGood.current = result.data;
    baseline.current ??= result.data;
  }
  const data = result.data ?? lastGood.current;
  const book = baseline.current;

  const edited = useMemo(() => Object.keys(committedScales(draft)).length > 0, [draft]);

  if (!data || !book) {
    if (result.isError) throw result.error; // nothing ever loaded
    return (
      <>
        <Skeleton height={72} />
        <div style={{ height: 16 }} />
        <Skeleton height={420} />
      </>
    );
  }

  const byTicker = new Map(data.positions.map((p) => [p.ticker, p]));
  const deskByCode = new Map(data.desks.map((d) => [d.desk_code, d]));
  const refreshing = result.isPlaceholderData || result.isError;
  const inputError =
    result.isError && result.error instanceof ApiError && result.error.status === 422
      ? result.error.detail
      : null;

  const setScale = (ticker: string, value: string) =>
    setDraft((d) => ({ ...d, [ticker]: value }));

  // invalid text snaps back to the committed scale when the field loses focus
  const snapBack = (ticker: string) =>
    setDraft((d) => {
      const raw = d[ticker];
      if (raw === undefined || (raw.trim() !== "" && Number.isFinite(Number(raw)))) return d;
      const next = { ...d };
      delete next[ticker];
      return next;
    });

  const firmBase = book.desks.find((d) => d.is_aggregate);
  const firm = deskByCode.get("FIRM");

  return (
    <>
      <StaleBanner resolved={data.as_of} />
      <div className={styles.badgeRow}>
        <span className={styles.whatifBadge}>WHAT-IF</span>
        <span className={styles.hint}>
          HYPOTHETICAL — scaled book revalued on the run&apos;s scenario set; the official
          numbers are the nightly batch&apos;s
        </span>
        <button
          className={styles.reset}
          onClick={() => {
            setDraft({});
            setScales({});
          }}
          disabled={!edited}
        >
          RESET BOOK
        </button>
      </div>

      <div className={`${styles.tiles} ${refreshing ? styles.stale : ""}`}>
        <div className={styles.tile}>
          <div className={styles.tileLabel}>FIRM 1D VAR 99 — WHAT-IF</div>
          <div
            className={styles.tileValue}
            style={{ color: "var(--rd-gold)" }}
            title={firm ? fmtMoneyFull(firm.var_hs_1d) : undefined}
          >
            {firm ? fmtMoney(firm.var_hs_1d) : "—"}
          </div>
          <div className={styles.tileDelta} style={{ color: "var(--rd-muted)" }}>
            official {fmtMoney(firm?.official_var_hs_1d ?? firmBase?.official_var_hs_1d)}
            <Delta value={firm?.var_delta} />
          </div>
        </div>
        {book.desks
          .filter((d) => !d.is_aggregate)
          .map((base) => {
            const d = deskByCode.get(base.desk_code);
            return (
              <div key={base.desk_code} className={styles.tile}>
                <div className={styles.tileLabel}>{base.desk_code} 1D VAR 99</div>
                <div
                  className={styles.tileValue}
                  title={d ? fmtMoneyFull(d.var_hs_1d) : undefined}
                >
                  {d ? fmtMoney(d.var_hs_1d) : "ZEROED"}
                </div>
                <div className={styles.tileDelta} style={{ color: "var(--rd-muted)" }}>
                  official {fmtMoney(base.official_var_hs_1d)}
                  <Delta value={d?.var_delta} />
                </div>
              </div>
            );
          })}
      </div>

      <div className={styles.panel}>
        <div className={styles.panelTitle}>BOOK — SCALE POSITIONS</div>
        {inputError && <p className={styles.error}>NOT REVALUED — {inputError}</p>}
        {result.isError && !inputError && (
          <p className={styles.error}>What-if unavailable — {result.error.message}</p>
        )}
        <table className={`${table.table} ${refreshing ? styles.stale : ""}`}>
          <thead>
            <tr>
              <th>Ticker</th>
              <th>Desk</th>
              <th className={table.r}>Scale</th>
              <th></th>
              <th className={table.r}>Quantity</th>
              <th className={table.r}>Standalone VaR</th>
              <th className={table.r}>Component ES</th>
              <th className={table.r}>Marginal VaR</th>
            </tr>
          </thead>
          <tbody>
            {book.positions.map((base) => {
              const p = byTicker.get(base.ticker);
              const zeroed = !p;
              return (
                <tr key={base.ticker} className={zeroed ? styles.stale : undefined}>
                  <td className="num" style={{ color: "var(--rd-gold)", fontWeight: 700 }}>
                    {base.ticker}
                  </td>
                  <td className={table.dim}>{base.desk_code}</td>
                  <td className={table.r}>
                    <input
                      className={`${styles.scaleInput} num`}
                      aria-label={`Scale ${base.ticker}`}
                      value={draft[base.ticker] ?? String(p?.scale ?? 0)}
                      onChange={(e) => setScale(base.ticker, e.target.value)}
                      onBlur={() => snapBack(base.ticker)}
                    />
                  </td>
                  <td>
                    <button
                      className={styles.quick}
                      aria-label={`Zero ${base.ticker}`}
                      onClick={() => setScale(base.ticker, "0")}
                    >
                      ×0
                    </button>
                    <button
                      className={styles.quick}
                      aria-label={`Flip ${base.ticker}`}
                      onClick={() => setScale(base.ticker, "-1")}
                    >
                      ±
                    </button>
                    <button
                      className={styles.quick}
                      aria-label={`Restore ${base.ticker}`}
                      onClick={() => setScale(base.ticker, "1")}
                    >
                      1
                    </button>
                  </td>
                  <td className={`${table.r} num`}>
                    {zeroed
                      ? "0"
                      : p.quantity.toLocaleString("en-US", { maximumFractionDigits: 0 })}
                  </td>
                  <td
                    className={`${table.r} num`}
                    title={zeroed ? undefined : fmtMoneyFull(p.standalone_var)}
                  >
                    {zeroed ? "—" : fmtMoney(p.standalone_var)}
                  </td>
                  <td
                    className={`${table.r} num`}
                    title={zeroed ? undefined : fmtMoneyFull(p.component_es)}
                  >
                    {zeroed ? "—" : fmtMoney(p.component_es)}
                  </td>
                  <td
                    className={`${table.r} num`}
                    title={zeroed ? undefined : fmtMoneyFull(p.marginal_var)}
                  >
                    {zeroed ? "—" : fmtMoney(p.marginal_var)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {data.zeroed.length > 0 && (
          <p className={styles.hint} style={{ padding: "8px 12px" }}>
            ZEROED: {data.zeroed.join(", ")} — restore with the scale input or the 1 button
          </p>
        )}
      </div>
    </>
  );
}
