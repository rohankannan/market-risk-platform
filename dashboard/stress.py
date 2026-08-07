"""Stress page: scenario P&L by desk - historical replays and shocks."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard import api_client
from dashboard.ui import fmt_usd, scenario_bars_figure


def render() -> None:
    st.title("Stress scenarios")
    as_of = st.session_state.get("as_of")
    # scenarios execute only in the EOD cycle, so backfill-only dates have no
    # scenario run at or before them - fall back to the latest one, labeled
    payload = api_client.fetch_or_none("/api/v1/scenarios/results", as_of=as_of)
    if payload is None:
        payload = api_client.fetch("/api/v1/scenarios/results")
        st.caption(f"No scenario run on or before {as_of} (scenarios execute in the EOD "
                   f"cycle, not in backfill) - showing the latest, {payload['as_of']}.")
    results = payload["results"]

    st.pyplot(scenario_bars_figure(results))
    st.caption("Bars: per-desk P&L impact. Diamonds: firm total. Ordered worst-first. "
               "Replays apply the window's cumulative factor moves to today's book.")

    st.subheader("Impacts by desk")
    rows = []
    for r in results:
        row = {"Scenario": r["scenario_name"], "Type": r["scenario_type"],
               "Window": (f"{r['window_start']} to {r['window_end']}"
                          if r["window_start"] else "-"),
               "Firm impact": fmt_usd(r["firm_impact"]),
               "Worst desk": r["worst_desk"] or "-"}
        row.update({k: fmt_usd(v) for k, v in sorted(r["impacts"].items()) if k != "FIRM"})
        rows.append(row)
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    definitions = [(r["scenario_name"], r["description"]) for r in results if r["description"]]
    if definitions:
        st.subheader("Definitions")
        for name, description in definitions:
            st.markdown(f"**{name}** - {description}")
