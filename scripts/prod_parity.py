#!/usr/bin/env python3
"""Prod parity: is the deployed stack actually running this commit?

Drift here is silent by construction. /api/v1/meta's code_version names the
*batch's* commit, and the batch runs on a scheduled runner that checks out main
every night - so the field advances whether or not the services ever redeploy.
Read as a deploy stamp it says "current" while the running code sits behind.

Three read-only checks:

  1. the served route set against the committed contract (web/openapi.json)
  2. the served build_version against the commit under test
  3. the published bundle's compiled-in API origin

Check 3 is here because the failure it catches cannot be seen from the API
side at all. VITE_API_URL is inlined by Vite at build time, so a wrong value
ships a dashboard that never reaches its backend - and when the wrong host
answers 200 with HTML rather than an error, nothing throws: the JSON parse
fails, the client falls back to the bundled snapshot, and the page serves
frozen numbers behind a demo badge. An API-only check calls that healthy.

    python scripts/prod_parity.py --api-url https://... --site-url https://...

Exit 0 when every check passes, 1 on drift, 2 when a surface is unreachable
(a free-tier cold start is retried first, and is not drift).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from risk.db import same_commit

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT = REPO_ROOT / "web" / "openapi.json"

# free-tier services spin down after idle; the first request pays the cold start
TIMEOUT_S = 120
BUNDLE_RE = re.compile(r"assets/index-[A-Za-z0-9_-]+\.js")
ORIGIN_RE = re.compile(r"https?://[A-Za-z0-9.\-]+(?::\d+)?")


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


# ------------------------------------------------------------- pure comparisons

def check_routes(expected: set[str], served: set[str]) -> Check:
    """Served route set against the committed contract."""
    missing = sorted(expected - served)
    extra = sorted(served - expected)
    if not missing and not extra:
        return Check("routes", True, f"{len(served)} routes match the committed contract")
    parts = []
    if missing:
        parts.append(f"deployed API is MISSING {len(missing)}: {', '.join(missing)}")
    if extra:
        # not a failure mode we expect, but silence would be worse than noise
        parts.append(f"deployed API serves {len(extra)} route(s) absent from the contract: "
                     f"{', '.join(extra)}")
    return Check("routes", False, "; ".join(parts))


def check_build(expected_sha: str | None, served_build: str | None) -> Check:
    """Served build stamp against the commit under test."""
    if not served_build:
        return Check("build", False,
                     "served /meta carries no build_version - the deployed API predates the "
                     "field, so it is necessarily behind this commit")
    if not expected_sha:
        return Check("build", False, "no expected commit to compare against")
    if same_commit(expected_sha, served_build):
        return Check("build", True, f"deployed build {served_build} is this commit")
    return Check("build", False,
                 f"deployed build is {served_build}, expected {expected_sha[:12]}")


def check_bundle_origin(bundle: str, api_url: str) -> Check:
    """Whether the published bundle actually points at the API.

    Vite inlines VITE_API_URL, so the expected origin has to appear literally in
    the shipped bytes. Any other origin found is reported: that list is where a
    wrong host shows itself.
    """
    want = api_url.rstrip("/")
    if want in bundle:
        return Check("bundle", True, f"published bundle targets {want}")
    found = sorted(set(ORIGIN_RE.findall(bundle)))
    return Check("bundle", False,
                 f"published bundle does NOT reference {want}; origins present: "
                 f"{', '.join(found[:10]) if found else '(none)'}")


# --------------------------------------------------------------- network shell

def fetch(url: str, what: str) -> str:
    """GET with one retry - the first timeout on free tier is a cold start."""
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT_S) as r:
                return r.read().decode()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt == 2:
                print(f"[parity] UNREACHABLE {what}: {url} ({type(exc).__name__}: {exc})")
                raise SystemExit(2) from exc
            print(f"[parity] {what} did not answer - retrying once (cold start?)")
    raise AssertionError("unreachable")


def head_sha() -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--api-url", required=True, help="deployed API origin")
    p.add_argument("--site-url", help="deployed dashboard origin (skipped if omitted)")
    p.add_argument("--expect-sha", default=None,
                   help="commit the deployment should be running (default: HEAD)")
    args = p.parse_args(argv)

    api = args.api_url.rstrip("/")
    expected_sha = args.expect_sha or head_sha()
    checks: list[Check] = []

    contract = json.loads(CONTRACT.read_text())
    served_doc = json.loads(fetch(f"{api}/openapi.json", "API openapi.json"))
    checks.append(check_routes(set(contract["paths"]), set(served_doc["paths"])))

    meta = json.loads(fetch(f"{api}/api/v1/meta", "API /meta"))
    checks.append(check_build(expected_sha, meta.get("build_version")))
    print(f"[parity] batch stamp on the latest run: {meta.get('code_version')} "
          f"(as of {meta.get('latest_as_of')}) - this is the BATCH, not the build")

    if args.site_url:
        site = args.site_url.rstrip("/")
        index = fetch(f"{site}/", "dashboard index")
        match = BUNDLE_RE.search(index)
        if not match:
            checks.append(Check("bundle", False,
                                f"no hashed entry bundle found in {site}/ - published output "
                                "is not the expected Vite build"))
        else:
            checks.append(check_bundle_origin(fetch(f"{site}/{match.group(0)}", "dashboard bundle"),
                                              api))

    print()
    for c in checks:
        print(f"[parity] {'OK  ' if c.ok else 'DRIFT'} {c.name:7s} {c.detail}")

    failed = [c for c in checks if not c.ok]
    if failed:
        print(f"\n[parity] {len(failed)} of {len(checks)} checks show drift: the deployed stack "
              f"is not running {(expected_sha or 'this commit')[:12]}.")
        print("[parity] Redeploy the affected service, then re-run. This check is the only "
              "signal that says so - /meta's code_version cannot.")
        return 1
    print(f"\n[parity] all {len(checks)} checks pass: deployed stack matches "
          f"{(expected_sha or 'this commit')[:12]}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
