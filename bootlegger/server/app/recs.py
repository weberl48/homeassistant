"""Recommendation lifecycle (design doc §3):
proposed → notified → approved|snoozed|ignored → executed → verified|failed.
Approval is the only thing that can enqueue a hands job."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from . import brain, db, push, rules
from .config import settings

TRANSITIONS: dict[str, set[str]] = {
    "proposed": {"notified", "approved", "snoozed", "ignored"},
    "notified": {"approved", "snoozed", "ignored"},
    "snoozed": {"approved", "ignored", "notified"},
    "approved": {"executed", "failed"},
    "executed": {"verified", "failed"},
    "ignored": set(), "verified": set(), "failed": set(),
}

JOB_TTL_H = 2.0  # the undo window; expired jobs are dropped, never retried


class BadTransition(ValueError):
    pass


def _get(conn: sqlite3.Connection, rec_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM recommendations WHERE rec_id=?", (rec_id,)).fetchone()
    if not row:
        raise KeyError(f"rec {rec_id} not found")
    return row


def transition(conn: sqlite3.Connection, rec_id: int, to_state: str) -> None:
    row = _get(conn, rec_id)
    if to_state not in TRANSITIONS.get(row["state"], set()):
        raise BadTransition(f"rec {rec_id}: {row['state']} → {to_state} not allowed")
    conn.execute("UPDATE recommendations SET state=? WHERE rec_id=?", (to_state, rec_id))
    conn.commit()
    db.log_action(conn, rec_id, f"state:{to_state}")


def scan_lineup(conn: sqlite3.Connection, week: int = 1) -> int | None:
    """Poll step: propose a lineup rec when the diff is material and no open
    rec already covers it. Returns the rec_id when one was created."""
    card = brain.get_week_card(conn, week)
    if not card.get("ready") or not card["material"] or not card["swaps"]:
        return None
    swaps_key = json.dumps([(s["out_id"], s["in_id"]) for s in card["swaps"]], sort_keys=True)
    # 'ignored' dedupes too — dismissing a swap set silences that exact set;
    # 'failed' does not, so the degradation ladder re-proposes after a failure.
    open_rec = conn.execute(
        "SELECT rec_id, payload_json FROM recommendations WHERE kind='lineup' AND week=? "
        "AND state IN ('proposed','notified','snoozed','approved','executed','ignored','verified')",
        (week,)
    ).fetchall()
    for r in open_rec:
        payload = json.loads(r["payload_json"])
        if payload.get("swaps_key") == swaps_key:
            return None  # already tracked
    fired = rules.evaluate(conn, card["swaps"], week)
    rationale = brain.rationale_for_swaps(conn, card)
    payload = {
        "swaps": card["swaps"], "swaps_key": swaps_key,
        "delta": card["delta"], "injury_flag": card["injury_flag"],
        "lineup_hash": card["lineup_hash"], "rules_fired": fired,
    }
    cur = conn.execute(
        "INSERT INTO recommendations(kind,week,payload_json,rationale,state,created_at) "
        "VALUES('lineup',?,?,?,'proposed',?)",
        (week, json.dumps(payload), rationale, db.utcnow()),
    )
    conn.commit()
    rec_id = cur.lastrowid
    db.log_action(conn, rec_id, "state:proposed", after=payload)
    _notify(conn, rec_id, card, fired)
    return rec_id


def _notify(conn: sqlite3.Connection, rec_id: int, card: dict, fired: list[str]) -> None:
    swaps = card["swaps"]
    headline = ", ".join(f"{s['in']['name']} in for {s['out']['name']}" for s in swaps[:2])
    channel = push.CHANNEL_EMERGENCY if card["injury_flag"] else push.CHANNEL_NORMAL
    body = f"{headline} ({card['delta']:+.1f} pts)."
    if fired:
        body += f" Held by rule: {', '.join(fired)}."
    push.send(conn, "Lineup call from the back room", body, channel,
              data={"rec_id": rec_id, "deep_link": "bootlegger://week"})
    transition(conn, rec_id, "notified")


def approve(conn: sqlite3.Connection, rec_id: int) -> int:
    """Approve a rec and enqueue the hands job. The job carries the lineup hash
    read *now* so pre-verify can prove the world hasn't shifted since."""
    row = _get(conn, rec_id)
    payload = json.loads(row["payload_json"])
    fired = rules.evaluate(conn, payload["swaps"], row["week"])
    transition(conn, rec_id, "approved")
    roster = brain.my_roster_row(conn)
    current = json.loads(roster["starters_json"]) if roster else []
    expires = (datetime.now(timezone.utc) + timedelta(hours=JOB_TTL_H)).isoformat(timespec="seconds")
    job_payload = {
        "rec_id": rec_id,
        "week": row["week"],
        "swaps": [{"out_id": s["out_id"], "in_id": s["in_id"], "slot": s["slot"]}
                  for s in payload["swaps"]],
        "lineup_hash_expected": brain.lineup_hash(current),
        "expires_at": expires,
    }
    cur = conn.execute(
        "INSERT INTO jobs(rec_id,payload_json,state,created_at,expires_at) "
        "VALUES(?,?, 'queued', ?, ?)",
        (rec_id, json.dumps(job_payload), db.utcnow(), expires),
    )
    conn.commit()
    db.log_action(conn, rec_id, "job:queued", after={"job_id": cur.lastrowid,
                                                     "rules_fired_at_approve": fired})
    return cur.lastrowid


def snooze(conn: sqlite3.Connection, rec_id: int, minutes: int = 30) -> None:
    transition(conn, rec_id, "snoozed")
    until = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat(timespec="seconds")
    db.log_action(conn, rec_id, "snoozed", after={"until": until})


def ignore(conn: sqlite3.Connection, rec_id: int) -> None:
    transition(conn, rec_id, "ignored")


def list_recs(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM recommendations ORDER BY rec_id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) | {"payload": json.loads(r["payload_json"])} for r in rows]
