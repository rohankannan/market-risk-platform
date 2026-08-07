"""RiskDesk dashboard - Streamlit MVP (spec section 7).

Three pages reading the API, never the database:
    streamlit run dashboard/app.py
Point RISKDESK_API_URL elsewhere for a deployed API.
"""

from __future__ import annotations

import streamlit as st

from dashboard import api_client, backtesting, overview, stress

st.set_page_config(page_title="RiskDesk", layout="wide")

meta = api_client.fetch("/api/v1/meta")
if meta["latest_as_of"] is None:
    st.warning("No completed batch runs yet - run `make demo`, then refresh.")
    st.stop()

with st.sidebar:
    as_of = st.selectbox("As of", list(reversed(meta["available_dates"])))
    st.caption(f"Latest batch: {meta['latest_as_of']} {meta['batch_type']} "
               f"{meta['batch_status']} - build {meta['code_version']}")
    if st.button("Refresh data"):
        st.cache_data.clear()
        st.rerun()

st.session_state["as_of"] = as_of
st.session_state["desk_codes"] = [d["desk_code"] for d in meta["desks"]]

nav = st.navigation([
    st.Page(overview.render, title="Overview", default=True),      # served at "/"
    st.Page(backtesting.render, title="Backtesting", url_path="backtesting"),
    st.Page(stress.render, title="Stress", url_path="stress"),
])
nav.run()
