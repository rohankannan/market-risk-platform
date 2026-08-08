import { useRiskSummary } from "../api/queries";
import { StaleBanner } from "../components/StaleBanner";
import { Skeleton } from "../components/Skeleton";
import { fmtMoney } from "../lib/format";

// M1 stub: proves the data path end to end (tiles, heatmap, movers and the
// 90d chart land with the Overview milestone)
export default function Overview() {
  const summary = useRiskSummary();
  if (summary.isPending) return <Skeleton height={120} />;
  if (summary.isError) throw summary.error;
  const firm = summary.data.desks.find((d) => d.is_aggregate);
  return (
    <>
      <StaleBanner resolved={summary.data.as_of} />
      <h1>Overview</h1>
      <p>
        Firm 1d VaR<sub>99</sub>{" "}
        <strong className="num">{fmtMoney(firm?.var_hs_1d)}</strong>
      </p>
    </>
  );
}
