"""Overview page: firm tiles, desk limit table, trailing P&L vs VaR."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard import api_client
from dashboard.ui import fmt_pct, fmt_usd, pnl_vs_var_figure

HISTORY_DAYS = 90


def render() -> None:
    st.title("Overview")
    as_of = st.session_state.get("as_of")
    summary = api_client.fetch("/api/v1/risk/summary", as_of=as_of)
    firm = next(d for d in summary["desks"] if d["is_aggregate"])

    tiles = st.columns(6)
    tiles[0].metric("Firm VaR99 1d (HS)", fmt_usd(firm["var_hs_1d"]),
                    delta=None if firm["var_dod"] is None else fmt_usd(firm["var_dod"]),
                    delta_color="inverse")
    tiles[1].metric("Firm VaR99 1d (FHS)", fmt_usd(firm["var_fhs_1d"]))
    tiles[2].metric("Firm ES 97.5 1d", fmt_usd(firm["es_975_1d"]))
    tiles[3].metric("Stressed ES (2008-09)", fmt_usd(firm["es_stressed_1d"]))
    tiles[4].metric("Limit utilization", fmt_pct(firm["utilization"]))
    tiles[5].metric("Diversification benefit", fmt_pct(summary["diversification_benefit"]))

    st.subheader("Desk risk and limits")
    rows = [{"Desk": d["desk_name"], "VaR99 HS 1d": fmt_usd(d["var_hs_1d"]),
             "VaR99 FHS 1d": fmt_usd(d["var_fhs_1d"]), "ES 97.5 1d": fmt_usd(d["es_975_1d"]),
             "Stressed ES": fmt_usd(d["es_stressed_1d"]), "DoD": fmt_usd(d["var_dod"]),
             "Limit": fmt_usd(d["limit_value"]), "Utilization": fmt_pct(d["utilization"]),
             "Status": d["limit_status"] or "-"} for d in summary["desks"]]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    st.subheader("Rates DV01 by key rate")
    exposures = api_client.fetch("/api/v1/risk/exposures", as_of=as_of)
    if exposures["rows"]:
        krd = pd.DataFrame(exposures["rows"])
        krd["node"] = krd["factor_code"].str.removeprefix("IR.UST.")
        wide = krd.pivot(index="desk_code", columns="node", values="value")
        wide = wide[krd.sort_values("tenor_years")["node"].unique()]
        wide["Total"] = wide.sum(axis=1)
        st.dataframe(wide.map(fmt_usd), width="stretch")
        st.caption(f"{exposures['unit']} per node, bootstrapped par curve, "
                   "curve re-solved per bump. Off-diagonal risk grows as coupons "
                   "drift from par (see the model doc's R7).")
        if exposures.get("vega"):
            st.caption("Vega: " + " · ".join(
                f"{v['desk_code']} {fmt_usd(v['value'])}/vol pt on {v['factor_code']}"
                for v in exposures["vega"]))
    else:
        st.caption("No key-rate exposures for this run - backfill runs skip the curve step.")

    st.subheader(f"Firm P&L vs VaR, trailing {HISTORY_DAYS} runs")
    history = api_client.fetch("/api/v1/risk/history", scope="FIRM",
                               window=HISTORY_DAYS, as_of=as_of)
    st.pyplot(pnl_vs_var_figure(history["points"]))
    st.caption("Bars: daily hypothetical P&L. Lines: -VaR99, the loss threshold. "
               "Dots: backtest exceptions - days when P&L fell below -VaR from the prior run.")
