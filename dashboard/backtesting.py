"""Backtesting page: coverage and independence stats, traffic light, timeline."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard import api_client
from dashboard.ui import fmt_usd, pla_scatter_figure, pnl_vs_var_figure

WINDOW_MIN, WINDOW_MAX, WINDOW_STEP = 50, 1000, 50
WINDOW_DEFAULT = 250      # the Basel traffic-light window


def render() -> None:
    st.title("Backtesting")
    as_of = st.session_state.get("as_of")
    scopes = st.session_state.get("desk_codes", ["FIRM"])

    c1, c2, c3 = st.columns(3)
    scope = c1.selectbox("Scope", scopes)
    model = c2.radio("VaR model", ["HS", "FHS"], horizontal=True)
    window = int(c3.number_input("Window (days)", min_value=WINDOW_MIN, max_value=WINDOW_MAX,
                                 value=WINDOW_DEFAULT, step=WINDOW_STEP))

    b = api_client.fetch("/api/v1/backtest/summary", scope=scope, model=model,
                         window=window, as_of=as_of)
    kupiec, ind, cc = b["kupiec"], b["christoffersen_independence"], b["christoffersen_cc"]
    tl = b["traffic_light"]

    m = st.columns(4)
    m[0].metric("Exceptions", f"{b['n_exceptions']} in {b['n_obs']}d",
                delta=f"expected {b['expected_exceptions']:.1f}", delta_color="off")
    m[1].metric("Basel zone", tl["zone"],
                delta=f"multiplier {tl['multiplier']:.2f}", delta_color="off")
    m[2].metric("Kupiec POF", f"LR {kupiec['statistic']:.2f}",
                delta=f"p = {kupiec['p_value']:.3f}", delta_color="off")
    m[3].metric("Christoffersen independence", f"LR {ind['statistic']:.2f}",
                delta=f"p = {ind['p_value']:.3f}", delta_color="off")
    if not tl["regulatory_window"]:
        st.caption(f"Traffic-light zones are calibrated to a 250-day window; "
                   f"realized window here is {b['n_obs']} days.")

    st.subheader("Test statistics")
    stats = pd.DataFrame([
        {"Test": "Kupiec POF (coverage)", "LR": f"{kupiec['statistic']:.3f}",
         "df": kupiec["df"], "p-value": f"{kupiec['p_value']:.3f}",
         "Reject at 5%": kupiec["reject_5pct"]},
        {"Test": "Christoffersen independence", "LR": f"{ind['statistic']:.3f}",
         "df": ind["df"], "p-value": f"{ind['p_value']:.3f}",
         "Reject at 5%": ind["reject_5pct"]},
        {"Test": "Christoffersen conditional coverage", "LR": f"{cc['statistic']:.3f}",
         "df": cc["df"], "p-value": f"{cc['p_value']:.3f}",
         "Reject at 5%": cc["reject_5pct"]},
    ])
    st.dataframe(stats, hide_index=True, width="stretch")

    st.subheader("Exception timeline")
    history = api_client.fetch("/api/v1/risk/history", scope=scope, window=window, as_of=as_of)
    st.pyplot(pnl_vs_var_figure(history["points"], models=(model.lower(),)))

    st.subheader("Exception days")
    if b["exceptions"]:
        st.dataframe(pd.DataFrame(
            [{"Date": e["date"], "VaR (prior run)": fmt_usd(e["var_value"]),
              "P&L": fmt_usd(e["pnl_value"])} for e in b["exceptions"]]),
            hide_index=True, width="stretch")
    else:
        st.caption("No exceptions in the window.")

    st.subheader("P&L attribution")
    pla = api_client.fetch_or_none("/api/v1/backtest/pla", scope=scope,
                                   window=window, as_of=as_of)
    if pla is None:
        st.caption("No paired risk-theoretical P&L for this scope and window yet.")
    else:
        p1, p2, p3 = st.columns(3)
        p1.metric("Spearman", f"{pla['spearman']:.3f}")
        p2.metric("KS statistic", f"{pla['ks']:.3f}")
        p3.metric("PLA zone", pla["zone"], delta=f"{pla['n_obs']} paired days",
                  delta_color="off")
        st.pyplot(pla_scatter_figure(pla["points"]))
        st.caption("Daily hypothetical vs risk-theoretical P&L on the 45-degree line. "
                   "RTPL is the delta-gamma-vega path - rho and cross terms excluded "
                   "by design, so the scatter is honest, not decorative.")
