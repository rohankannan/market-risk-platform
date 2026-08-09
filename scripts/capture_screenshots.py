"""Recapture the README dashboard screenshots from a running local stack.

The images in docs/img are the first thing most readers see, and they go stale
silently: they were last captured before the stress catalog gained RISK_OFF and
RATES_2022, so they showed a five-scenario catalog topped by the GFC replay
while the model doc headlined the 2022 replay at roughly twice that loss. A
committed script makes them regenerable, which is the same reason the RNIV and
stress packs carry one.

Two things this has to get right, both learned the hard way:

  - Viewport heights are MEASURED, never hardcoded. A fixed height is the bug
    that produced the stale images in the first place: the scenarios page grew
    64px when the catalog gained two rows, so a height pinned to the old page
    silently cropped the new content. Each page is loaded short, asked for its
    own scrollHeight, then re-opened at exactly that height.
  - The footer stamps code_version(), which is `git describe --always --dirty`.
    Capturing from a dirty tree bakes "-dirty" into the image, so this refuses
    to run unless the tree is clean (ALLOW_DIRTY_SHOTS=1 to override locally).
    Shots are written to a temp directory and moved into place only at the end,
    because writing the first image into a tracked directory dirties the tree
    and every page captured after it would stamp "-dirty" - the guard runs once
    at the start and cannot see damage the run does to itself.

Usage (with the API on :8000 and the frontend on :5173):
    python scripts/capture_screenshots.py
    python scripts/capture_screenshots.py --base-url http://localhost:5173
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

OUT_DIR = Path("docs/img")
WIDTH = 1512

# route -> output name. dashboard_stress.png keeps its name from when the page
# was called Stress so the README links do not move.
PAGES: list[tuple[str, str]] = [
    ("/", "dashboard_overview.png"),
    ("/backtesting", "dashboard_backtesting.png"),
    ("/scenarios", "dashboard_stress.png"),
]

# short enough that scrollHeight reports content rather than the viewport
MEASURE_HEIGHT = 400

# the network-idle wait covers the fetch; ECharts renders on the next frame and
# animation is off in every option builder, so a short settle is enough
SETTLE_MS = 900


def tree_is_clean() -> bool:
    out = subprocess.run(["git", "status", "--porcelain"],
                         capture_output=True, text=True, check=True)
    return not out.stdout.strip()


def measure_height(browser, url: str) -> int:
    """The page's own content height, so a grown page is never cropped."""
    page = browser.new_page(viewport={"width": WIDTH, "height": MEASURE_HEIGHT},
                            color_scheme="dark")
    page.goto(url, wait_until="networkidle")
    page.wait_for_timeout(SETTLE_MS)
    height = int(page.evaluate("document.documentElement.scrollHeight"))
    page.close()
    return height


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:5173")
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    args = ap.parse_args()

    if not tree_is_clean() and not os.getenv("ALLOW_DIRTY_SHOTS"):
        print("refusing to capture from a dirty tree: the footer would stamp "
              "a -dirty code_version. Commit first, or set ALLOW_DIRTY_SHOTS=1.",
              file=sys.stderr)
        return 1

    from playwright.sync_api import sync_playwright

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as staging_name:
        staging = Path(staging_name)
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for route, name in PAGES:
                url = f"{args.base_url}{route}"
                height = measure_height(browser, url)
                page = browser.new_page(viewport={"width": WIDTH, "height": height},
                                        color_scheme="dark",
                                        device_scale_factor=1)
                page.goto(url, wait_until="networkidle")
                page.wait_for_timeout(SETTLE_MS)
                page.screenshot(path=str(staging / name))
                print(f"{out_dir / name}  {WIDTH}x{height}")
                page.close()
            browser.close()
        # only now touch the tracked directory
        for _, name in PAGES:
            shutil.move(str(staging / name), str(out_dir / name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
