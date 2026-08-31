"""Record the REAL locators for Sleeper's lineup editor. Read-only.

hands/selector_map.json ships four placeholder locators and `calibrated: false`,
so hands.browser.swap_lineup refuses to run — correctly, because clicking a
guess on a real team is worse than not clicking. This tool opens the actual team
page with the saved session and reports which candidate locators resolve, so the
map can be filled in from evidence.

It NEVER clicks anything that mutates. It reads the DOM, counts matches, and
saves a screenshot. Arming the swap is a separate, deliberate step that a person
should watch.

    python tools/calibrate_lineup.py --state PATH --league ID [--headed]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

# Candidates per role, best-practice first: a role/name query that survives a
# CSS redesign beats a class selector that does not. Whatever resolves here is
# what goes in the map — the point is to stop guessing, not to guess in a new
# file.
CANDIDATES = {
    "logged_in_probe": [
        ("role", "link", "Scores"),
        ("role", "button", "Lineup"),
        ("text", None, "My Team"),
        ("css", None, "[class*='avatar']"),
    ],
    "starter_row": [
        ("css", None, "[class*='starter'] [class*='player-name']"),
        ("css", None, "[data-testid*='starter']"),
        ("css", None, "[class*='roster-slot']"),
        ("css", None, "[class*='team-roster-item']"),
    ],
    "bench_row": [
        ("css", None, "[class*='bench'] [class*='player-name']"),
        ("css", None, "[data-testid*='bench']"),
        ("css", None, "[class*='bench-slot']"),
    ],
    "confirm_swap": [
        ("role", "button", "Confirm"),
        ("role", "button", "Save"),
        ("text", None, "Move here"),
        ("css", None, "[class*='confirm']"),
    ],
}


def probe(page, kind, role, value):
    try:
        if kind == "role":
            loc = page.get_by_role(role, name=value)
        elif kind == "text":
            loc = page.get_by_text(value, exact=False)
        else:
            loc = page.locator(value)
        n = loc.count()
        sample = ""
        if n:
            sample = (loc.first.inner_text(timeout=1500) or "").strip()[:60].replace("\n", " / ")
        return {"n": n, "sample": sample}
    except Exception as e:
        return {"n": 0, "error": type(e).__name__}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--league", required=True)
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--shot", default="lineup_page.png")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    url = f"https://sleeper.com/leagues/{args.league}/team"
    report = {"url": url, "roles": {}}
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=not args.headed,
                               args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = b.new_context(storage_state=args.state,
                            viewport={"width": 1400, "height": 1000})
        page = ctx.new_page()
        page.goto(url, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3500)          # the roster hydrates after load

        report["title"] = page.title()
        report["final_url"] = page.url
        report["looks_logged_out"] = bool(
            page.get_by_role("button", name="Log In").count()
            or "login" in page.url.lower())

        for role_name, cands in CANDIDATES.items():
            report["roles"][role_name] = [
                {"how": k, "role": r, "value": v, **probe(page, k, r, v)}
                for k, r, v in cands
            ]
        # A wide net, so a redesign that broke every guess above still leaves
        # something to read.
        report["dom_hints"] = page.evaluate(r"""() => {
          const seen = {};
          for (const el of document.querySelectorAll('[class]')) {
            for (const c of String(el.className).split(/\s+/)) {
              if (/start|bench|slot|roster|player|lineup|confirm/i.test(c))
                seen[c] = (seen[c] || 0) + 1;
            }
          }
          return Object.entries(seen).sort((a,b) => b[1]-a[1]).slice(0, 25);
        }""")
        page.screenshot(path=args.shot, full_page=True)
        b.close()

    print(json.dumps(report, indent=1)[:4000])
    pathlib.Path("lineup_calibration.json").write_text(
        json.dumps(report, indent=1), encoding="utf-8")
    print("\nsaved: lineup_calibration.json and", args.shot)
    return 0


if __name__ == "__main__":
    sys.exit(main())
