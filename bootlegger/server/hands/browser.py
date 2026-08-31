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

CHROMIUM_ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]


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


def perform_swaps(swaps: list[dict], rec_id: int) -> list[tuple[str, str]]:
    """Returns [(step, screenshot_path)]. Raises on anything unexpected — the
    worker turns every raise into a failed job plus an urgent notification."""
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
    from app.brain import _players_index  # names for role/text locators
    names = {pid: r["name"] for pid, r in _players_index(connect()).items()}

    shots: list[tuple[str, str]] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=CHROMIUM_ARGS)
        ctx = browser.new_context(storage_state=str(state))
        page = ctx.new_page()
        page.goto(m["team_page_url"].format(league_id=settings.league_id),
                  wait_until="networkidle")
        shots.append(("loaded", _shoot(page, rec_id, "loaded")))

        probe = m["locators"]["logged_in_probe"]
        if not page.get_by_role(probe["role"], name=_re(probe["name_re"])).count():
            shots.append(("reauth", _shoot(page, rec_id, "reauth")))
            raise ReauthNeeded("session expired — export a fresh storageState.json")

        for i, s in enumerate(swaps):
            _pace(m)
            row = m["locators"]["starter_row"]
            page.get_by_role(row["role"], name=_re(row["name_re"], player_name=names[s["out_id"]])).first.click()
            shots.append((f"tap_out_{i}", _shoot(page, rec_id, f"tap_out_{i}")))
            _pace(m)
            row = m["locators"]["bench_row"]
            page.get_by_role(row["role"], name=_re(row["name_re"], player_name=names[s["in_id"]])).first.click()
            shots.append((f"tap_in_{i}", _shoot(page, rec_id, f"tap_in_{i}")))
        _pace(m)
        confirm = m["locators"]["confirm_swap"]
        loc = page.get_by_role(confirm["role"], name=_re(confirm["name_re"]))
        if loc.count():
            loc.first.click()
        shots.append(("confirmed", _shoot(page, rec_id, "confirmed")))
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
        ctx = browser.new_context(storage_state=str(state))
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
