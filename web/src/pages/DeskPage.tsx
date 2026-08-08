import { useParams } from "react-router-dom";

import { useDeskDecomposition } from "../api/queries";
import { Skeleton } from "../components/Skeleton";
import { StaleBanner } from "../components/StaleBanner";
import { fmtMoney } from "../lib/format";

// M1 stub: waterfall, exposure bars and the positions table land with the
// Desk milestone
export default function DeskPage() {
  const { deskCode = "" } = useParams();
  const decomp = useDeskDecomposition(deskCode);
  if (decomp.isPending) return <Skeleton height={120} />;
  if (decomp.isError) throw decomp.error;
  return (
    <>
      <StaleBanner resolved={decomp.data.as_of} />
      <h1>{decomp.data.desk_name}</h1>
      <p>
        1d VaR<sub>99</sub>{" "}
        <strong className="num">{fmtMoney(decomp.data.var_hs_1d)}</strong>
      </p>
    </>
  );
}
