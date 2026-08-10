"""Record real API responses as frontend fixtures.

    python web/scripts/record_fixtures.py [base_url]

Writes one JSON file per request to web/src/mocks/fixtures/ (the MSW test
payloads) and bundles every payload into web/public/snapshot.json - the
offline fallback the fetch wrapper serves, keyed by path plus canonical
(sorted) query string. Run against a seeded local stack (make demo + make
api); everything below reads the committed snapshot, no third-party calls.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

DEFAULT_BASE = "http://127.0.0.1:8000"
WEB_DIR = Path(__file__).resolve().parent.parent
FIXTURES_DIR = WEB_DIR / "src" / "mocks" / "fixtures"
SNAPSHOT_PATH = WEB_DIR / "public" / "snapshot.json"

HISTORY_WINDOW_FIRM = 90        # Overview chart
HISTORY_WINDOW_DESK = 60        # Desk pages
BACKTEST_WINDOWS = (250, 500, 750)   # Backtesting page selector
ROLLING_WINDOW = 250            # the page over-fetches history by this much
HISTORY_CAP = 1000
BACKTEST_WINDOW = BACKTEST_WINDOWS[0]


def canonical(path: str, params: dict) -> str:
    """The snapshot lookup key: path + sorted query string (matches the
    frontend fetch wrapper's canonicalization)."""
    if not params:
        return path
    query = "&".join(f"{k}={params[k]}" for k in sorted(params))
    return f"{path}?{query}"


def slug(key: str) -> str:
    """Fixture filename from a canonical key: strip the API prefix, then make
    it filesystem-safe."""
    s = key.removeprefix("/api/v1/").removeprefix("/")
    for a, b in (("/", "_"), ("?", "__"), ("&", "__"), ("=", "_"), (":", "_")):
        s = s.replace(a, b)
    return f"{s}.json"


def request_list(desks: list[str], scenario_codes: list[str]) -> list[tuple[str, dict]]:
    """Every request the pages issue at their default and selectable states -
    the offline demo must cover the whole surface, not just the landing view."""
    reqs: list[tuple[str, dict]] = [
        ("/api/v1/meta", {}),
        ("/api/v1/risk/summary", {}),
        ("/api/v1/risk/history", {"scope": "FIRM", "window": HISTORY_WINDOW_FIRM}),
        ("/api/v1/risk/movers", {}),
        ("/api/v1/risk/exposures", {}),
        ("/api/v1/factors/latest", {}),
        ("/api/v1/scenarios", {}),
        ("/api/v1/scenarios/results", {}),
        ("/api/v1/flash", {}),
        ("/api/v1/modeldoc", {}),
    ]
    # the terminal overview reads FIRM history at the plain backtest window,
    # and the sampling-uncertainty panel beside the firm measures
    reqs.append(("/api/v1/risk/history", {"scope": "FIRM", "window": BACKTEST_WINDOW}))
    reqs.append(("/api/v1/risk/uncertainty", {}))
    for w in BACKTEST_WINDOWS:
        reqs.append(("/api/v1/risk/history",
                     {"scope": "FIRM", "window": min(w + ROLLING_WINDOW, HISTORY_CAP)}))
        reqs.append(("/api/v1/backtest/pla", {"scope": "FIRM", "window": w}))
        for model in ("HS", "FHS"):
            reqs.append(("/api/v1/backtest/summary",
                         {"scope": "FIRM", "model": model, "window": w}))
            # what the test at that window can detect, same params as the summary
            reqs.append(("/api/v1/backtest/power",
                         {"scope": "FIRM", "model": model, "window": w}))
    # every catalog scenario resolved to its shock vector: the sandbox loads
    # these as presets, so the offline demo must carry them too
    for code in scenario_codes:
        reqs.append((f"/api/v1/scenarios/{code}/shocks", {}))
    for desk in desks:
        reqs += [
            ("/api/v1/risk/history", {"scope": f"desk:{desk}", "window": HISTORY_WINDOW_DESK}),
            ("/api/v1/risk/history",
             {"scope": f"desk:{desk}", "window": BACKTEST_WINDOW + ROLLING_WINDOW}),
            ("/api/v1/backtest/pla", {"scope": desk, "window": BACKTEST_WINDOW}),
            (f"/api/v1/desks/{desk}/decomposition", {}),
            (f"/api/v1/desks/{desk}/positions", {}),
        ]
        for model in ("HS", "FHS"):
            reqs.append(("/api/v1/backtest/summary",
                         {"scope": desk, "model": model, "window": BACKTEST_WINDOW}))
            # keep power's offline coverage identical to summary's: a desk
            # scope whose cards render but whose resolution strip silently
            # vanishes would read as a bug, not as a recording gap
            reqs.append(("/api/v1/backtest/power",
                         {"scope": desk, "model": model, "window": BACKTEST_WINDOW}))
    return reqs


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    base = args[0] if args else DEFAULT_BASE
    client = httpx.Client(base_url=base, timeout=30.0)

    meta = client.get("/api/v1/meta").raise_for_status().json()
    desks = [d["desk_code"] for d in meta["desks"] if not d["is_aggregate"]]
    if not desks:
        raise RuntimeError("meta reports no standalone desks - is the stack seeded?")
    # BOTH stamps: code_version is the batch's commit off the run row, and
    # build_version is the serving process's own `git describe --dirty`. The
    # gate once checked only the first, which a dirty tree slips whenever the
    # batch row predates the local edits - exactly the state a mid-iteration
    # recording happens in - and the CI provenance grep then fails on the
    # recorded build stamp instead of this refusal firing first.
    for field in ("code_version", "build_version"):
        version = meta.get(field) or ""
        if version.endswith("-dirty") and not os.getenv("ALLOW_DIRTY_FIXTURES"):
            raise RuntimeError(
                f"latest run stamps {field} {version!r} - committed fixtures must "
                "come from a clean tree (commit, re-run the EOD, re-record; "
                "ALLOW_DIRTY_FIXTURES=1 overrides for local iteration)")

    # collect everything before writing anything: a mid-run failure must not
    # leave fixtures and snapshot.json recorded from different stack states
    snapshot: dict[str, dict] = {}
    catalog = client.get("/api/v1/scenarios").raise_for_status().json()
    scenario_codes = [s["scenario_code"] for s in catalog["scenarios"]]
    for path, params in request_list(desks, scenario_codes):
        key = canonical(path, params)
        resp = client.get(path, params=params)
        if resp.status_code != 200:
            try:
                detail = resp.json().get("detail", resp.text)
            except ValueError:
                detail = resp.text
            raise RuntimeError(f"{key} -> HTTP {resp.status_code}: {detail} - record "
                               "against a fully cycled stack (make demo + make api); "
                               "no files were written")
        snapshot[key] = resp.json()
        print(f"[fixtures] {key}")

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    for key, body in snapshot.items():
        (FIXTURES_DIR / slug(key)).write_text(json.dumps(body, indent=2) + "\n")
    SNAPSHOT_PATH.write_text(json.dumps(snapshot, indent=2) + "\n")
    print(f"[fixtures] {len(snapshot)} responses -> {SNAPSHOT_PATH.relative_to(WEB_DIR.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
