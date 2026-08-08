// skeletons are sized by the caller to the final dimensions - no spinners,
// no layout shift when data lands
export function Skeleton({ height, width }: { height: number; width?: number | string }) {
  return (
    <div
      aria-hidden
      style={{
        height,
        width: width ?? "100%",
        background: "var(--border)",
        borderRadius: "var(--rd-radius)",
        opacity: 0.5,
      }}
    />
  );
}
