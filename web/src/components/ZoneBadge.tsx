const ZONE_COLOR: Record<string, string> = {
  GREEN: "var(--zone-green)",
  AMBER: "var(--zone-amber)",
  RED: "var(--zone-red)",
};

// the zone text always accompanies the color - color is never the only signal
export function ZoneBadge({ zone }: { zone: string }) {
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 10px",
        borderRadius: "var(--rd-radius)",
        color: "var(--rd-bg)",
        background: ZONE_COLOR[zone] ?? "var(--text-dim)",
        fontSize: 13,
        fontWeight: 600,
        letterSpacing: "0.03em",
      }}
    >
      {zone}
    </span>
  );
}
