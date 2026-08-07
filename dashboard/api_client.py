"""Thin HTTP client for the RiskDesk API.

The dashboard reads the API, never the database (spec section 7). Responses
cache per (path, params): pinned-as_of payloads are immutable server-side, so
a modest TTL costs nothing and keeps page switches instant.
"""

from __future__ import annotations

import os

import httpx
import streamlit as st

API_URL = os.getenv("RISKDESK_API_URL", "http://localhost:8000")
TIMEOUT_S = 10.0
CACHE_TTL_S = 300


@st.cache_data(ttl=CACHE_TTL_S, show_spinner=False)
def get(path: str, **params) -> dict:
    """Raw GET; raises httpx errors (fetch() turns them into page messages)."""
    resp = httpx.get(f"{API_URL}{path}",
                     params={k: v for k, v in params.items() if v is not None},
                     timeout=TIMEOUT_S)
    resp.raise_for_status()
    return resp.json()


def _get_rendering_failures(path: str, allow_404: bool, params: dict) -> dict | None:
    try:
        return get(path, **params)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            if allow_404:
                return None
            detail = exc.response.json().get("detail", "no data")
            st.warning(f"No data from the API ({detail}). "
                       "If the database is empty, run `make demo`, then refresh.")
        else:
            st.error(f"API error {exc.response.status_code} on {path}.")
        st.stop()
    except httpx.HTTPError:
        st.error(f"API unreachable at {API_URL}. Start it with `make api`.")
        st.stop()


def fetch(path: str, **params) -> dict:
    """GET with the failure modes rendered in-page: 404 (no data for the
    request) becomes a warning, anything else an error; both halt the page."""
    return _get_rendering_failures(path, allow_404=False, params=params)


def fetch_or_none(path: str, **params) -> dict | None:
    """GET returning None on 404, for callers with a fallback; other failures
    render and halt as in fetch()."""
    return _get_rendering_failures(path, allow_404=True, params=params)
