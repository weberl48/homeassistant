"""The Draft Pilot — unattended picking, behind every lock we own.

Sleeper's public API is read-only: there is no pick endpoint. Picking for an
absent owner therefore means driving the logged-in draft room in a browser,
exactly like the lineup hands. This worker:

  watch the board (same DB the API serves)
    -> on MY clock, ARMED, and not dry-run:
         choose: first slip player still available, else The Call
         drive the room: search the name, tap the player, tap Draft, confirm
         verify: the pick must appear in the pick feed within the window
    -> anything unexpected: STOP, disarm, log — Sleeper's own timer/queue
       autopick is the fallback of last resort, so a dead pilot costs at
       worst what having no pilot would have cost.

Safety locks (ALL must open):
  1. meta pilot_armed == "1"  (the UI toggle — disarm any time, takes effect
     within a second)
  2. HANDS_DRY_RUN=0          (env; default 1 logs would-picks only)
  3. /run/secrets/sleeper_storage_state mounted (your logged-in session)
  4. selector_map.json draft_room section calibrated:true (rehearsed in a
     practice room first — scrimmage mode exists for exactly this)

Run:  python -m hands.draft_pilot   (needs playwright in the environment;
      the main API/ingest containers deliberately do not carry it)
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from app import brain, db
from app.config import settings

log = logging.getLogger("bootlegger.pilot")

SELECTOR_MAP_PATH = Path(__file__).parent / "selector_map.json"
STORAGE_STATE_PATH = Path("/run/secrets/sleeper_storage_state")
POST_VERIFY_TIMEOUT_S = 12.0
POLL_S = 1.0


def _armed(conn) -> bool:
    return db.meta_get(conn, "pilot_armed") == "1"


def _log_event(conn, kind: str, detail: dict) -> None:
    events = json.loads(db.meta_get(conn, "pilot_log") or "[]")
    events.append({"ts": db.utcnow(), "kind": kind, **detail})
    db.meta_set(conn, "pilot_log", json.dumps(events[-50:]))  # last 50 only


def _draft_map() -> dict:
    m = json.loads(SELECTOR_MAP_PATH.read_text()).get("draft_room") or {}
    if not m.get("calibrated"):
        raise RuntimeError("draft_room selectors not calibrated — rehearse in "
                           "a scrimmage before arming for real")
    return m


def _perform_pick(conn, draft_id: str, name: str) -> None:
    """Drive the room. Role/text locators only (React class churn); every
    step screenshots to the audit dir; any surprise raises."""
    from playwright.sync_api import sync_playwright  # lazy: pilot-only dep

    m = _draft_map()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = browser.new_context(storage_state=str(STORAGE_STATE_PATH))
        page = ctx.new_page()
        page.goto(f"https://sleeper.com/draft/nfl/{draft_id}", timeout=30000)
        page.wait_for_timeout(2500)
        settings.audit_dir.mkdir(parents=True, exist_ok=True)

        def shoot(step: str) -> None:
            page.screenshot(
                path=str(settings.audit_dir / f"pilot_{int(time.time())}_{step}.png"))

        search = page.get_by_role(m["search"]["role"], name=None).or_(
            page.get_by_placeholder(m["search"]["placeholder_re"]))
        search.first.fill(name)
        shoot("searched")
        page.get_by_text(name, exact=False).first.click()
        shoot("selected")
        page.get_by_role("button", name=m["draft_button"]["name_re"]).first.click()
        shoot("drafted")
        browser.close()


def run_once(conn) -> None:
    board = brain.get_board(conn)
    d = board["draft"]
    if d["status"] != "drafting" or not d["on_the_clock_me"]:
        return
    if not _armed(conn):
        return

    picked = {p["id"] for p in board["players"] if p.get("pick_no")}
    queue_ids = json.loads(db.meta_get(conn, "draft_queue") or "[]")
    choice = brain.resolve_pilot_pick(queue_ids, picked, board["suggestions"])
    if choice is None:
        _log_event(conn, "no_pick", {"pick_no": d["current_pick"]})
        return
    pid, source = choice
    name = next((p["name"] for p in board["players"] if p["id"] == pid), pid)

    if settings.hands_dry_run:
        log.info("DRY RUN — would draft %s (from the %s) at pick %s",
                 name, source, d["current_pick"])
        _log_event(conn, "dry_run", {"name": name, "source": source,
                                     "pick_no": d["current_pick"]})
        time.sleep(5.0)  # don't spam the log every poll while on the clock
        return

    log.info("PILOT PICKING: %s (from the %s) at pick %s",
             name, source, d["current_pick"])
    try:
        _perform_pick(conn, d["id"], name)
    except Exception as e:
        # One failure disarms the pilot entirely: a wounded automaton must
        # not flail at the room. Sleeper's own autopick takes over on timer.
        db.meta_set(conn, "pilot_armed", "0")
        _log_event(conn, "failed_disarmed", {"name": name, "error": str(e)[:200]})
        log.exception("pilot pick failed — DISARMED")
        return

    deadline = time.time() + POST_VERIFY_TIMEOUT_S
    while time.time() < deadline:
        fresh = {p["id"] for p in brain.get_board(conn)["players"] if p.get("pick_no")}
        if pid in fresh:
            _log_event(conn, "picked", {"name": name, "source": source,
                                        "pick_no": d["current_pick"]})
            return
        time.sleep(1.5)
    db.meta_set(conn, "pilot_armed", "0")
    _log_event(conn, "verify_timeout_disarmed", {"name": name})
    log.error("pick did not verify in the feed — DISARMED, check the room")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    conn = db.connect()
    log.info("draft pilot up (dry_run=%s, storage_state=%s)",
             settings.hands_dry_run, STORAGE_STATE_PATH.exists())
    while True:
        try:
            run_once(conn)
        except Exception:
            log.exception("pilot loop error")
        time.sleep(POLL_S)


if __name__ == "__main__":
    main()
