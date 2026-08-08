import { useAsOf } from "../api/queries";

// "Batch not yet run for X - showing Y": rendered by pages whose resolved run
// predates the pinned as-of (the API resolves to the latest run <= as_of)
export function StaleBanner({ resolved }: { resolved: string | undefined }) {
  const [asOf] = useAsOf();
  if (!asOf || !resolved || resolved === asOf) return null;
  return (
    <div
      role="status"
      style={{
        background: "var(--util-warn)",
        border: "1px solid var(--zone-amber)",
        borderRadius: "var(--rd-radius)",
        padding: "6px 12px",
        marginBottom: 16,
        fontSize: 13,
      }}
    >
      Batch not yet run for <span className="num">{asOf}</span> — showing{" "}
      <span className="num">{resolved}</span>
    </div>
  );
}
