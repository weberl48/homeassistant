"""The back room. Tier-2 actuation worker (design doc §5).

Scope lock, enforced at the code level: this package contains exactly one
operation — a lineup swap. The job schema is validated strictly; a payload
carrying anything besides swaps is aborted. Waivers, FAAB, and trades have no
code path here, and none may be added.

Execute-verify loop: pre-verify (API state must match the hash the approval
saw) → act (dry-run mutates the local mirror; live drives Sleeper's web UI via
hands.browser) → post-verify (re-read until the API shows the target lineup).
Success claims require API evidence, never the browser's word."""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone

from app import brain, db, push, rules
from app.config import settings
from app.ingest import ping_healthchecks
from app.recs import transition

log = logging.getLogger("bootlegger.hands")

JOB_KEYS = {"rec_id", "week", "swaps", "lineup_hash_expected", "expires_at"}
SWAP_KEYS = {"out_id", "in_id", "slot"}
POST_VERIFY_TIMEOUT_S = 60.0


class ScopeViolation(ValueError):
    """A job asked for something other than a lineup swap."""


def validate_job(payload: dict) -> None:
    if set(payload) != JOB_KEYS:
        raise ScopeViolation(f"job keys {sorted(set(payload) ^ JOB_KEYS)} outside the lineup-swap contract")
    if not payload["swaps"]:
        raise ScopeViolation("job has no swaps")
    for s in payload["swaps"]:
        if set(s) != SWAP_KEYS:
            raise ScopeViolation(f"swap keys {sorted(set(s) ^ SWAP_KEYS)} outside the lineup-swap contract")


def read_starters(conn: sqlite3.Connection) -> list[str]:
    """The 'public API' read. Demo mode's mirror *is* the rosters table; live
    mode refreshes it from Sleeper first so verification is against reality."""
    if settings.mode == "live":
        from app.ingest import etl_rosters
        from app.sleeper import SleeperClient
        etl_rosters(SleeperClient(), conn)
    row = brain.my_roster_row(conn)
    return json.loads(row["starters_json"]) if row else []


def apply_swaps(starters: list[str], swaps: list[dict]) -> list[str]:
    out = list(starters)
    for s in swaps:
        if s["out_id"] in out:
            out[out.index(s["out_id"])] = s["in_id"]
    return out


def _write_starters_demo(conn: sqlite3.Connection, starters: list[str]) -> None:
    conn.execute("UPDATE rosters SET starters_json=?, updated_at=? WHERE roster_id=?",
                 (json.dumps(starters), db.utcnow(), settings.my_roster_id))
    conn.commit()


def _fail(conn, job_id: int, rec_id: int, step: str, detail: dict, urgent_body: str) -> None:
    conn.execute("UPDATE jobs SET state='failed', finished_at=? WHERE job_id=?",
                 (db.utcnow(), job_id))
    conn.commit()
    db.log_action(conn, rec_id, step, after=detail)
    row = conn.execute("SELECT state FROM recommendations WHERE rec_id=?", (rec_id,)).fetchone()
    if row and row["state"] in ("approved", "executed"):
        transition(conn, rec_id, "failed")
    push.send(conn, "Back room hit a snag", urgent_body, push.CHANNEL_EMERGENCY,
              data={"rec_id": rec_id})


def run_once(conn: sqlite3.Connection) -> bool:
    """Consume at most one queued job. Returns True when a job was processed."""
    job = conn.execute(
        "SELECT * FROM jobs WHERE state='queued' ORDER BY job_id LIMIT 1").fetchone()
    if not job:
        return False
    job_id, rec_id = job["job_id"], job["rec_id"]
    payload = json.loads(job["payload_json"])

    conn.execute("UPDATE jobs SET state='running' WHERE job_id=?", (job_id,))
    conn.commit()

    # Contract checks -------------------------------------------------------
    try:
        validate_job(payload)
    except ScopeViolation as e:
        _fail(conn, job_id, rec_id, "scope_violation", {"error": str(e)},
              "Job refused: outside the lineup-swap contract.")
        return True

    if datetime.fromisoformat(payload["expires_at"]) < datetime.now(timezone.utc):
        conn.execute("UPDATE jobs SET state='expired', finished_at=? WHERE job_id=?",
                     (db.utcnow(), job_id))
        conn.commit()
        db.log_action(conn, rec_id, "job:expired")
        row = conn.execute("SELECT state FROM recommendations WHERE rec_id=?", (rec_id,)).fetchone()
        if row and row["state"] == "approved":
            transition(conn, rec_id, "failed")
        push.send(conn, "Approval expired", "The window closed before the swap ran — "
                  "set your lineup in Sleeper.", push.CHANNEL_EMERGENCY, data={"rec_id": rec_id})
        return True

    fired = rules.evaluate(conn, payload["swaps"], payload["week"])
    if fired:
        _fail(conn, job_id, rec_id, "dont_act_rule_fired", {"rules": fired},
              f"Held by don't-act rule: {', '.join(fired)}. Set your lineup manually.")
        return True

    # Pre-verify ------------------------------------------------------------
    before = read_starters(conn)
    if brain.lineup_hash(before) != payload["lineup_hash_expected"]:
        conn.execute("UPDATE jobs SET state='aborted', finished_at=? WHERE job_id=?",
                     (db.utcnow(), job_id))
        conn.commit()
        db.log_action(conn, rec_id, "pre_verify_mismatch", before=before)
        transition(conn, rec_id, "failed")
        push.send(conn, "Lineup changed under us",
                  "The lineup moved since you approved — check Sleeper.",
                  push.CHANNEL_EMERGENCY, data={"rec_id": rec_id})
        return True
    db.log_action(conn, rec_id, "pre_verify_ok", before=before)

    target = apply_swaps(before, payload["swaps"])
    if before == target:
        # Idempotent by construction: already applied.
        conn.execute("UPDATE jobs SET state='done', finished_at=? WHERE job_id=?",
                     (db.utcnow(), job_id))
        conn.commit()
        db.log_action(conn, rec_id, "noop_already_applied", after=target)
        for st in ("executed", "verified"):
            transition(conn, rec_id, st)
        return True

    # Act -------------------------------------------------------------------
    if settings.hands_dry_run and settings.mode == "live":
        # A live dry run must not pretend. The rosters mirror is REAL data
        # here (writing a fake lineup into it corrupts every reader until the
        # next sync), and post-verify against the actual API would always
        # fail and cry wolf. So: touch nothing, park the rec in its own
        # terminal state (the scanner dedups on it), tell the user plainly.
        conn.execute("UPDATE jobs SET state='done', finished_at=? WHERE job_id=?",
                     (db.utcnow(), job_id))
        conn.commit()
        db.log_action(conn, rec_id, "act:dry_run_noop", before=before, after=target)
        transition(conn, rec_id, "dry_run")
        names = _swap_names(conn, payload["swaps"])
        push.send(conn, "Dry run — nothing touched",
                  " · ".join(f"{i} in for {o}" for o, i in names)
                  + ". Hands are in dry-run; set it in Sleeper yourself if you agree.",
                  push.CHANNEL_NORMAL, data={"rec_id": rec_id})
        return True
    try:
        if settings.hands_dry_run:
            db.log_action(conn, rec_id, "act:dry_run_swap", before=before, after=target)
            _write_starters_demo(conn, target)
        else:
            from . import browser
            shots = browser.perform_swaps(payload["swaps"], rec_id)
            for step, path in shots:
                db.log_action(conn, rec_id, f"act:{step}", screenshot_path=path)
    except Exception as e:  # any actuation failure degrades to a notification
        _fail(conn, job_id, rec_id, "act_failed", {"error": str(e)},
              "Swap attempt failed — set your lineup in Sleeper now.")
        return True
    transition(conn, rec_id, "executed")

    # Post-verify ------------------------------------------------------------
    deadline = time.time() + (0 if settings.hands_dry_run else POST_VERIFY_TIMEOUT_S)
    while True:
        after = read_starters(conn)
        if after == target:
            break
        if time.time() >= deadline:
            _fail(conn, job_id, rec_id, "post_verify_timeout",
                  {"expected": target, "actual": after},
                  "Swap did not verify against the API — check Sleeper immediately.")
            return True
        time.sleep(2.0)
    conn.execute("UPDATE jobs SET state='done', finished_at=? WHERE job_id=?",
                 (db.utcnow(), job_id))
    conn.commit()
    db.log_action(conn, rec_id, "post_verify_ok", before=before, after=after)
    transition(conn, rec_id, "verified")

    names = _swap_names(conn, payload["swaps"])
    push.send(conn, "Done and verified",
              " · ".join(f"{i} in for {o}" for o, i in names) + " ✅",
              push.CHANNEL_NORMAL, data={"rec_id": rec_id})
    ping_healthchecks(ok=True)
    return True


def _swap_names(conn, swaps: list[dict]) -> list[tuple[str, str]]:
    def name(pid: str) -> str:
        r = conn.execute("SELECT name FROM players WHERE sleeper_id=?", (pid,)).fetchone()
        return r["name"] if r else pid
    return [(name(s["out_id"]), name(s["in_id"])) for s in swaps]


def run_loop(poll_seconds: float = 2.0) -> None:
    conn = db.connect()
    db.init_db(conn)
    if not settings.hands_dry_run:
        # Armed hands must prove they CAN act before consuming a single job —
        # discovering a missing browser mid-act would burn an approval on an
        # ImportError. Fail loud at boot; the restart loop keeps it visible.
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError as e:
            log.critical("HANDS_DRY_RUN=0 but Playwright is not installed in "
                         "this image — refusing to start: %s", e)
            raise SystemExit(2)
    log.info("hands worker up (dry_run=%s, approve_required=%s)",
             settings.hands_dry_run, settings.approve_required)
    while True:
        try:
            if not run_once(conn):
                time.sleep(poll_seconds)
        except Exception:
            log.exception("hands loop error")
            time.sleep(poll_seconds)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_loop()
