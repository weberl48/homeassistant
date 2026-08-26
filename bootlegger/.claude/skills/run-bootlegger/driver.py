#!/usr/bin/env python3
"""Bootlegger run-driver — launch-adjacent harness for the web UI.

The server is started separately (see SKILL.md); this drives the running app
the way a person would: loads the board, clicks through the five tabs, runs the
approve -> execute -> verify flow, and screenshots what it sees.

Runs on any Python with Playwright installed — NOT the server venv, which
deliberately has no test tooling in it. See SKILL.md "Prerequisites".

    python driver.py check           # render check: console errors, tab count
    python driver.py api             # every GET endpoint, status codes
    python driver.py shots [outdir]  # screenshot all five tabs
    python driver.py flow [outdir]   # drive approve -> verified, assert audit
    python driver.py all  [outdir]   # api + check + shots + flow

Exit code is nonzero if any assertion fails, so it is CI-usable.
"""
from __future__ import annotations

import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

BASE = "http://localhost:8484"

# The Board / This Week / Waivers / The League / The Parlor / The Ledger.
# Bump deliberately when a room is added — a silently-changing tab count is
# how a room goes missing without any test noticing.
EXPECTED_TABS = 6

# Every GET the UI depends on. /health is deliberately NOT under /api — see
# SKILL.md Gotchas; requesting /api/health returns 404 and looks like an outage.
GETS = [
    "/health",
    "/api/draft/board",
    "/api/week/current",
    "/api/recs",
    "/api/audit",
    "/api/rules",
    "/api/waivers",
    "/api/league/overview",
    "/api/queue",
    "/api/practice",
    "/api/trades/suggest",
    "/api/league/rosters",
    "/api/draft/grades",
]


def _get(path: str, timeout: float = 20.0):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode())


def _post(path: str, timeout: float = 30.0):
    req = urllib.request.Request(BASE + path, method="POST", data=b"")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode()
        return r.status, (json.loads(body) if body.strip() else None)


def cmd_api() -> int:
    """Hit every GET the UI needs. Fails loudly rather than rendering an empty board."""
    bad = []
    for p in GETS:
        try:
            status, _ = _get(p)
            print(f"  {status}  {p}")
            if status != 200:
                bad.append((p, status))
        except urllib.error.HTTPError as e:
            print(f"  {e.code}  {p}   <-- FAIL")
            bad.append((p, e.code))
        except Exception as e:  # connection refused == server not up
            print(f"  ERR  {p}: {type(e).__name__}: {e}")
            bad.append((p, str(e)))
    if bad:
        print(f"\nFAIL: {len(bad)} endpoint(s) unhealthy: {bad}")
        return 1
    print(f"\nOK: {len(GETS)} endpoints healthy")
    return 0


def _page(pw, errors):
    b = pw.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=2)
    pg.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
          if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    return b, pg


def _tabs(pg):
    """Locate the five nav tabs.

    Deliberately by role+index, never by text: each tab's innerText is two
    lines ("THE BOARD\nthe draft"), so get_by_text(..., exact=True) matches
    nothing and every click silently times out. See SKILL.md Gotchas.
    """
    return pg.locator("nav a, nav button, [role=tab]")


def cmd_check() -> int:
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    with sync_playwright() as pw:
        b, pg = _page(pw, errors)
        pg.goto(BASE, wait_until="networkidle")
        pg.wait_for_timeout(2500)
        title = pg.title()
        n = _tabs(pg).count()
        # The board must actually paint rows, not just resolve the shell: an
        # API that 200s while the board renders empty is the failure this
        # catches. `.prow` is the player row (app.js playerRow()).
        players = pg.locator(".prow").count()
        cols = pg.locator("section.col").count()
        b.close()

    print(f"  title      : {title!r}")
    print(f"  nav tabs   : {n}")
    print(f"  board cols : {cols}")
    print(f"  player rows: {players}")
    print(f"  js errors  : {errors if errors else 'none'}")

    ok = (title == "BOOTLEGGER" and n == EXPECTED_TABS
          and cols >= 6 and players > 50 and not errors)
    if n != EXPECTED_TABS:
        print(f"  !! expected {EXPECTED_TABS} tabs; bump EXPECTED_TABS if a room was added")
    print("\nOK: render clean" if ok else "\nFAIL: render check failed")
    return 0 if ok else 1


def cmd_shots(out: pathlib.Path) -> int:
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    failures = []
    with sync_playwright() as pw:
        b, pg = _page(pw, errors)
        pg.goto(BASE, wait_until="networkidle")
        pg.wait_for_timeout(2000)
        tabs = _tabs(pg)
        for i in range(tabs.count()):
            el = tabs.nth(i)
            label = (el.inner_text() or "").split("\n")[0].strip()
            slug = "".join(c if c.isalnum() else "-" for c in label.lower())[:20]
            try:
                el.click(timeout=5000)
                pg.wait_for_timeout(1800)
                path = out / f"tab-{i}-{slug}.png"
                pg.screenshot(path=str(path))
                print(f"  OK   {label:<12} -> {path.name}")
            except Exception as e:
                print(f"  FAIL {label}: {str(e)[:100]}")
                failures.append(label)
        b.close()

    print(f"\njs errors: {errors if errors else 'none'}")
    print(f"screenshots in {out}")
    if failures or errors:
        print(f"FAIL: {failures=}")
        return 1
    print("OK: all tabs rendered")
    return 0


def cmd_flow(out: pathlib.Path) -> int:
    """Drive the seeded lineup rec proposed -> verified through the real UI.

    Needs a pending rec. A demo DB whose rec was already approved will skip —
    restore it by deleting data/bootlegger.db and restarting (SKILL.md Gotchas).
    """
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)
    _, recs = _get("/api/recs")
    if not recs:
        print("SKIP: no rec seeded at all")
        return 0
    rec = recs[0]
    print(f"  rec #{rec['rec_id']} state={rec.get('status') or rec.get('state')}")

    errors: list[str] = []
    with sync_playwright() as pw:
        b, pg = _page(pw, errors)
        pg.goto(BASE, wait_until="networkidle")
        pg.wait_for_timeout(2000)
        _tabs(pg).nth(1).click()          # THIS WEEK
        pg.wait_for_timeout(1500)
        pg.screenshot(path=str(out / "flow-1-before.png"))

        btn = pg.locator("#btn-approve")
        if btn.count() == 0 or not btn.is_enabled():
            print("  SKIP: no enabled #btn-approve (rec already executed?)")
            pg.screenshot(path=str(out / "flow-2-noop.png"))
            b.close()
            return 0

        btn.click()
        # Server-side: queued -> pre_verify -> act -> post_verify -> verified.
        deadline = time.time() + 45
        steps = []
        while time.time() < deadline:
            _, audit = _get("/api/audit")
            steps = [a["step"] for a in audit if a["rec_id"] == rec["rec_id"]]
            if "state:verified" in steps:
                break
            time.sleep(1.5)
        pg.wait_for_timeout(2000)
        pg.screenshot(path=str(out / "flow-3-after.png"))
        b.close()

    print("  audit steps:", " <- ".join(steps[:8]))
    ok = "state:verified" in steps and "post_verify_ok" in steps
    print("\nOK: rec reached verified" if ok else "\nFAIL: rec never verified")
    return 0 if ok else 1


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    out = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else "shots")

    if cmd == "api":
        return cmd_api()
    if cmd == "check":
        return cmd_check()
    if cmd == "shots":
        return cmd_shots(out)
    if cmd == "flow":
        return cmd_flow(out)
    if cmd == "all":
        rc = 0
        for name, fn in (("api", cmd_api), ("check", cmd_check),
                         ("shots", lambda: cmd_shots(out)),
                         ("flow", lambda: cmd_flow(out))):
            print(f"\n=== {name} ===")
            rc |= fn()
        return rc
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
