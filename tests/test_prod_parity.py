"""Parity tests: the rules that decide whether a deployment is stale.

Known answers only - no network. The checks are pure so the decision logic can
be pinned; the fetching around them is a thin shell.
"""

import scripts.prod_parity as parity
from risk.db import SHA_CHARS, build_version, same_commit

CONTRACT = {"/healthz", "/api/v1/meta", "/api/v1/whatif", "/api/v1/flash"}


# ------------------------------------------------------------------ build stamps

def test_same_commit_compares_on_the_common_prefix():
    # a host-injected SHA truncated to twelve against git describe's seven:
    # the same commit, spelled two different lengths
    assert same_commit("8736842abcdef0123456", "8736842")
    assert same_commit("8736842", "8736842abcdef")
    assert not same_commit("8736842abcdef", "c3b8689abcdef")


def test_same_commit_treats_a_dirty_tree_as_a_different_commit():
    # the prefix matches; the code does not. A dirty stamp naming a clean
    # commit is exactly the provenance lie the -dirty suffix exists to prevent.
    assert not same_commit("8736842-dirty", "8736842")
    assert not same_commit("8736842", "8736842-dirty")
    assert same_commit("8736842-dirty", "8736842-dirty")


def test_same_commit_missing_stamp_is_not_a_mismatch():
    # the offline snapshot has no running build; a false alarm there is worse
    # than staying quiet
    assert same_commit(None, "8736842")
    assert same_commit("8736842", None)
    assert same_commit("", "8736842")


def test_build_version_prefers_the_host_injected_commit(monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "0123456789abcdef0123456789abcdef01234567")
    monkeypatch.setenv("GIT_SHA", "deadbee")
    assert build_version() == "0123456789ab"
    assert len(build_version()) == SHA_CHARS


def test_build_version_falls_back_to_the_build_arg(monkeypatch):
    # no host stamp (compose, CI, a local uvicorn) - the image's build arg wins
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    monkeypatch.setenv("GIT_SHA", "deadbee")
    assert build_version() == "deadbee"


# ---------------------------------------------------------------------- routes

def test_check_routes_passes_when_the_served_set_matches():
    c = parity.check_routes(CONTRACT, set(CONTRACT))
    assert c.ok and "match" in c.detail


def test_check_routes_names_every_missing_route():
    # the shape of the real stall: the deployed API predates three commits
    served = CONTRACT - {"/api/v1/whatif", "/api/v1/flash"}
    c = parity.check_routes(CONTRACT, served)
    assert not c.ok
    assert "/api/v1/whatif" in c.detail and "/api/v1/flash" in c.detail


def test_check_routes_reports_routes_absent_from_the_contract():
    c = parity.check_routes(CONTRACT, CONTRACT | {"/api/v1/surprise"})
    assert not c.ok and "/api/v1/surprise" in c.detail


# ----------------------------------------------------------------------- build

def test_check_build_matches_across_stamp_lengths():
    c = parity.check_build("8736842abcdef0123456", "8736842abcde")
    assert c.ok


def test_check_build_flags_a_missing_stamp_as_behind():
    # an API that cannot report a build is necessarily older than the commit
    # that introduced the field - silence here must not read as agreement
    c = parity.check_build("8736842abcdef", None)
    assert not c.ok and "predates" in c.detail


def test_check_build_reports_both_sides_on_mismatch():
    c = parity.check_build("8736842abcdef", "3667c32abcde")
    assert not c.ok and "3667c32abcde" in c.detail and "8736842abcde" in c.detail


# ---------------------------------------------------------------------- bundle

def test_check_bundle_origin_accepts_a_bundle_that_names_the_api():
    c = parity.check_bundle_origin('x="https://riskdesk.onrender.com";', "https://riskdesk.onrender.com")
    assert c.ok


def test_check_bundle_origin_catches_the_wrong_host_and_names_it():
    # the failure that shipped: VITE_API_URL pointed at the Streamlit ops
    # service, which answers 200 with HTML - so nothing errored, the JSON parse
    # failed, and the dashboard quietly served its bundled snapshot instead
    bundle = 'const A="https://riskdeskdash.onrender.com";'
    c = parity.check_bundle_origin(bundle, "https://riskdesk.onrender.com")
    assert not c.ok
    assert "riskdeskdash.onrender.com" in c.detail


def test_check_bundle_origin_catches_an_unset_build_variable():
    # VITE_API_URL missing at build time bakes in the dev default
    c = parity.check_bundle_origin('const A="http://localhost:8000";', "https://riskdesk.onrender.com")
    assert not c.ok and "localhost:8000" in c.detail


def test_check_bundle_origin_ignores_the_trailing_slash():
    c = parity.check_bundle_origin('"https://riskdesk.onrender.com"', "https://riskdesk.onrender.com/")
    assert c.ok
