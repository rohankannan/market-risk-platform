import type { HistoryPoint } from "../api/types";
import { fmtMoney } from "../lib/format";
import table from "./DataTable.module.css";

// the text alternative for every P&L-vs-VaR chart: same numbers, same negation
export function HistoryDataTable({ points }: { points: HistoryPoint[] }) {
  return (
    <details className={table.chartData}>
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
              <td className={`${table.r} num`}>{p.var_hs == null ? "-" : fmtMoney(-p.var_hs)}</td>
              <td className={`${table.r} num`}>
                {p.var_fhs == null ? "-" : fmtMoney(-p.var_fhs)}
              </td>
              <td>
                {[p.exception_hs && "HS", p.exception_fhs && "FHS"].filter(Boolean).join("+")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}
