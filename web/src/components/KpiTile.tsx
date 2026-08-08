import styles from "./KpiTile.module.css";

export function KpiTile({
  label,
  value,
  delta,
  deltaColor,
  sub,
  children,
}: React.PropsWithChildren<{
  label: string;
  value: React.ReactNode;
  delta?: string;
  deltaColor?: string;
  sub?: React.ReactNode;
}>) {
  return (
    <div className={styles.tile}>
      <span className={styles.label}>{label}</span>
      <span>
        <span className={`${styles.value} num`}>{value}</span>
        {delta && (
          <span className={`${styles.delta} num`} style={{ color: deltaColor }}>
            {delta}
          </span>
        )}
      </span>
      {children}
      {sub && <span className={`${styles.sub} num`}>{sub}</span>}
    </div>
  );
}
