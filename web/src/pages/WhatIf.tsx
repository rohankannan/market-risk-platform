import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { ApiError, postJson } from "../api/client";
import { useAsOf, useScenarioCatalog, useScenarioShocks } from "../api/queries";
import type { WhatIfResult, WhatIfShock } from "../api/types";
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

// shocks are edited as text like scales; a preset's full-precision value is
// kept until the user actually types over that factor, so an untouched preset
// reproduces the batch's scenario P&L to the cent
// A factor is shocked unless the user says otherwise. Clearing the field or
// typing 0 means "do not shock this factor" - and the row says so, because a
// blank box that silently keeps applying the preset's move would be a lie.
function committedShocks(loaded: WhatIfShock[], draft: Draft): WhatIfShock[] {
  return loaded
    .map((s) => {
      const raw = draft[s.factor_code];
      if (raw === undefined) return s; // untouched: keep the preset's precision
      const typed = Number(raw);
      if (raw.trim() === "" || !Number.isFinite(typed)) return { ...s, value: 0 };
      return { ...s, value: toWire(s, typed) };
    })
    .filter((s) => s.value !== 0);
}

// -100% wipes the factor out: log1p(-1) is -Infinity and anything past it is
// NaN, and JSON.stringify puts either on the wire as null. Catch it in the
// editor's own units - the server could only answer with a validation error.
function shockUnitError(loaded: WhatIfShock[], draft: Draft): string | null {
  for (const s of loaded) {
    const raw = draft[s.factor_code];
    if (raw === undefined || raw.trim() === "") continue;
    const typed = Number(raw);
    if (Number.isFinite(typed) && !Number.isFinite(toWire(s, typed))) {
      return `${s.factor_code} move ${raw}% - a percent move must be above -100%, ` +
        "a level cannot fall past zero";
    }
  }
  return null;
}

function isDropped(s: WhatIfShock, draft: Draft): boolean {
  const raw = draft[s.factor_code];
  if (raw === undefined) return false;
  const typed = Number(raw);
  return raw.trim() === "" || !Number.isFinite(typed) || typed === 0;
}

function shockDisplay(s: WhatIfShock): string {
  if (s.shock_type === "ABSOLUTE_BP") return s.value.toFixed(1);
  if (s.shock_type === "ABSOLUTE") return s.value.toFixed(1);
  return (Math.expm1(s.value) * 100).toFixed(2); // log return shown as a percent move
}

const SHOCK_UNIT: Record<string, string> = {
  ABSOLUTE_BP: "bp",
  ABSOLUTE: "pt",
  RELATIVE: "%",
};

// the editor works in percent for LOG factors; the wire wants log returns
function toWire(s: WhatIfShock, typed: number): number {
  return s.shock_type === "RELATIVE" ? Math.log1p(typed / 100) : typed;
}

function useWhatIf(scales: Record<string, number>, shocks: WhatIfShock[]) {
  const [asOf] = useAsOf();
  return useQuery<WhatIfResult>({
    // identical books dedupe client-side; the server never caches sandbox output
    queryKey: ["/api/v1/whatif", asOf ?? "latest", scales, shocks],
    queryFn: () =>
      postJson<WhatIfResult>(
        "/api/v1/whatif",
        {
          adjustments: Object.entries(scales).map(([ticker, scale]) => ({ ticker, scale })),
          shocks,
        },
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
  const [preset, setPreset] = useState<string | null>(null);
  const [shockDraft, setShockDraft] = useState<Draft>({});
  const [shocks, setShocks] = useState<WhatIfShock[]>([]);
  const timer = useRef<ReturnType<typeof setTimeout>>();

  const catalog = useScenarioCatalog();
  const presetShocks = useScenarioShocks(preset);
  const loaded = useMemo<WhatIfShock[]>(
    () => (preset && presetShocks.isSuccess ? presetShocks.data.shocks : []),
    [preset, presetShocks.isSuccess, presetShocks.data],
  );

  const unitError = useMemo(() => shockUnitError(loaded, shockDraft), [loaded, shockDraft]);

  useEffect(() => {
    timer.current = setTimeout(() => {
      setScales(committedScales(draft));
      // hold the last good vector while the input is unrepresentable
      if (!unitError) setShocks(committedShocks(loaded, shockDraft));
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer.current);
  }, [draft, shockDraft, loaded, unitError]);

  const result = useWhatIf(scales, shocks);

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
  // a rejected shock belongs beside the shock grid, not under the book
  const shockError = inputError && /shock|factor|convention/i.test(inputError)
    ? inputError
    : null;
  const bookError = inputError && !shockError ? inputError : null;

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
            setShockDraft({});
            setPreset(null);
            setShocks([]);
          }}
          disabled={!edited && !preset}
        >
          RESET ALL
        </button>
      </div>

      <div className={styles.panel}>
        <div className={styles.panelTitle}>
          SHOCK — FACTOR MOVES
          {preset && (
            <span className={styles.presetTag}>
              {Object.keys(shockDraft).length ? `EDITED FROM ${preset}` : preset}
            </span>
          )}
        </div>
        <div className={styles.presetRow}>
          <label className={styles.hint}>
            PRESET
            <select
              className={styles.presetSelect}
              aria-label="Scenario preset"
              value={preset ?? ""}
              onChange={(e) => {
                setPreset(e.target.value || null);
                setShockDraft({});
              }}
            >
              <option value="">none — book changes only</option>
              {(catalog.data?.scenarios ?? []).map((s) => (
                <option key={s.scenario_code} value={s.scenario_code}>
                  {s.scenario_name}
                </option>
              ))}
            </select>
          </label>
          <span className={styles.hint}>
            {loaded.length
              ? `${loaded.length} factors shocked · instantaneous full revaluation`
              : "pick a scenario to load its moves, then edit any factor"}
          </span>
        </div>
        {preset && presetShocks.isError && (
          <p className={styles.error}>
            Could not load {preset} — {presetShocks.error.message}
          </p>
        )}
        {preset && presetShocks.isPending && <Skeleton height={120} />}
        {(unitError ?? shockError) && (
          <p className={styles.error}>NOT REVALUED — {unitError ?? shockError}</p>
        )}
        {loaded.length > 0 && (
          <table className={table.table}>
            <thead>
              <tr>
                <th>Factor</th>
                <th className={table.r}>Move</th>
                <th>Unit</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {loaded.map((s) => {
                const dropped = isDropped(s, shockDraft);
                const edited = shockDraft[s.factor_code] !== undefined;
                return (
                  <tr key={s.factor_code} className={dropped ? styles.stale : undefined}>
                    <td className="num" style={{ color: "var(--rd-gold)" }}>
                      {s.factor_code}
                    </td>
                    <td className={table.r}>
                      <input
                        className={`${styles.scaleInput} num`}
                        aria-label={`Shock ${s.factor_code}`}
                        value={shockDraft[s.factor_code] ?? shockDisplay(s)}
                        onChange={(e) =>
                          setShockDraft((d) => ({ ...d, [s.factor_code]: e.target.value }))
                        }
                      />
                    </td>
                    <td className={table.dim}>
                      {dropped ? "not shocked" : SHOCK_UNIT[s.shock_type]}
                    </td>
                    <td>
                      {edited && (
                        <button
                          className={styles.quick}
                          aria-label={`Restore ${s.factor_code}`}
                          onClick={() =>
                            setShockDraft((d) => {
                              const next = { ...d };
                              delete next[s.factor_code];
                              return next;
                            })
                          }
                        >
                          restore
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
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
          {firm?.shock_pnl != null && (
            <div className={styles.shockRow} title={fmtMoneyFull(firm.shock_pnl)}>
              SHOCK P&amp;L{" "}
              <strong style={{ color: firm.shock_pnl < 0 ? "var(--rd-down)" : "var(--rd-up)" }}>
                {fmtMoney(firm.shock_pnl)}
              </strong>
            </div>
          )}
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
                {d?.shock_pnl != null && (
                  <div className={styles.shockRow} title={fmtMoneyFull(d.shock_pnl)}>
                    SHOCK P&amp;L{" "}
                    <strong
                      style={{ color: d.shock_pnl < 0 ? "var(--rd-down)" : "var(--rd-up)" }}
                    >
                      {fmtMoney(d.shock_pnl)}
                    </strong>
                  </div>
                )}
              </div>
            );
          })}
      </div>

      <div className={styles.panel}>
        <div className={styles.panelTitle}>BOOK — SCALE POSITIONS</div>
        {bookError && <p className={styles.error}>NOT REVALUED — {bookError}</p>}
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
