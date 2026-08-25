"""Live UI mock: a paced simulated draft written into the LIVE DB so the real
board, dossier, and overlay track it exactly like draft night. My seat takes
The Call (the production path); eleven opponents draft ADP-with-noise.

Run with bootlegger-ingest STOPPED (it refreshes the league draft row, which
would race this draft for the board's newest-draft binding). Clean up after:
    python tools/live_mock.py cleanup
"""
from __future__ import annotations

import json
import random
import sqlite3
import sys
import time

sys.path.insert(0, "/src")
from app import brain, db  # noqa: E402
from tools.h2h_mock import TEAMS, ROUNDS, load_world, opponent_pick, snake_slot  # noqa: E402

DRAFT_ID = "uimock-1"
MY_SLOT = 5
PICK_SECONDS = 2.5


def cleanup(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM draft_picks WHERE draft_id=?", (DRAFT_ID,))
    conn.execute("DELETE FROM drafts WHERE draft_id=?", (DRAFT_ID,))
    conn.commit()
    print("uimock rows removed; board will rebind to the league draft")


def main() -> None:
    conn = db.connect()
    conn.execute("PRAGMA busy_timeout=5000")
    if len(sys.argv) > 1 and sys.argv[1] == "cleanup":
        cleanup(conn)
        return

    world = load_world(conn)
    players, cons, projs, adp, ecr = world
    rng = random.Random(42)

    print("RUNBOOK: stop bootlegger-ingest before running; afterwards run "
          "'python tools/live_mock.py cleanup' and 'docker start "
          "bootlegger-ingest'. On any crash this script cleans up after "
          "itself so the board rebinds to the league draft.", flush=True)
    cleanup(conn)
    conn.execute(
        "INSERT INTO drafts(draft_id,status,settings_json,updated_at) VALUES(?,?,?,?)",
        (DRAFT_ID, "drafting",
         json.dumps({"teams": TEAMS, "rounds": ROUNDS, "slot": MY_SLOT}), db.utcnow()))
    conn.commit()

    taken: set[str] = set()
    rosters = {s: [] for s in range(1, TEAMS + 1)}
    next_pick_at = time.time() + PICK_SECONDS
    pick_no = 1
    try:
        _run_draft(conn, world, rng, taken, rosters, next_pick_at, pick_no)
    except BaseException:
        # A dead sim must not leave the board bound to a phantom draft.
        cleanup(conn)
        raise


def _run_draft(conn, world, rng, taken, rosters, next_pick_at, pick_no) -> None:
    players, cons, projs, adp, ecr = world
    while pick_no <= TEAMS * ROUNDS:
        # heartbeat every second, like the real poller — the staleness banner
        # must stay dark for the whole show
        conn.execute("UPDATE drafts SET updated_at=? WHERE draft_id=?",
                     (db.utcnow(), DRAFT_ID))
        conn.commit()
        if time.time() >= next_pick_at:
            slot = snake_slot(pick_no)
            rnd = (pick_no - 1) // TEAMS + 1
            if slot == MY_SLOT:
                board = brain.get_board(conn)
                sugg = [s for s in board["suggestions"] if s["id"] not in taken]
                pid = sugg[0]["id"] if sugg else opponent_pick(
                    rng, players, adp, taken, rosters[slot], rnd)
                print(f"R{rnd} P{pick_no} MY PICK: {players[pid]['name']} "
                      f"({players[pid]['pos']}) — {sugg[0]['reason'][:60] if sugg else ''}",
                      flush=True)
            else:
                pid = opponent_pick(rng, players, adp, taken, rosters[slot], rnd)
            taken.add(pid)
            rosters[slot].append(pid)
            conn.execute(
                "INSERT INTO draft_picks(draft_id,pick_no,round,draft_slot,roster_id,player_id,ts) "
                "VALUES(?,?,?,?,?,?,?)",
                (DRAFT_ID, pick_no, rnd, slot, slot, pid, db.utcnow()))
            conn.commit()
            pick_no += 1
            next_pick_at = time.time() + PICK_SECONDS
        time.sleep(0.5)

    conn.execute("UPDATE drafts SET status='complete', updated_at=? WHERE draft_id=?",
                 (db.utcnow(), DRAFT_ID))
    conn.commit()
    print("uimock draft complete", flush=True)


if __name__ == "__main__":
    main()
