"""The Draft Pilot — unattended picking, behind every lock we own.

Sleeper's public API is read-only: there is no pick endpoint. Picking for an
absent owner therefore means driving the logged-in draft room in a browser.
Calibrated LIVE 2026-08-25 (practice room, verified pick Harold Fannin P71):
search box placeholder "Find player", row control `.draft-button-wrapper`,
text confirm "Draft".

Architecture (proven in the rehearsal, in this order of hard lessons):
  - HTTP transport: the pilot talks to the board API, not the DB, so it can
    run on ANY machine with Playwright + LAN access. The Pi's Chromium
    crashed under a live room (renderer OOM at ~1.9GB free); the desktop
    flew the same draft flawlessly. Host is a deploy-time choice.
  - Persistent page: the room stays open across picks. A fresh browser per
    pick burns ~30s of a 60s clock on hydration alone.
  - One attempt per clock, verified against the pick feed; any failure or
    unverified pick DISARMS via the API and leaves the rest to Sleeper's
    own timer autopick — a dead pilot costs exactly what no pilot costs.

Safety locks (ALL must open before a real click):
  1. /api/queue reports pilot_armed (the UI toggle; disarm reacts in ~1s)
  2. HANDS_DRY_RUN=0 (env; default 1 logs would-picks only)
  3. auth: a session state file captured from the owner's own browser
     (BOOTLEGGER_STATE_FILE or the defaults). Credential login is NOT a
     substitute — Sleeper gates it behind hCaptcha for automated browsers
     (see hands/sleeper_login); creds are tried only as a last resort.
  4. selector_map.json draft_room.calibrated == true

Run:  python -m hands.draft_pilot [--api http://192.168.1.160:8484]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path

import httpx

from . import browser, sleeper_login

log = logging.getLogger("bootlegger.pilot")

LOGIN_USER = os.environ.get("SLEEPER_LOGIN_USER", "")
LOGIN_PW = os.environ.get("SLEEPER_LOGIN_PW", "")

SELECTOR_MAP_PATH = Path(__file__).parent / "selector_map.json"
# An UNSET BOOTLEGGER_STATE_FILE used to enter this list as Path(""), whose
# str() is "." and whose .exists() is True — so the search always stopped on
# the working DIRECTORY and the real session file was never reached. Playwright
# would then be handed a directory as storage_state on draft night. Skip blank
# values, and require an actual file: a directory is not a session.
# The session's home is hands/browser.py — one owner, imported not copied.
POST_VERIFY_TIMEOUT_S = 15.0
# ONE PARSER FOR ONE SWITCH. This read `!= "0"` while app/config.py reads
# `not in ("0", "false", "no")`, and ops/pi/deploy.sh pipes the operator's raw
# shell value into both. So `HANDS_DRY_RUN=false` gave the server False and the
# pilot True: /api/queue reported pilot_dry_run=False, the board rendered
# "ARMED — LIVE", and the pilot quietly logged and never acted. The dangerous
# direction is exactly this one — the board claiming more capability than the
# hands have. Importing the server's own parser makes the disagreement
# impossible rather than merely unlikely.
_DRY_RUN_FALSE = ("0", "false", "no")
DRY_RUN = os.environ.get("HANDS_DRY_RUN", "1").strip().lower() not in _DRY_RUN_FALSE


def _page_alive(page) -> bool:
    """Is this page still attached to a browser that exists?

    Cheap non-blocking checks FIRST, and they are the ones that catch the
    failure this exists for: a renderer that dies closes its target and, on an
    OOM kill, usually takes the connection with it. Both answers are local —
    neither round-trips to the page.

    The evaluate() is a last resort and is the part I am least sure of. It
    catches a page that is nominally open but wedged, and Playwright's Python
    API gives evaluate no timeout of its own — so on a renderer that is hung
    rather than dead it can block, and blocking here costs a pick. It runs only
    after the cheap checks have said "probably alive", which bounds how often
    that can happen, and it is deliberately the last thing tried rather than
    the first. If a hung-but-connected renderer ever shows up in a real draft,
    this is the line to replace with a threaded probe.
    """
    try:
        if page.is_closed():
            return False
        ctx = getattr(page, "context", None)
        browser = getattr(ctx, "browser", None) if ctx else None
        if browser is not None and not browser.is_connected():
            return False
        page.evaluate("1")
        return True
    except Exception:                           # noqa: BLE001 — any failure is death
        return False


def _shut(browser) -> None:
    """Close a browser that may already be gone. Never raises: this is called
    on the failure path, and a teardown that throws would hide the failure it
    was cleaning up after."""
    try:
        if browser is not None:
            browser.close()
    except Exception:                           # noqa: BLE001
        pass


def state_file() -> Path | None:
    return browser.state_file()


def draft_map() -> dict:
    m = json.loads(SELECTOR_MAP_PATH.read_text(encoding="utf-8")).get("draft_room") or {}
    if not m.get("calibrated"):
        raise SystemExit("draft_room selectors not calibrated — rehearse in a "
                         "scrimmage before flying")
    return m


class Api:
    def __init__(self, base: str, token: str = ""):
        self.c = httpx.Client(base_url=base, timeout=6.0,
                              headers={"X-Bootlegger-Token": token} if token else {})

    def board(self) -> dict:
        return self.c.get("/api/draft/board").json()

    def queue(self) -> dict:
        return self.c.get("/api/queue").json()

    def disarm(self) -> None:
        try:
            self.c.post("/api/pilot/arm", json={"armed": False})
        except httpx.HTTPError:
            log.error("could not disarm via API — kill this process")


def choose(board: dict, queue: list[dict]) -> tuple[str, str, str] | None:
    """Slip first, The Call when the slip runs dry. (id, name, source).
    Deliberately mirrors brain.resolve_pilot_pick (test-pinned) rather than
    importing it — the pilot may run on a host without the app's deps."""
    picked = {p["id"] for p in board["players"] if p.get("pick_no")}
    for p in queue:
        if p["id"] not in picked:
            return p["id"], p["name"], "slip"
    for s in board["suggestions"]:
        if s["id"] not in picked:
            return s["id"], s["name"], "call"
    return None


def find_controls(page, m: dict, name: str):
    """Pre-click phase: search and locate the row's draft button. Failures
    here touched NOTHING and are retryable within the same clock (the
    rehearsal saw one transient locator timeout that the next attempt would
    have cleared)."""
    box = page.get_by_placeholder(m["search_placeholder"], exact=False).first
    box.fill(name)
    time.sleep(1.8)
    btn = page.get_by_text(name, exact=False).first.evaluate_handle(
        """(el, css) => { let n = el;
             for (let i = 0; i < 6 && n.parentElement; i++) {
               n = n.parentElement;
               const b = n.querySelector(css);
               if (b) return b; }
             return null; }""", m["row_draft_button_css"])
    el = btn.as_element()
    if el is None:
        raise RuntimeError("draft button not found in the result row")
    return box, el


def click_draft(page, m: dict, box, el) -> None:
    """Post-click phase: from here the room's state is unknown on failure —
    the caller must disarm, never retry."""
    el.click()
    time.sleep(1.2)
    confirm = page.get_by_text(m["confirm_text"], exact=False)
    if confirm.count():
        try:
            confirm.first.click(timeout=2000)
        except Exception:
            pass                       # no dialog — the row click drafted
    box.fill("")


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=os.environ.get(
        "BOOTLEGGER_API", "http://192.168.1.160:8484"))
    args = ap.parse_args()
    api = Api(args.api, os.environ.get("BOOTLEGGER_API_TOKEN", ""))
    m = draft_map()
    st = state_file()
    # Session file FIRST: credential login is captcha-gated, so it is only a
    # fallback for an interactive host where a human can solve the puzzle.
    has_creds = bool(LOGIN_USER and LOGIN_PW)
    if st is None and not has_creds and not DRY_RUN:
        raise SystemExit("no auth — capture a session state file first")
    log.info("pilot up (dry_run=%s, api=%s, auth=%s)", DRY_RUN, args.api,
             st or "credentials (captcha risk)")

    # A dry run never opens a page, so it must not need a browser to exist.
    # Importing Playwright unconditionally made the rehearsal impossible on any
    # host that wasn't already set up to fly for real — which is exactly the
    # host you want to rehearse on. tools/pilot_rehearsal.py depends on this.
    if DRY_RUN:
        log.info("DRY RUN — no browser will be launched, nothing will be clicked")
        return _fly(api, m, st, None)
    from playwright.sync_api import sync_playwright  # lazy: pilot-only dep
    with sync_playwright() as pw:
        return _fly(api, m, st, pw)


def _fly(api: "Api", m: dict, st: Path | None, pw) -> None:
    """The pilot's loop. `pw` is None on a dry run, where it is never used."""
    browser = ctx = page = None
    last_done = 0
    attempt_pick, attempts = 0, 0
    while True:
        try:
            board = api.board()
        except httpx.HTTPError:
            time.sleep(3); continue
        d = board["draft"]
        drafting = d["status"] == "drafting"

        if not drafting and browser:
            log.info("draft over — closing the room")
            browser.close(); browser = ctx = page = None
        if not drafting:
            time.sleep(10); continue

        armed = False
        queue: list[dict] = []
        try:
            q = api.queue()
            armed, queue = q["pilot_armed"], q["queue"]
        except httpx.HTTPError:
            pass
        if not armed:
            time.sleep(2); continue

        # Hold the room open the whole draft (hydration is too slow to
        # pay per pick).
        #
        # LIVENESS, NOT IDENTITY. This tested `page is None`, which a dead
        # browser never becomes: the module docstring records the real event —
        # "the Pi's Chromium crashed under a live room (renderer OOM at ~1.9GB
        # free)" — and after it the object is still there, still not None,
        # pointing at a closed target. The relaunch never fired, every
        # remaining clock raised TargetClosedError, and the board went on
        # reporting the pilot armed and flying while it silently did nothing.
        if page is not None and not _page_alive(page):
            log.warning("browser died mid-draft — relaunching")
            _shut(browser)
            browser = ctx = page = None

        if page is None and not DRY_RUN:
            # THE WHOLE BRINGUP IS GUARDED. Only the login call was, and only
            # for one exception class, so a missing chromium binary, a
            # truncated storage_state or a goto timeout escaped _fly, unwound
            # `with sync_playwright()` in main(), and killed the process
            # WITHOUT disarming — the one exit that leaves the board
            # advertising a pilot that no longer exists. All three are reached
            # only after arming, which is exactly when it matters.
            try:
                browser = pw.chromium.launch(
                    args=["--no-sandbox", "--disable-dev-shm-usage"])
                if st is not None:
                    ctx = browser.new_context(storage_state=str(st))
                    page = ctx.new_page()
                else:
                    ctx = browser.new_context()
                    page = ctx.new_page()
                    try:
                        ok = sleeper_login.login(page, LOGIN_USER, LOGIN_PW)
                    except sleeper_login.CaptchaBlocked as e:
                        log.error("%s — DISARMING", e)
                        ok = False
                    if not ok:
                        api.disarm()
                        _shut(browser)
                        browser = ctx = page = None
                        time.sleep(30)
                        continue
                    log.info("logged in with credentials")
                page.goto(f"https://sleeper.com/draft/nfl/{d['id']}", timeout=60000)
                for _ in range(25):
                    time.sleep(1.5)
                    if page.evaluate("document.body.innerText.length") > 3000:
                        break
                log.info("room open and hydrated")
            except Exception as exc:            # noqa: BLE001
                # Disarm FIRST, then tidy up: the board must never outlive the
                # pilot's ability to fly.
                log.exception("browser bringup failed — DISARMING (%s)",
                              type(exc).__name__)
                try:
                    api.disarm()
                except httpx.HTTPError:
                    log.error("could not reach the API to disarm — the board "
                              "may still show the pilot armed")
                _shut(browser)
                browser = ctx = page = None
                time.sleep(30)
                continue

        if not d["on_the_clock_me"] or d["current_pick"] == last_done:
            time.sleep(0.8); continue

        pick_no = d["current_pick"]
        ch = choose(board, queue)
        if ch is None:
            log.warning("pick %s: nothing to take", pick_no)
            last_done = pick_no; continue
        pid, name, source = ch
        if DRY_RUN:
            log.info("DRY RUN — would draft %s (from the %s) at pick %s",
                     name, source, pick_no)
            last_done = pick_no
            time.sleep(3); continue

        log.info("pick %s: taking %s (from the %s)", pick_no, name, source)
        if pick_no != attempt_pick:
            attempt_pick, attempts = pick_no, 0
        try:
            box, el = find_controls(page, m, name)
        except Exception as e:
            attempts += 1
            log.warning("find failed (attempt %s/3): %s", attempts, str(e)[:120])
            if attempts >= 3:
                log.error("giving this clock to the timer — pilot stays armed")
                last_done = pick_no
            time.sleep(1.2); continue
        try:
            click_draft(page, m, box, el)
        except Exception:
            log.exception("CLICK PATH failed — DISARMING (room state unknown)")
            api.disarm()
            last_done = pick_no; continue
        deadline = time.time() + POST_VERIFY_TIMEOUT_S
        ok = False
        while time.time() < deadline:
            time.sleep(1.5)
            try:
                fresh = api.board()
            except httpx.HTTPError:
                continue
            got = next((p for p in fresh["players"]
                        if p["id"] == pid and p.get("pick_no")), None)
            if got:
                log.info("  VERIFIED %s at pick %s mine=%s",
                         name, got["pick_no"], got.get("mine"))
                ok = True; break
        if not ok:
            log.error("  NOT verified — DISARMING, check the room")
            api.disarm()
        last_done = pick_no


if __name__ == "__main__":
    main()
