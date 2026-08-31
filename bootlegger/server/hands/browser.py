"""Playwright flow for the one real-world operation: tap-to-swap lineup edits
on the Sleeper web team page. Runs only in live mode with HANDS_DRY_RUN=0, a
mounted storageState.json, and a calibrated selector map (rehearse in the test
league first — design doc §5.8).

Locators are accessibility role/text, never CSS classes (React class churn).
Pacing is randomized 0.8–2.5s. Every step screenshots to audit/. This module
never decides anything; it executes a validated job and reports."""
from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path

from app.config import settings

SELECTOR_MAP_PATH = Path(__file__).parent / "selector_map.json"
# WHERE THE SLEEPER SESSION LIVES — one owner for the whole package.
#
# This module knew only the compose-secret path while draft_pilot.py searched
# three candidates and fell back to /data. So the two halves of the same
# subsystem looked for the same credential in different places, and on the Pi
# the lineup swapper reported "storageState secret missing" while a perfectly
# good session sat at /data/.sleeper_storage_state, which the draft pilot found
# without trouble. Same fact, two copies, diverged — so it lives here now and
# draft_pilot imports it.
_STATE_CANDIDATES = [p for p in (os.environ.get("BOOTLEGGER_STATE_FILE", "").strip(),
                                 "/run/secrets/sleeper_storage_state",
                                 "/data/.sleeper_storage_state") if p]


def state_file() -> Path | None:
    """The first candidate that is actually a file, or None."""
    return next((Path(p) for p in _STATE_CANDIDATES if Path(p).is_file()), None)

# Chromium on a Raspberry Pi with ~2GB free. The first live attempt died with
# "Target crashed" mid-swap — the OOM this project had already seen once, in a
# draft room. Single-process with a capped JS heap survives it; the container
# also needs --memory=2g and --shm-size=512m (see ops/pi/deploy.sh), because
# the default 1g limit is not enough to render a hydrated React roster.
CHROMIUM_ARGS = [
    "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
    "--single-process", "--no-zygote", "--disable-extensions",
    "--disable-background-networking", "--js-flags=--max-old-space-size=256",
]


class ReauthNeeded(RuntimeError):
    pass


class NotCalibrated(RuntimeError):
    pass


def _map() -> dict:
    return json.loads(SELECTOR_MAP_PATH.read_text())


def _pace(m: dict) -> None:
    p = m["pacing_seconds"]
    time.sleep(random.uniform(p["min"], p["max"]))


def _shoot(page, rec_id: int, step: str) -> str:
    settings.audit_dir.mkdir(parents=True, exist_ok=True)
    path = settings.audit_dir / f"rec{rec_id}_{int(time.time())}_{step}.png"
    page.screenshot(path=str(path), full_page=True)
    return str(path)


def _row(page, m: dict, full_name: str):
    """The roster row for a player, matched on LAST NAME.

    Sleeper prints "J Dobbins", not "J.K. Dobbins" — matching the full name
    from our own players table finds nothing. The surname is the stable part.
    Anything other than exactly one match raises: two men sharing a surname on
    one roster is rare and clicking the wrong one is not recoverable.
    """
    surname = full_name.split()[-1].strip(".")
    sel = m["locators"]["row_by_name"].format(name=surname)
    loc = page.locator(sel)
    n = loc.count()
    if n != 1:
        raise RuntimeError(
            f"{full_name!r} matched {n} roster rows on surname {surname!r}; "
            f"refusing to guess which man to move")
    return loc.first


def perform_swaps(swaps: list[dict], rec_id: int) -> list[tuple[str, str]]:
    """Returns [(step, screenshot_path)]. Raises on anything unexpected — the
    worker turns every raise into a failed job plus an urgent notification.

    The gesture, calibrated against the live page on 2026-08-31: click the
    POSITION BUTTON of the man to move, which marks his row `.selected` and
    every other row `.valid` or `.invalid`; then click the position button of a
    `.valid` row. There is no confirm step — the second click writes, via a
    `update_matchup_leg` GraphQL mutation. Nothing here can undo it, so the
    validity check before that second click is the last line of defence.
    """
    m = _map()
    if not m.get("calibrated"):
        raise NotCalibrated(
            "selector_map.json is not calibrated — rehearse in the test league "
            "and record the real locators before pointing hands at anything.")
    state = state_file()
    if state is None:
        raise ReauthNeeded(
            "no Sleeper session found in any of " + ", ".join(_STATE_CANDIDATES))

    from playwright.sync_api import sync_playwright  # imported lazily; optional dep

    from app.db import connect
    from app.brain import _players_index
    names = {pid: r["name"] for pid, r in _players_index(connect()).items()}

    shots: list[tuple[str, str]] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=CHROMIUM_ARGS)
        # A viewport, explicitly. Chromium defaults to 1280x720, at which
        # Sleeper renders a narrower layout with no .team-roster at all —
        # the probe found nothing and a perfectly good session read as
        # expired.
        ctx = browser.new_context(storage_state=str(state),
                                  viewport={"width": 1400, "height": 1100})
        page = ctx.new_page()
        url = m["team_page_url"].format(league_id=settings.league_id)
        # The Pi fails this three different ways: ERR_NETWORK_CHANGED on
        # navigation, a navigation that succeeds onto an EMPTY body, or it
        # simply works. Only the first is a goto error, so retrying goto alone
        # left the empty-render case looking like an expired session. Retry on
        # the thing actually wanted — the roster being on the page.
        roster_sel = m["locators"]["row_by_name"].split(":has")[0]
        for attempt in range(4):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_selector(roster_sel, timeout=20000)
                break
            except Exception:
                if attempt == 3:
                    shots.append(("blank", _shoot(page, rec_id, "blank")))
                    raise RuntimeError(
                        "the team page never rendered a roster after 4 attempts "
                        "— the browser reached Sleeper but the app did not come "
                        "up. Seen on the Pi; the same session renders fine "
                        "elsewhere, so suspect this host's network path.")
                page.wait_for_timeout(3000)
        shots.append(("loaded", _shoot(page, rec_id, "loaded")))

        if not page.locator(m["locators"]["logged_in_probe"]).count():
            shots.append(("reauth", _shoot(page, rec_id, "reauth")))
            raise ReauthNeeded("session expired — export a fresh storageState.json")

        square = m["locators"]["slot_square"]
        for i, s in enumerate(swaps):
            _pace(m)
            _row(page, m, names[s["out_id"]]).locator(square).click()
            page.wait_for_timeout(1200)
            shots.append((f"tap_out_{i}", _shoot(page, rec_id, f"tap_out_{i}")))

            dest = _row(page, m, names[s["in_id"]])
            cls = dest.get_attribute("class") or ""
            # valid/invalid land on the ROW, never on the square. Reading the
            # square finds neither and would abort every legal swap.
            if "valid" not in cls.split() or "invalid" in cls.split():
                shots.append((f"refused_{i}", _shoot(page, rec_id, f"refused_{i}")))
                raise RuntimeError(
                    f"{names[s['in_id']]} is not a legal destination for "
                    f"{names[s['out_id']]} (row classes: {cls!r})")

            _pace(m)
            dest.locator(square).click()     # THIS WRITES
            page.wait_for_timeout(2500)
            shots.append((f"tap_in_{i}", _shoot(page, rec_id, f"tap_in_{i}")))

        shots.append(("done", _shoot(page, rec_id, "done")))
        browser.close()
    return shots


def _re(pattern: str, **kw: str) -> "object":
    import re
    return re.compile(pattern.format(**kw) if kw else pattern)


def canary(rec_id: int = 0) -> dict:
    """Wednesday dry-run (design doc §5.7): walk the flow up to — not including
    — the final tap, and report which locators still resolve."""
    m = _map()
    state = state_file()
    if not m.get("calibrated") or state is None:
        return {"ok": False, "reason": "not calibrated or no storageState"}
    from playwright.sync_api import sync_playwright
    results = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=CHROMIUM_ARGS)
        # A viewport, explicitly. Chromium defaults to 1280x720, at which
        # Sleeper renders a narrower layout with no .team-roster at all —
        # the probe found nothing and a perfectly good session read as
        # expired.
        ctx = browser.new_context(storage_state=str(state),
                                  viewport={"width": 1400, "height": 1100})
        page = ctx.new_page()
        page.goto(m["team_page_url"].format(league_id=settings.league_id),
                  wait_until="networkidle")
        for key, loc in m["locators"].items():
            try:
                results[key] = page.get_by_role(loc["role"], name=_re(loc["name_re"], player_name=".")).count() > 0
            except Exception:
                results[key] = False
        _shoot(page, rec_id, "canary")
        browser.close()
    return {"ok": all(results.values()), "locators": results}
