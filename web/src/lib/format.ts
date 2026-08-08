// Number formatting, one place. Same scale semantics as the Streamlit
// dashboard's fmt_usd (>= $1M shows 2dp in millions, >= $1K whole thousands,
// minus sign never parentheses) in the terminal's typography: uppercase K and
// a true minus (U+2212) on money. Ties round half-to-even like Python's
// format() - JS defaults round halves away from zero, which would print
// $318,500 as $319K where the batch says $318K.

const grouped = (v: number, dp: number): string =>
  v.toLocaleString("en-US", {
    minimumFractionDigits: dp,
    maximumFractionDigits: dp,
    roundingMode: "halfEven",
  });

export function fmtMoney(x: number | null | undefined): string {
  if (x == null) return "-";
  const sign = x < 0 ? "−" : "";
  const a = Math.abs(x);
  if (a >= 1e6) return `${sign}$${grouped(a / 1e6, 2)}M`;
  if (a >= 1e3) return `${sign}$${grouped(a / 1e3, 0)}K`;
  return `${sign}$${grouped(a, 0)}`;
}

export function fmtMoneyFull(x: number | null | undefined): string {
  if (x == null) return "-";
  const sign = x < 0 ? "−" : "";
  return `${sign}$${grouped(Math.abs(x), 2)}`;
}

export function fmtPct(x: number | null | undefined, digits = 1): string {
  if (x == null) return "-";
  return `${(x * 100).toFixed(digits)}%`;
}

export function fmtSignedPct(x: number | null | undefined, digits = 1): string {
  if (x == null) return "-";
  const s = (x * 100).toFixed(digits);
  return `${x > 0 ? "+" : ""}${s}%`;
}

export function fmtBp(x: number | null | undefined): string {
  if (x == null) return "-";
  return `${x > 0 ? "+" : ""}${Math.round(x)}bp`;
}

export function fmtVolPt(x: number | null | undefined): string {
  if (x == null) return "-";
  const sign = x < 0 ? "−" : "";
  return `${sign}$${grouped(Math.abs(x), 0)}/pt`;
}
