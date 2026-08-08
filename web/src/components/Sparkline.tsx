// hand-rolled SVG sparkline: no axes, no deps, deterministic under test
export function Sparkline({
  values,
  width = 120,
  height = 28,
  stroke = "var(--text-dim)",
}: {
  values: (number | null | undefined)[];
  width?: number;
  height?: number;
  stroke?: string;
}) {
  const ys = values.filter((v): v is number => v != null);
  if (ys.length < 2) return null;
  const min = Math.min(...ys);
  const max = Math.max(...ys);
  const span = max - min || 1;
  const pad = 2;
  const pts = ys
    .map((v, i) => {
      const x = pad + (i * (width - 2 * pad)) / (ys.length - 1);
      const y = pad + (1 - (v - min) / span) * (height - 2 * pad);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg width={width} height={height} aria-hidden focusable="false">
      <polyline points={pts} fill="none" stroke={stroke} strokeWidth="1.2" />
    </svg>
  );
}
