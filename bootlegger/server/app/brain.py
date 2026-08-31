"""Read-side assembly: the draft board and the weekly lineup card, built as
pure queries over the DB so the API, the recs scanner, and hands all see the
same world."""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# `alerts` imports brain right back; both bind the MODULE (not its members)
# and only touch each other at call time, so the cycle resolves whichever
# module Python reaches first.
from . import alerts, db, schedule
from .config import (DEMO_ROSTER_POSITIONS, MATERIALITY_PTS,
                     RUN_WINDOW_PICKS, settings)
from .demo import DEMO_DRAFT_ID, slot_for_pick
from .engines import advisories
from .engines import draft as draft_engine
from .engines import environment as env_engine
from .engines import grades as grades_engine
from .engines import matchup as matchup_engine
from .engines import room as room_engine
from .engines import trades as trades_engine
from .engines import waivers as waivers_engine
from .engines.draft import Candidate
from .engines.lineup import INactive, PlayerProj, diff_lineup, optimize, ros_status

SUGGESTION_COUNT = 5
# Roster flags that mean "he is not taking a snap", as Sleeper writes them.
# Projections absorb these eventually — a man on IR is already priced at forty
# points — but the flag moves in minutes and the projection sources rebuild
# overnight, which is the entire gap the 2026 draft fell through. DOUBTFUL and
# QUESTIONABLE are deliberately absent: those men play most weeks, and a board
# that refuses to name anybody carrying a knock names nobody in November.
BENCHED_BY_FLAG = {"DNR", "Sus", "IR", "PUP", "NA", "Out"}

_DRAFT_ID_RE = re.compile(r"(\d{15,20})")


def parse_draft_id(text: str) -> str | None:
    """Pull a Sleeper draft id out of a pasted room URL — or accept a bare id.
    Sleeper ids are snowflake-style, always well over 15 digits, so a plain
    'first long number wins' scan is unambiguous."""
    m = _DRAFT_ID_RE.search(text or "")
    return m.group(1) if m else None


def draft_is_complete(status: str | None, n_picks: int, total: int) -> bool:
    """THE one home for 'is the draft in the books': the label says so, or
    every pick is in (some rooms — the demo seed included — never flip the
    label). The board and the report card must never disagree on this."""
    return status == "complete" or (total > 0 and n_picks >= total)


def live_league_draft(conn: sqlite3.Connection) -> bool:
    """True while a NON-scrimmage draft is live. The scrimmage bind must
    refuse while this holds — retargeting the poller mid-real-draft would
    blind the board on the one night it matters, under a green wire lamp."""
    practice_id = db.meta_get(conn, "practice_draft_id") or ""
    row = conn.execute(
        "SELECT 1 FROM drafts WHERE status='drafting' AND draft_id != ?",
        (practice_id,)).fetchone()
    return row is not None


def set_practice(conn: sqlite3.Connection, did: str) -> None:
    """Bind the scrimmage: sweep any previous room's rows (they'd contest
    the newest-draft rule), then write the meta key the poller watches."""
    old = db.meta_get(conn, "practice_draft_id")
    if old and old != did:
        conn.execute("DELETE FROM draft_picks WHERE draft_id=?", (old,))
        conn.execute("DELETE FROM drafts WHERE draft_id=?", (old,))
    db.meta_set(conn, "practice_draft_id", did)


def get_queue(conn: sqlite3.Connection) -> dict:
    """The Slip: the user's ordered pick list. Resolved to display rows with
    a picked flag (picked players stay visible, struck through — the slip is
    a plan, and the room should see how the plan met reality)."""
    ids = json.loads(db.meta_get(conn, "draft_queue") or "[]")
    players = _players_index(conn)
    cons = {r["player_id"]: r for r in conn.execute("SELECT * FROM consensus WHERE week=0")}
    drow = conn.execute("SELECT * FROM drafts ORDER BY updated_at DESC LIMIT 1").fetchone()
    picked = {r["player_id"] for r in conn.execute(
        "SELECT player_id FROM draft_picks WHERE draft_id=?",
        (drow["draft_id"],))} if drow else set()
    rows = [{"id": pid, "name": players[pid]["name"], "pos": players[pid]["pos"],
             "team": players[pid]["team"],
             "pts": round((cons[pid]["pts_robust"] or 0.0) if pid in cons else 0.0, 1),
             "picked": pid in picked}
            for pid in ids if pid in players]
    return {"queue": rows,
            "pilot_armed": db.meta_get(conn, "pilot_armed") == "1",
            "pilot_dry_run": settings.hands_dry_run,
            # Session key lives with the other secrets in the data volume
            # (the .ds_cookie pattern); /run/secrets kept for a compose future.
            "pilot_ready": Path("/data/.sleeper_storage_state").exists()
            or Path("/run/secrets/sleeper_storage_state").exists()}


def set_queue(conn: sqlite3.Connection, ids: list[str]) -> int:
    """Replace the slip wholesale (the UI always sends the full order).
    Unknown ids are dropped, order preserved, dupes collapsed."""
    players = _players_index(conn)
    seen: set[str] = set()
    clean = [pid for pid in ids
             if pid in players and not (pid in seen or seen.add(pid))]
    db.meta_set(conn, "draft_queue", json.dumps(clean))
    return len(clean)


def resolve_pilot_pick(queue_ids: list[str], picked: set[str],
                       suggestions: list[dict]) -> tuple[str, str] | None:
    """The pilot's one decision, kept pure and testable: first slip player
    still on the board, else The Call's top suggestion. Returns
    (player_id, source) or None when there is nothing to take."""
    for pid in queue_ids:
        if pid not in picked:
            return pid, "slip"
    for s in suggestions:
        if s["id"] not in picked:
            return s["id"], "call"
    return None


def clear_practice(conn: sqlite3.Connection) -> str | None:
    """Back to the real thing: drop the scrimmage rows and the meta key so
    the board rebinds to the league draft on the next poll."""
    did = db.meta_get(conn, "practice_draft_id")
    if did:
        conn.execute("DELETE FROM draft_picks WHERE draft_id=?", (did,))
        conn.execute("DELETE FROM drafts WHERE draft_id=?", (did,))
        conn.execute("DELETE FROM meta WHERE key='practice_draft_id'")
        conn.commit()
    return did


def lineup_hash(starters: list[str]) -> str:
    """Order-sensitive hash of the starters list — slot order is the lineup."""
    return hashlib.sha256(json.dumps(starters).encode()).hexdigest()[:16]


def roster_positions(conn: sqlite3.Connection) -> list[str]:
    row = conn.execute("SELECT settings_json FROM league LIMIT 1").fetchone()
    if row and row["settings_json"]:
        rp = json.loads(row["settings_json"]).get("roster_positions")
        if rp:
            return rp
    return DEMO_ROSTER_POSITIONS


def _players_index(conn) -> dict[str, sqlite3.Row]:
    return {r["sleeper_id"]: r for r in conn.execute("SELECT * FROM players")}



def room_tendencies(conn: sqlite3.Connection) -> dict[str, room_engine.Tendency]:
    """How this room drafts against the market, from its own past drafts.

    Empty until the league has enough completed history of the same shape —
    and empty is the correct answer then, not a guess. See engines/room.py.
    """
    live = conn.execute(
        "SELECT draft_id, settings_json FROM drafts ORDER BY updated_at DESC").fetchall()
    if not live:
        return {}
    try:
        current = json.loads(live[0]["settings_json"] or "{}")
    except (ValueError, TypeError):
        current = {}
    teams_now = int(current.get("teams") or settings.teams)

    players = _players_index(conn)
    curves = []
    for row in live:
        try:
            st = json.loads(row["settings_json"] or "{}")
        except (ValueError, TypeError):
            continue
        if not st.get("historical"):
            continue
        # A room of a different size is a different room; its curve says
        # nothing about how twelve seats behave.
        if st.get("teams") and int(st["teams"]) != teams_now:
            continue
        # Prefer the position recorded AT THE PICK: a 2023 draft is full of
        # men the current players table no longer carries, and losing them
        # would bend the early-round curve that actually matters.
        picks = [{"pick_no": r["pick_no"],
                  "pos": r["pos"] or (players[r["player_id"]]["pos"]
                                      if r["player_id"] in players else None)}
                 for r in conn.execute(
                     "SELECT pick_no, player_id, pos FROM draft_picks WHERE draft_id=?",
                     (row["draft_id"],))]
        picks = [x for x in picks if x["pos"]]
        if picks:
            curves.append(room_engine.room_curve(picks))
    if len(curves) < room_engine.MIN_DRAFTS:
        return {}

    # ONE row per player. The adp table holds a row per (player, source), and
    # feeding both a player's Sleeper and FFC rows into the market curve counts
    # him twice — which makes the k-th man at every position look like he goes
    # earlier than he does, and read out as "every position slides here".
    # Sleeper's platform ADP wins because that is where this room drafts.
    best: dict[str, tuple[int, float]] = {}
    for r in conn.execute(
            "SELECT player_id, source, adp FROM adp "
            "WHERE source IN ('sleeper','ffc','demo')"):
        if r["player_id"] not in players:
            continue
        rank = {"sleeper": 0, "demo": 1, "ffc": 2}.get(r["source"], 9)
        cur = best.get(r["player_id"])
        if cur is None or rank < cur[0]:
            best[r["player_id"]] = (rank, r["adp"])
    adp_rows = [{"pos": players[pid]["pos"], "adp": v[1]} for pid, v in best.items()]
    if not adp_rows:
        return {}
    return room_engine.tendencies(curves, room_engine.market_curve(adp_rows))


def draft_order_rows(conn: sqlite3.Connection, dsettings: dict, teams: int,
                     on_clock_slot: int | None, my_slot: int) -> list[dict]:
    """The twelve seats in pick order, named.

    Sleeper publishes slot_to_roster_id once the order is set; before that this
    returns nothing rather than inventing an order, because a seating plan the
    room has not agreed on is worse than no seating plan. Owners come from the
    rosters table, so a seat whose roster has not synced yet reads as its slot
    number instead of a wrong name.
    """
    mapping = dsettings.get("slot_to_roster_id") or {}
    if not mapping:
        return []
    owners = {r["roster_id"]: (r["owner"] or f"roster {r['roster_id']}")
              for r in conn.execute("SELECT roster_id, owner FROM rosters")}
    out = []
    for slot in range(1, teams + 1):
        rid = mapping.get(str(slot), mapping.get(slot))
        try:
            rid = int(rid) if rid is not None else None
        except (TypeError, ValueError):
            rid = None
        out.append({
            "slot": slot,
            "roster_id": rid,
            "owner": owners.get(rid) or f"slot {slot}",
            "mine": slot == my_slot,
            "on_clock": on_clock_slot is not None and slot == on_clock_slot,
        })
    return out


def get_board(conn: sqlite3.Connection) -> dict[str, Any]:
    """Everything the Draft Board surface needs, in one payload."""
    # Newest draft wins: a league reset creates a second draft row, and an
    # unordered LIMIT 1 could bind the board to the dead one on draft night.
    drow = conn.execute("SELECT * FROM drafts WHERE draft_id=?", (DEMO_DRAFT_ID,)).fetchone() \
        if settings.mode == "demo" else conn.execute(
            "SELECT * FROM drafts ORDER BY updated_at DESC LIMIT 1").fetchone()
    dsettings = json.loads(drow["settings_json"]) if drow and drow["settings_json"] else {}
    teams = int(dsettings.get("teams", settings.teams))
    rounds = int(dsettings.get("rounds", settings.rounds))
    my_slot = int(dsettings.get("slot", settings.my_roster_id))
    draft_id = drow["draft_id"] if drow else None
    status = drow["status"] if drow else "pre_draft"
    # Scrimmage flag: the UI banners hard when the board is tracking a pasted
    # practice room instead of the league draft — a rehearsal must never be
    # mistakable for the real thing.
    practice = bool(draft_id) and draft_id == db.meta_get(conn, "practice_draft_id")

    picks = conn.execute(
        "SELECT * FROM draft_picks WHERE draft_id=? ORDER BY pick_no", (draft_id,)
    ).fetchall() if draft_id else []
    picked = {p["player_id"]: p for p in picks}
    current_pick = len(picks) + 1
    total_picks = teams * rounds
    if draft_is_complete(status, len(picks), total_picks):
        status = "complete"
        on_clock_slot, my_next = None, None
    else:
        on_clock_slot = slot_for_pick(current_pick)
        my_next = draft_engine.next_pick_after(current_pick - 1, my_slot, teams, rounds)
    # The wait-decision horizon. The Call advises the pick at my_next, so "what
    # if I pass?" means the snake's NEXT return (my_after) — scoring against
    # my_next itself lets nearly everyone "survive" one pick and collapses the
    # cliff math into now-vs-second-best-now, which overrates steep positions
    # (the Josh-Allen-at-#2 bug). Row meters still show survival to my_next.
    my_after = (draft_engine.next_pick_after(my_next, my_slot, teams, rounds)
                if my_next else None)

    players = _players_index(conn)
    cons = {r["player_id"]: r for r in conn.execute("SELECT * FROM consensus WHERE week=0")}
    # ADP composition across sources: the MEAN should track how this room
    # drafts (they draft on Sleeper, so platform ADP first; FFC mocks next;
    # FantasyPros expert consensus last — experts aren't the room). The SIGMA
    # takes the widest stdev any source reports: disagreement is information.
    _adp_pref = {"sleeper": 0, "demo": 1, "ffc": 2, "fp_ecr": 3}
    _adp_rows: dict[str, list] = {}
    ecr_rank: dict[str, float] = {}
    for r in conn.execute("SELECT * FROM adp"):
        _adp_rows.setdefault(r["player_id"], []).append(r)
        if r["source"] == "fp_ecr":
            ecr_rank[r["player_id"]] = r["adp"]
    adp: dict[str, dict] = {}
    for pid, rows in _adp_rows.items():
        rows.sort(key=lambda r: _adp_pref.get(r["source"], 9))
        stds = [r["stdev"] for r in rows if r["stdev"]]
        adp[pid] = {"adp": rows[0]["adp"], "stdev": max(stds) if stds else None}

    # Expert-blended value. Projections-VBD and the 106-expert consensus rank
    # are two independent value signals; ranks carry what projections can't
    # (injury risk, floor). Map each ECR rank through OUR overall value curve
    # so expert opinion enters in VBD units, then average. Displayed vbd stays
    # the raw projections stat; only the decision value blends.
    vbd_curve = sorted((r["vbd"] or 0.0 for r in cons.values()), reverse=True)

    def _curve_value(rank: float) -> float:
        if not vbd_curve:
            return 0.0
        i = max(0.0, min(rank - 1.0, len(vbd_curve) - 1.0))
        lo = int(i)
        hi = min(lo + 1, len(vbd_curve) - 1)
        return vbd_curve[lo] + (vbd_curve[hi] - vbd_curve[lo]) * (i - lo)

    def blended_value(pid: str, vbd: float) -> float:
        r = ecr_rank.get(pid)
        return 0.5 * vbd + 0.5 * _curve_value(r) if r else vbd

    my_picks = [p for p in picks if p["draft_slot"] == my_slot]
    my_counts: dict[str, int] = {}
    for p in my_picks:
        pos = players[p["player_id"]]["pos"]
        my_counts[pos] = my_counts.get(pos, 0) + 1
    rp = roster_positions(conn)

    # Candidate pools per position (available players with consensus).
    # This room's own habits, read from its past drafts. Empty (and therefore
    # a no-op) until the league has enough completed history of the same shape.
    tend = room_tendencies(conn)

    pools: dict[str, list[Candidate]] = {}
    board_rows: list[dict] = []
    for pid, c in cons.items():
        if pid not in players:
            continue
        p = players[pid]
        a = adp.get(pid)
        row: dict[str, Any] = {
            "id": pid, "name": p["name"], "pos": p["pos"], "team": p["team"],
            "bye": p["bye"], "tier": c["tier"], "vbd": round(c["vbd"] or 0, 1),
            "pts": round(c["pts_robust"] or 0, 1),
            "adp": a["adp"] if a else None,
            "injury": p["injury_status"],
        }
        pk = picked.get(pid)
        if pk:
            row.update(picked_by=pk["draft_slot"], pick_no=pk["pick_no"],
                       mine=pk["draft_slot"] == my_slot)
        elif my_next and a:
            # National ADP is the average of drafts full of strangers. This
            # room has habits, and they are measurable from its own past
            # drafts — see engines/room.py. Empty tendencies leave both values
            # exactly as the market had them.
            eff_adp = room_engine.adjust_adp(a["adp"], p["pos"], tend)
            eff_sigma = room_engine.widen_sigma(
                draft_engine.adp_sigma(a["adp"], a["stdev"]), p["pos"], tend)
            surv = draft_engine.survival_prob(eff_adp, eff_sigma, my_next)
            row["survival"] = round(surv, 3)
            wait_surv = (draft_engine.survival_prob(eff_adp, eff_sigma, my_after)
                         if my_after else 0.0)
            pools.setdefault(p["pos"], []).append(
                Candidate(pid, p["pos"], blended_value(pid, c["vbd"] or 0.0), wait_surv))
        board_rows.append(row)

    # Suggestion scores for available players.
    scores: dict[str, float] = {}
    reasons: dict[str, str] = {}
    for pos, pool in pools.items():
        mult = draft_engine.roster_need_multiplier(pos, my_counts, rp)
        # A filled K/DEF slot makes further K/DEF picks dead weight — leave
        # them unscored so they sort behind even negative-VBD skill depth
        # (a tiny positive DST edge beat lottery-ticket RBs at the last pick).
        if pos in ("K", "DEF") and mult <= 0.05:
            continue
        for cand in pool:
            s = draft_engine.suggestion_score(cand, pool, mult)
            scores[cand.player_id] = s
            e_next = cand.vbd - (s / mult if mult else 0)
            reasons[cand.player_id] = _reason(cand, e_next, mult)

    # Endgame starvation guard. Regret math says "wait" forever on positions
    # that survive (QB/K/DEF always survive) — but with S open starter slots
    # and R of my picks left, waiting stops being an option. Ramp open-slot
    # positions up as slack (R − S) shrinks. The h2h harness caught a draft
    # finishing with no QB and no K without this.
    if my_next and status != "complete":
        remaining = len([p for p in draft_engine.snake_pick_numbers(my_slot, teams, rounds)
                         if p >= my_next])
        dedicated = {p: rp.count(p) for p in ("QB", "RB", "WR", "TE", "K", "DEF")}
        flex_total = sum(1 for s_ in rp if s_ in ("FLEX", "SUPER_FLEX", "SUPERFLEX",
                                                  "WRRB_FLEX", "REC_FLEX"))
        flex_used = sum(max(0, my_counts.get(p, 0) - dedicated.get(p, 0))
                        for p in ("RB", "WR", "TE"))
        open_dedicated = {p for p, w in dedicated.items() if w and my_counts.get(p, 0) < w}
        open_slots = (sum(w - min(my_counts.get(p, 0), w) for p, w in dedicated.items())
                      + max(0, flex_total - flex_used))
        slack = remaining - open_slots
        if slack <= 3 and open_dedicated:
            # A nudge loses to late-round scarcity scores; the endgame rule
            # must dominate. Slack 3: strong lean toward the starving slots.
            # Slack ≤2: luxury picks are off the menu — fill the slots only
            # one position can fill (flex refills itself once they're closed).
            for pos, pool in pools.items():
                if pos in open_dedicated:
                    urgency = 0.5 if slack >= 3 else 1.0
                    for cand in pool:
                        scores[cand.player_id] = scores.get(cand.player_id, 0.0) \
                            + urgency * max(cand.vbd, 0.0)
                        reasons[cand.player_id] = (
                            f"The shelf still needs a {pos} and the draft is closing — "
                            f"{remaining} of your picks left.")
                elif slack <= 2:
                    for cand in pool:
                        if cand.player_id in scores:
                            scores[cand.player_id] *= 0.05
    for row in board_rows:
        if row["id"] in scores:
            row["score"] = round(scores[row["id"]], 1)

    available = [r for r in board_rows if "pick_no" not in r]
    available.sort(key=lambda r: -(r.get("score") if r.get("score") is not None else -999))

    # THE WIRE, ON THE BOARD.
    #
    # This join is the whole lesson of the 2026 draft. The news was ingested,
    # classified, and stored — twenty-five items on one first-round back, nine
    # of them saying he had been put on the Commissioner's Exempt List — and
    # get_board never read the table. The engine went on recommending him at
    # three times the runner-up score for two straight turns, off a projection
    # built when he was expected to play sixteen games.
    #
    # Projections carry injury news eventually; a flag carries it in minutes.
    # The board must not wait for the slower channel.
    news = alerts.for_players(conn, [r["id"] for r in available])
    for row in available:
        item = news.get(row["id"])
        if item:
            row["news"] = {"severity": item["severity"], "headline": item["headline"],
                           "published_at": item["published_at"]}

    # A man the feed says is not playing is not a suggestion, whatever his
    # projection still says. This SUPPRESSES rather than reprices: the survival
    # model that gave a man who lasted to pick 74 a 1.3e-13 chance of lasting
    # has not earned a news multiplier, and a wrong number moved by a second
    # wrong number is not an improvement. He keeps his score and his place on
    # the board; he just stops being told to you.
    blocked = {
        r["id"] for r in available
        if (r.get("injury") or "") in BENCHED_BY_FLAG
        or (r.get("news") or {}).get("severity") == "out"
    }
    # ...unless suppressing leaves nothing to say. In the last two rounds the
    # pool is thin enough that every man left can be flagged, and a shortlist
    # of zero is worse advice than a flagged man with his flag showing.
    shortlist = [r for r in available if r["id"] not in blocked] or available
    suggestions = [
        {**r, "reason": reasons.get(r["id"], "")} for r in shortlist[:SUGGESTION_COUNT]
    ]

    # The experts' best available — shown on The Call when the room disagrees.
    # Blocked men are out here too: a second voice repeating the first voice's
    # blind spot is worse than no second voice.
    experts_call = None
    ranked_avail = [(ecr_rank[r["id"]], r) for r in available
                    if r["id"] in ecr_rank and r["id"] not in blocked]
    if ranked_avail:
        er, erow = min(ranked_avail, key=lambda t: t[0])
        experts_call = {"id": erow["id"], "name": erow["name"],
                        "pos": erow["pos"], "ecr": round(er, 1)}

    recent = [
        {
            "pick_no": p["pick_no"], "round": p["round"], "slot": p["draft_slot"],
            "player": players[p["player_id"]]["name"],
            "pos": players[p["player_id"]]["pos"],
            "team": players[p["player_id"]]["team"],
            "mine": p["draft_slot"] == my_slot,
        }
        for p in picks[-12:]
    ][::-1]

    my_roster = [
        {"pick_no": p["pick_no"], "round": p["round"],
         "player": players[p["player_id"]]["name"],
         "pos": players[p["player_id"]]["pos"],
         "team": players[p["player_id"]]["team"],
         # the shelf panel cannot flag a bye it was never handed
         "bye": players[p["player_id"]]["bye"]}
        for p in my_picks
    ]

    # ------------------------------------------------------ the advisory layer
    # Everything below is READ-ONLY: it is computed after the scores are final
    # and never feeds back into them. A bye penalty inside the pick score would
    # trade real season points for cosmetic tidiness (one bad week is
    # streamable), and position pressure is not calibrated enough to touch
    # survival. See engines/advisories.py.
    slots = advisories.starting_slots(rp)
    shelf = advisories.shelf_findings(
        [{"pos": r["pos"], "name": r["player"], "team": r["team"], "bye": r["bye"]}
         for r in my_roster], slots)

    # What the room took at each position over the last window, against what
    # ADP said that pick range would take. The residual — NOT a raw count of
    # positions, which mostly measures the shape of the ADP curve and would
    # double-count the very thing survival is already built from.
    pressure: list[dict] = []
    if len(picks) >= RUN_WINDOW_PICKS and status != "complete":
        win = picks[-RUN_WINDOW_PICKS:]
        lo, hi = win[0]["pick_no"], win[-1]["pick_no"]
        observed: dict[str, int] = {}
        for p in win:
            if p["player_id"] in players:
                pos = players[p["player_id"]]["pos"]
                observed[pos] = observed.get(pos, 0) + 1
        expected: dict[str, float] = {}
        for pid, a in adp.items():
            if pid in players and lo <= a["adp"] <= hi:
                pos = players[pid]["pos"]
                expected[pos] = expected.get(pos, 0.0) + 1.0
        pressure = advisories.position_pressure(observed, expected)

    srow = conn.execute("SELECT scoring_json FROM league LIMIT 1").fetchone()
    scoring = json.loads(srow["scoring_json"]) if srow and srow["scoring_json"] else {}
    priors = advisories.league_priors(scoring, rp, teams)

    return {
        "draft": {
            "id": draft_id, "status": status, "practice": practice,
            "teams": teams, "rounds": rounds,
            "current_pick": min(current_pick, total_picks),
            "total_picks": total_picks,
            "round": min((current_pick - 1) // teams + 1, rounds),
            "on_clock_slot": on_clock_slot,
            "on_the_clock_me": on_clock_slot == my_slot,
            "my_slot": my_slot, "my_next_pick": my_next,
            # THE ROOM'S SEATING PLAN. "slot 10 on the clock" tells you
            # nothing on draft night; a name tells you whether the man ahead
            # of you takes running backs. Every seat, in pick order, with the
            # one on the clock marked and yours flagged.
            "order": draft_order_rows(conn, dsettings, teams, on_clock_slot, my_slot),
            # Every pick number this seat owns, so the wait between turns is a
            # fact on the screen rather than arithmetic done under a clock.
            "my_picks": draft_engine.snake_pick_numbers(my_slot, teams, rounds),
            "starts_at": dsettings.get("start_time"),
            # heartbeat: the poller refreshes this every cycle; the frontend
            # banners when it goes stale during a live draft
            "synced_at": drow["updated_at"] if drow else None,
            # When the SHEET was last pulled, as opposed to when the picks
            # were. They were eleven hours apart on draft night and nothing
            # on screen said so: the pick feed's heartbeat was two seconds
            # old and looked like freshness for the whole board.
            "sheet_as_of": conn.execute(
                "SELECT MAX(t) FROM (SELECT MAX(updated_at) t FROM players "
                "UNION ALL SELECT MAX(updated_at) FROM adp)").fetchone()[0],
        },
        "players": board_rows,
        "suggestions": suggestions,
        "recent_picks": recent,
        "my_roster": my_roster,
        "roster_positions": rp,
        "experts_call": experts_call,
        "shelf": {"findings": [f.as_dict() for f in shelf["findings"]],
                  "byes_known": shelf["byes_known"]},
        "pressure": pressure,
        "priors": priors,
        # What this room does that the market doesn't. Empty when the league
        # has no comparable history — the survival numbers are then plain
        # market ADP, and the board says so rather than implying calibration
        # it doesn't have.
        "room": {"tendencies": [t.as_dict() for t in
                                sorted(tend.values(), key=lambda t: -abs(t.offset))],
                 "read": room_engine.read_out(tend)},
    }


def _reason(cand: Candidate, e_next: float, mult: float) -> str:
    gap = cand.vbd - e_next
    # cand.survival is the WAIT horizon (my pick after this one) — the row's
    # meter shows the nearer "reaches this pick" number; word them apart.
    surv = f"{cand.survival:.0%} odds he lasts until your next turn"
    if gap > 15:
        return f"Cliff at {cand.pos} — expect {gap:.0f} VBD gone if you wait. {surv}."
    if cand.survival < 0.35:
        return f"Won't come back around ({surv})."
    if mult >= 1.0:
        return f"Fills an open starter slot; {surv}."
    return f"Best value on the board; {surv}."


def suggest_my_pick(conn: sqlite3.Connection) -> str | None:
    board = get_board(conn)
    if board["suggestions"]:
        return board["suggestions"][0]["id"]
    # Fallback: best available by ADP (e.g. no consensus row).
    for r in sorted(board["players"], key=lambda r: r.get("adp") or 9999):
        if "pick_no" not in r:
            return r["id"]
    return None


# ---------------------------------------------------------------------------
# Weekly lineup card
# ---------------------------------------------------------------------------

def week_projections(conn: sqlite3.Connection, week: int) -> dict[str, float]:
    return {r["player_id"]: r["pts_robust"] or 0.0 for r in
            conn.execute("SELECT player_id, pts_robust FROM consensus WHERE week=?", (week,))}


def my_roster_row(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM rosters WHERE roster_id=?",
                        (settings.my_roster_id,)).fetchone()


def week_bands(conn: sqlite3.Connection,
               player_ids: list[str]) -> dict[str, tuple[float | None, float | None]]:
    """Per-game floor/ceiling for a set of players, from Draft Sharks' season
    band divided by their own projected games. One query for the roster rather
    than one per player — the floor/ceiling lineups need every man banded, not
    just the ones in a proposed swap.

    Missing here is fine: engines/lineup falls back to a positional spread.
    """
    if not player_ids:
        return {}
    marks = ",".join("?" * len(player_ids))
    out: dict[str, tuple[float | None, float | None]] = {}
    for r in conn.execute(
            f"SELECT pr.player_id, pr.floor, pr.ceiling, pl.proj_games "
            f"FROM projections pr JOIN players pl ON pl.sleeper_id=pr.player_id "
            f"WHERE pr.week=0 AND pr.source='draftsharks' "
            f"AND pr.player_id IN ({marks})", player_ids):
        games = r["proj_games"] or 17.0
        lo = round(r["floor"] / games, 1) if r["floor"] else None
        hi = round(r["ceiling"] / games, 1) if r["ceiling"] else None
        if lo is not None or hi is not None:
            out[r["player_id"]] = (lo, hi)
    return out


def get_week_card(conn: sqlite3.Connection, week: int = 1) -> dict[str, Any]:
    roster = my_roster_row(conn)
    if not roster:
        return {"week": week, "ready": False,
                "note": "No roster on the books — the wire opens once the league seats you."}
    players = _players_index(conn)
    projs = week_projections(conn, week)
    ids = json.loads(roster["players_json"])
    # Pre-draft the roster row exists but holds nobody. Without this gate the
    # card reports "Lineup optimal — projected 0.0", which is confidently wrong.
    if not ids:
        return {"week": week, "ready": False,
                "note": "This room opens after the draft — no roster to set yet. "
                        "The Board is where the season starts."}
    starters = json.loads(roster["starters_json"])
    rp = roster_positions(conn)
    bands = week_bands(conn, ids)

    # Game context for every roster team, one query: opponent, kickoff, and
    # any weather concern. `locked` = kickoff already passed (Sleeper locks
    # the slot; the card must stop proposing that swap).
    games = {g["team"]: g for g in conn.execute(
        "SELECT * FROM nfl_games WHERE season=? AND week=?",
        (settings.season, week))}
    now_utc = datetime.now(timezone.utc)

    # THE GAME EACH MAN IS STANDING IN. Until now every projection here was
    # matchup-blind: a receiver whose team is implied for 30 and one implied
    # for 16 carried the same number. The market's implied team total is the
    # correction, damped and capped — see engines/environment.py for why it is
    # half-strength and why an unpriced game is an AVERAGE game, never a
    # penalty. The adjustment happens BEFORE the optimizer runs, because a
    # lineup chosen on unadjusted numbers is the thing being fixed.
    slate_mean, mean_note = env_engine.slate_mean(
        [g["implied_total"] for g in games.values()])
    envs = {t: env_engine.for_team(t, g["implied_total"], slate_mean, mean_note)
            for t, g in games.items()}

    def env_of(team: str | None):
        return envs.get(team or "") or env_engine.for_team(
            team, None, slate_mean, mean_note)

    adj_proj = {}
    for pid in ids:
        if pid not in players:
            continue
        raw = projs.get(pid, 0.0)
        adj_proj[pid] = env_engine.apply(raw, env_of(players[pid]["team"]))

    pool = [
        PlayerProj(pid, players[pid]["pos"], adj_proj.get(pid, 0.0),
                   name=players[pid]["name"],
                   injury_status=players[pid]["injury_status"],
                   on_bye=players[pid]["bye"] == week,
                   floor=bands.get(pid, (None, None))[0],
                   ceiling=bands.get(pid, (None, None))[1])
        for pid in ids if pid in players
    ]
    d = diff_lineup(pool, starters, rp)

    def describe(pid: str) -> dict:
        p = players[pid]
        # `raw` is the number the lineup was actually built on — the
        # environment-adjusted one — so a row always reconciles with the
        # totals beside it. The unadjusted figure rides along as proj_base so
        # the card can show its work.
        base = round(projs.get(pid, 0.0), 1)
        raw = round(adj_proj.get(pid, projs.get(pid, 0.0)), 1)
        zeroed = (p["injury_status"] or "") in INactive or p["bye"] == week
        g = games.get(p["team"])
        kickoff = g["kickoff_utc"] if g else None
        locked = bool(kickoff) and datetime.fromisoformat(kickoff) <= now_utc
        # proj is what the player counts for (0 when Out/bye) so rows always
        # reconcile with the totals; proj_full keeps the healthy number.
        return {"id": pid, "name": p["name"], "pos": p["pos"], "team": p["team"],
                "proj": 0.0 if zeroed else raw, "proj_full": raw,
                "injury": p["injury_status"], "bye": p["bye"] == week,
                "opp": (("" if g["is_home"] else "@") + (g["opponent"] or "")) if g else None,
                "kickoff_utc": kickoff, "locked": locked,
                "imp": g["implied_total"] if g else None,
                # The board shows its work: what the projection was before the
                # game it is being played in was priced in, and by how much.
                "proj_base": base,
                "env": env_of(p["team"]).as_dict(),
                "practice": p["practice_status"],
                "wx": schedule.weather_flags(g)}

    # Latest rec whatever its state — the card must be able to show the
    # verified confirmation and the failed notice, not just open work.
    open_rec = conn.execute(
        "SELECT * FROM recommendations WHERE kind='lineup' AND week=? "
        "ORDER BY rec_id DESC LIMIT 1", (week,)
    ).fetchone()

    slot_order = [s for s in rp if s not in ("BN", "IR", "TAXI")]
    actual_rows = [{"slot": slot_order[i] if i < len(slot_order) else "?",
                    **describe(pid)} for i, pid in enumerate(starters) if pid in players]
    optimal_rows = [{"slot": slot, **describe(p.player_id)}
                    for slot, p in (d.optimal.assignment if d.optimal else [])]
    bench_rows = [describe(pid) for pid in ids
                  if pid in players and pid not in set(starters)]

    def floor_pg(pid: str) -> float | None:
        """Draft Sharks floor as a per-game number — the uncertainty signal
        the dossier already displays, finally allowed into a decision."""
        row = conn.execute(
            "SELECT pr.floor, pl.proj_games FROM projections pr "
            "JOIN players pl ON pl.sleeper_id=pr.player_id "
            "WHERE pr.player_id=? AND pr.week=0 AND pr.floor IS NOT NULL "
            "ORDER BY pr.source='draftsharks' DESC LIMIT 1", (pid,)).fetchone()
        if not row or row["floor"] is None:
            return None
        return round(row["floor"] / (row["proj_games"] or 17.0), 1)

    swaps = []
    for s in d.swaps:
        out_d, in_d = describe(s["out_id"]), describe(s["in_id"])
        if out_d["locked"] or in_d["locked"]:
            continue  # Sleeper has locked the slot; proposing it would be a lie
        fo, fi = floor_pg(s["out_id"]), floor_pg(s["in_id"])
        risk = None
        if fo is not None and fi is not None and fi < fo and s["gain"] < 2.0:
            risk = (f"thin edge, thinner floor — {in_d['name']}'s floor is "
                    f"{fi} vs {fo} a game; a coin-flip, not a clear start")
        # The market's read: a thin edge into a much lower implied team total
        # is the projection arguing with Vegas — say so.
        io, ii = out_d["imp"], in_d["imp"]
        if (io is not None and ii is not None and s["gain"] < 2.0
                and io - ii >= 4.0):
            note = (f"the market likes the bench spot — {in_d['name']}'s team "
                    f"is implied for {ii}, {out_d['name']}'s for {io}")
            risk = f"{risk}; {note}" if risk else note
        if in_d["wx"]:
            note = f"{in_d['name']}'s game: {', '.join(in_d['wx'])}"
            risk = f"{risk}; {note}" if risk else note
        # Practice report (in-season, from the official Wed-Fri reports): a
        # swap-in who hasn't practiced is a different bet than his projection.
        if in_d.get("practice") == "DNP":
            note = f"{in_d['name']} has not practiced this week"
            risk = f"{risk}; {note}" if risk else note
        swaps.append({**s, "out": out_d, "in": in_d,
                      "out_floor_pg": fo, "in_floor_pg": fi, "risk": risk})

    # Everything the verdict claims must be earned by the swaps it actually
    # proposes: a locked (dropped) swap's gain may not inflate the delta, the
    # materiality call, or the push copy.
    actionable = round(sum(s["gain"] for s in swaps), 2)
    kept_flag = any(s["out"]["injury"] or s["out"]["bye"] for s in swaps)

    return {
        "week": week, "ready": True,
        "owner": roster["owner"],
        "actual": actual_rows, "optimal": optimal_rows, "bench": bench_rows,
        "actual_total": round(d.actual_total, 1),
        "optimal_total": round(d.optimal.total if d.optimal else 0.0, 1),
        "delta": round(actionable, 1),
        "injury_flag": kept_flag,
        "material": bool(swaps) and (actionable > MATERIALITY_PTS or kept_flag),
        "swaps": swaps,
        "wx_concerns": sorted({f"{r['team']}: {w}" for r in actual_rows
                               for w in (r["wx"] or [])}),
        "lineup_hash": lineup_hash(starters),
        "rec": dict(open_rec) if open_rec else None,
        # Who you're playing, and what that does to the right lineup.
        "matchup": week_matchup(conn, week, pool, rp, d),
        # How much of this week the market has actually priced. A card that
        # silently applies no adjustment looks identical to one where every
        # game happens to be average, and this house does not allow the two to
        # be confused. Most of a season sits at 0 priced, and says so.
        "slate": {
            "priced": sum(1 for g in games.values() if g["implied_total"] is not None),
            "teams": len(games),
            "mean": round(slate_mean, 1),
            "note": mean_note,
        },
        # The wire's freshest word on anyone in this room.
        "news": alerts.for_players(
            conn, [r["id"] for r in actual_rows + bench_rows]),
    }


# The alternative lineup has to buy enough win probability to be worth the
# words. Below this it is noise dressed as strategy.
MIN_STRATEGY_SWING = 0.02


def _matchup_sigma(conn: sqlite3.Connection) -> tuple[float, str]:
    """This league's own scoring spread, measured from every roster-week where
    both a projection and a realized score exist AND the week is actually over.

    The completeness gate is the load-bearing part. Sleeper reports points
    continuously — 0.0 before kickoff, partial totals during — and the ETL
    persists them every nightly, so the current week was landing in here as a
    realized score. Twelve in-progress rows of (0 - 110) mixed into real
    N(0,27) residuals measure sigma 39.4 instead of 27, and sigma is exactly
    what decides whether the week gets the floor lineup or the ceiling one.
    """
    done = schedule.completed_weeks(conn, settings.season)
    if not done:
        return matchup_engine.sigma_from_history([])
    marks = ",".join("?" * len(done))
    residuals = [r["points_for"] - r["proj_for"] for r in conn.execute(
        f"SELECT points_for, proj_for FROM matchups "
        f"WHERE points_for IS NOT NULL AND proj_for IS NOT NULL AND proj_for > 0 "
        f"AND week IN ({marks})", tuple(sorted(done)))]
    return matchup_engine.sigma_from_history(residuals)


def week_matchup(conn: sqlite3.Connection, week: int, pool: list[PlayerProj],
                 rp: list[str], d) -> dict | None:
    """The head-to-head: his projected score, the odds, and which of the three
    lineups this week actually wants.

    The optimizer maximises expected points, which is the right objective only
    when the game is close. A heavy favourite should be buying floor and a
    heavy underdog should be buying ceiling — see engines/matchup.py. Returns
    None when the league hasn't published a pairing for the week (pre-season,
    or a bye in the schedule), because inventing an opponent would be worse
    than saying nothing.
    """
    me = my_roster_row(conn)
    if not me:
        return None
    row = conn.execute(
        "SELECT * FROM matchups WHERE week=? AND roster_id=?",
        (week, me["roster_id"])).fetchone()
    if not row or row["opp_roster_id"] is None:
        return None
    opp = conn.execute("SELECT * FROM rosters WHERE roster_id=?",
                       (row["opp_roster_id"],)).fetchone()

    # His projected score: what he has actually set, which is what he will
    # score — not what he could optimally set. Falls back to his best lineup
    # when the league hasn't published starters yet.
    projs = week_projections(conn, week)
    players = _players_index(conn)
    opp_ids = json.loads(opp["players_json"] or "[]") if opp else []
    opp_starters = json.loads(opp["starters_json"] or "[]") if opp else []

    def to_proj(ids: list[str]) -> list[PlayerProj]:
        return [PlayerProj(pid, players[pid]["pos"], projs.get(pid, 0.0),
                           name=players[pid]["name"],
                           injury_status=players[pid]["injury_status"],
                           on_bye=players[pid]["bye"] == week)
                for pid in ids if pid in players]

    if opp_starters:
        opp_proj = round(sum(p.startable_proj for p in to_proj(opp_starters)), 1)
        opp_basis = "his lineup as set"
    else:
        opp_proj = round(optimize(to_proj(opp_ids), rp).total, 1)
        opp_basis = "his best lineup (none set yet)"
    if not opp_proj and row["proj_against"]:
        opp_proj, opp_basis = round(row["proj_against"], 1), "the league's own projection"

    sigma, sigma_note = _matchup_sigma(conn)
    expected = d.optimal if d.optimal else optimize(pool, rp)
    floor_lu = optimize(pool, rp, objective="floor")
    ceil_lu = optimize(pool, rp, objective="ceiling")

    def wp(total: float) -> float:
        return matchup_engine.win_probability(total, opp_proj, sigma)

    # A floor lineup wins by removing downside, not by scoring more, so its
    # win probability has to be judged on the distribution it produces — a
    # tighter spread around a slightly lower mean. Approximate that by
    # crediting half the reduction in the lineup's own spread.
    def wp_shaped(lu) -> float:
        spread = max(1.0, (lu.ceiling_total - lu.floor_total) / 2.0)
        base_spread = max(1.0, (expected.ceiling_total - expected.floor_total) / 2.0)
        adj = sigma * math.sqrt(max(0.05, spread / base_spread))
        return matchup_engine.win_probability(lu.total, opp_proj, adj)

    wp_expected = wp(expected.total)
    plan = matchup_engine.strategy(wp_expected)
    wp_floor, wp_ceiling = wp_shaped(floor_lu), wp_shaped(ceil_lu)
    gain = matchup_engine.swing(wp_floor, wp_expected, wp_ceiling, plan.key)
    alt = {"floor": floor_lu, "ceiling": ceil_lu}.get(plan.key)
    # Only speak when the alternative both differs from the expected-points
    # lineup and moves the odds enough to matter.
    changes = (sorted(alt.starter_ids) != sorted(expected.starter_ids)) if alt else False
    actionable = bool(alt and changes and gain >= MIN_STRATEGY_SWING)

    return {
        "opponent": (opp["owner"] if opp else None) or f"roster {row['opp_roster_id']}",
        "opp_roster_id": row["opp_roster_id"],
        "opp_proj": opp_proj,
        "opp_basis": opp_basis,
        "my_proj": round(expected.total, 1),
        "margin": round(expected.total - opp_proj, 1),
        "win_prob": round(wp_expected, 3),
        "sigma": round(sigma, 1),
        "sigma_note": sigma_note,
        "strategy": plan.as_dict(),
        "actionable": actionable,
        "swing": round(gain, 3),
        "bands": {
            "floor": round(expected.floor_total, 1),
            "expected": round(expected.total, 1),
            "ceiling": round(expected.ceiling_total, 1),
        },
        "alt": ({
            "objective": plan.key,
            "total": round(alt.total, 1),
            "floor": round(alt.floor_total, 1),
            "ceiling": round(alt.ceiling_total, 1),
            "win_prob": round(wp_floor if plan.key == "floor" else wp_ceiling, 3),
            "rows": [{"slot": slot, "id": p.player_id, "name": p.name, "pos": p.pos,
                      "proj": round(p.startable_proj, 1),
                      "floor": round(p.startable_floor, 1),
                      "ceiling": round(p.startable_ceiling, 1),
                      "swap_in": p.player_id not in expected.starter_ids}
                     for slot, p in alt.assignment],
        } if actionable else None),
    }


def rationale_for_swaps(conn: sqlite3.Connection, card: dict) -> str:
    """Deterministic rationale writer. (The Claude-API rationale writer is the
    Phase 3 upgrade; this keeps the field honest until then.)"""
    if not card["swaps"]:
        return "Lineup already optimal."
    parts = []
    for s in card["swaps"]:
        why = []
        if s["out"]["injury"]:
            why.append(f"{s['out']['name']} is {s['out']['injury']}")
        if s["out"]["bye"]:
            why.append(f"{s['out']['name']} is on bye")
        why.append(f"{s['in']['name']} projects {s['gain']:+.1f} pts in the {s['slot']} slot")
        opp = s["in"].get("opp")
        if opp:
            # "(LAR)" beside a Chargers quarterback reads as his club. The
            # away marker is already in the string; the home case needs the
            # word or the line is ambiguous.
            why[-1] += f" ({opp})" if opp.startswith("@") else f" (vs {opp})"
        parts.append("; ".join(why) + ".")
        # When the market is WHY this swap exists, say so — a reader who sees
        # a smaller projection winning deserves the reason on the card rather
        # than in a tooltip.
        env_in = (s["in"].get("env") or {}).get("pct") or 0
        env_out = (s["out"].get("env") or {}).get("pct") or 0
        if abs(env_in - env_out) >= 5:
            parts.append(
                f"The market likes the spot: {s['in']['name']}'s game is worth "
                f"{env_in:+.0f}% against {s['out']['name']}'s {env_out:+.0f}%.")
        if s.get("risk"):
            # str.capitalize() would lowercase every player name in the tail
            parts.append(s["risk"][0].upper() + s["risk"][1:] + ".")
    parts.append(f"Net {card['delta']:+.1f} projected points.")
    if card.get("wx_concerns"):
        parts.append("Weather on the slate: " + "; ".join(card["wx_concerns"]) + ".")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# The Parlor — trade suggestions
# ---------------------------------------------------------------------------

def draft_grades(conn: sqlite3.Connection) -> dict:
    """The Report Card: grade every seat of the completed draft on the
    league's own curve. Works for scrimmages too — a practice room gets the
    same treatment, with anonymous seat labels."""
    drow = conn.execute("SELECT * FROM drafts WHERE draft_id=?", (DEMO_DRAFT_ID,)).fetchone() \
        if settings.mode == "demo" else conn.execute(
            "SELECT * FROM drafts ORDER BY updated_at DESC LIMIT 1").fetchone()
    not_ready = {"ready": False,
                 "note": "The report card is written when the draft is in the books."}
    if not drow:
        return not_ready
    draft_id = drow["draft_id"]
    dsettings = json.loads(drow["settings_json"]) if drow["settings_json"] else {}
    my_slot = int(dsettings.get("slot", settings.my_roster_id))
    n_picks = conn.execute("SELECT COUNT(*) c FROM draft_picks WHERE draft_id=?",
                           (draft_id,)).fetchone()["c"]
    total = int(dsettings.get("teams", settings.teams)) * int(dsettings.get("rounds", settings.rounds))
    # A cancelled room can carry the 'complete' label with no picks — grading
    # zero seats would crash the curve math, so an empty book is never ready.
    if n_picks == 0 or not draft_is_complete(drow["status"], n_picks, total):
        return not_ready
    practice = draft_id == db.meta_get(conn, "practice_draft_id")

    players = _players_index(conn)
    cons = {r["player_id"]: r for r in conn.execute("SELECT * FROM consensus WHERE week=0")}
    _pref = {"sleeper": 0, "demo": 1, "ffc": 2, "fp_ecr": 3}
    adp: dict[str, float] = {}
    adp_src: dict[str, int] = {}
    for r in conn.execute("SELECT * FROM adp"):
        pid, p = r["player_id"], _pref.get(r["source"], 9)
        if pid not in adp or p < adp_src[pid]:
            adp[pid], adp_src[pid] = r["adp"], p

    owners = {r["roster_id"]: r["owner"] for r in conn.execute("SELECT * FROM rosters")}
    rp = roster_positions(conn)
    n_starting = len([s for s in rp if s not in ("BN", "IR", "TAXI")])

    by_slot: dict[int, list[sqlite3.Row]] = {}
    for p in conn.execute(
            "SELECT * FROM draft_picks WHERE draft_id=? ORDER BY pick_no", (draft_id,)):
        by_slot.setdefault(p["draft_slot"], []).append(p)

    teams, steal = [], None
    for slot, picks in sorted(by_slot.items()):
        ids = [p["player_id"] for p in picks if p["player_id"] in players]
        pool = [PlayerProj(pid, players[pid]["pos"],
                           (cons[pid]["pts_robust"] or 0.0) if pid in cons else 0.0,
                           players[pid]["name"],
                           ros_status(players[pid]["injury_status"]))
                for pid in ids]
        starters = optimize(pool, rp).total
        vbd_total = sum(max(0.0, (cons[pid]["vbd"] or 0.0)) for pid in ids if pid in cons)
        depth = max(0, sum(1 for pid in ids
                           if pid in cons and (cons[pid]["vbd"] or 0) > 0) - n_starting)

        surplus, best, reach = 0.0, None, None
        for p in picks:
            pid = p["player_id"]
            if pid not in players or pid not in adp:
                continue
            d = adp[pid] - p["pick_no"]           # +N picks of market value
            surplus += d
            entry = {"name": players[pid]["name"], "pos": players[pid]["pos"],
                     "pick_no": p["pick_no"], "adp": round(adp[pid], 1),
                     "surplus": round(d, 1)}
            if best is None or d > best["surplus"]:
                best = entry
            if reach is None or d < reach["surplus"]:
                reach = entry
            if d > 0 and (steal is None or d > steal["surplus"]):
                steal = {**entry, "slot": slot}

        risks = [(players[pid]["injury_risk"], (cons[pid]["pts_robust"] or 0.0))
                 for pid in ids if pid in cons and players[pid]["injury_risk"] is not None]
        wsum = sum(w for _, w in risks)
        risk = round(sum(r * w for r, w in risks) / wsum, 1) if wsum else None

        # A CPU practice room reuses slot numbers that collide with the real
        # league's roster ids — never paste the league's owner names on bots.
        owner = f"Seat {slot}" if practice else \
            (owners.get(picks[0]["roster_id"]) or f"roster {picks[0]['roster_id']}")
        teams.append({
            "slot": slot, "owner": owner, "mine": slot == my_slot,
            "starters": round(starters, 1), "vbd": round(vbd_total, 1),
            "surplus": round(surplus, 1), "depth": depth, "risk": risk,
            "best_pick": best, "reach": reach,
        })

    teams = grades_engine.compose(teams)
    for t in teams:
        t["note"] = grades_engine.seat_note(t)
    if steal is not None:
        seat = next((t for t in teams if t["slot"] == steal["slot"]), None)
        steal["owner"] = seat["owner"] if seat else f"Seat {steal['slot']}"
    return {"ready": True, "draft_id": draft_id, "practice": practice,
            "teams": teams, "steal": steal}


def league_rosters(conn: sqlite3.Connection) -> dict:
    """Every roster with its owner and ranked players — feeds the Parlor's
    back-table deal checker. Players sorted by consensus points so each pool
    reads like a depth chart, not an id dump."""
    players = _players_index(conn)
    cons = {r["player_id"]: r for r in conn.execute("SELECT * FROM consensus WHERE week=0")}
    out = []
    for r in conn.execute("SELECT * FROM rosters ORDER BY roster_id"):
        ids = json.loads(r["players_json"])
        ps = [{"id": pid, "name": players[pid]["name"], "pos": players[pid]["pos"],
               "team": players[pid]["team"],
               "pts": round((cons[pid]["pts_robust"] or 0.0) if pid in cons else 0.0, 1)}
              for pid in ids if pid in players]
        ps.sort(key=lambda p: -p["pts"])
        out.append({"roster_id": r["roster_id"],
                    "owner": r["owner"] or f"roster {r['roster_id']}",
                    "mine": r["roster_id"] == settings.my_roster_id,
                    "players": ps})
    return {"rosters": out}


LEAGUE_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")
_FLEX_SLOTS = ("FLEX", "SUPER_FLEX", "REC_FLEX", "WRRB_FLEX", "BN", "IR", "TAXI")
_READ_Z = 0.8   # how far off the field a room must sit before it's worth saying


def _norm_pos(pos: str) -> str:
    return "DEF" if pos in ("DST", "D/ST") else pos


def _starting_slots(conn: sqlite3.Connection) -> dict[str, int]:
    """How many of each position the league actually starts — the yardstick a
    room is measured against. FLEX is excluded on purpose: it belongs to no
    single position, so counting it would flatter whichever room fills it."""
    counts: dict[str, int] = {}
    for slot in roster_positions(conn):
        if slot in _FLEX_SLOTS:
            continue
        counts[slot] = counts.get(slot, 0) + 1
    return counts


def schedule_strength(conn: sqlite3.Connection, from_week: int = 1) -> dict:
    """Every club's PRICED schedule, and how much of a schedule that is.

    Market-implied only, which makes this honest and thin at the same time.
    Books post a week or two out, so most of the season has no line and this
    read covers whatever they have got to. That coverage is the headline
    alongside the number: "+2.1 over three games" and "+2.1 over fourteen" are
    different claims, and a season-shaped number built from two weeks would be
    the more dangerous of the two.

    It does NOT move any price. A waiver bid or a trade value swung by two
    weeks of betting lines would be fitting noise — the schedule shows up as
    context on the row and stays out of the arithmetic until there is enough
    of it to mean something. Same posture engines/advisories.py takes with
    position pressure.
    """
    rows = conn.execute(
        "SELECT team, week, implied_total FROM nfl_games "
        "WHERE season=? AND week>=? ORDER BY team, week",
        (settings.season, from_week)).fetchall()
    by_team: dict[str, dict[int, float | None]] = {}
    for r in rows:
        by_team.setdefault(r["team"], {})[r["week"]] = r["implied_total"]

    priced = [v for weeks in by_team.values() for v in weeks.values() if v is not None]
    league_mean, mean_note = env_engine.slate_mean(priced)
    reads = [env_engine.schedule_read(t, w, league_mean).as_dict()
             for t, w in sorted(by_team.items())]
    reads.sort(key=lambda r: -(r["vs_league"] if r["vs_league"] is not None else -99))
    total_games = sum(len(w) for w in by_team.values())
    return {
        "teams": reads,
        "league_mean": round(league_mean, 1),
        "note": mean_note,
        "priced": len(priced),
        "total": total_games,
        "advisory": ("Market-implied and display-only — it does not move a bid "
                     "or a trade value. Books post a week or two out, so this "
                     "covers what they have priced, not a season."),
    }


def league_overview(conn: sqlite3.Connection) -> dict:
    """Every seat scouted on one screen: what it can actually start, how each
    of its rooms sits against the field, and the season it played.

    Ranked on the optimal STARTING lineup, never the whole roster — a seat
    hoarding four good backs on the bench should not outrank a balanced
    contender, which is exactly the inversion total-roster points produces.
    The per-position z-scores are the raw material the Parlor turns into a
    deal: surplus needs both quality and a spare body behind it."""
    from .engines.lineup import PlayerProj, optimize

    slots = roster_positions(conn)
    starting = _starting_slots(conn)
    records = {r["roster_id"]: r for r in conn.execute(
        "SELECT roster_id,wins,losses,ties,fpts FROM rosters")}

    seats = []
    for r in league_rosters(conn)["rosters"]:
        players = r["players"]
        proj = optimize([PlayerProj(p["id"], _norm_pos(p["pos"]), p["pts"])
                         for p in players], slots).total
        by_pos = {}
        for pos in LEAGUE_POSITIONS:
            room = sorted((p["pts"] for p in players if _norm_pos(p["pos"]) == pos),
                          reverse=True)
            starts = starting.get(pos, 1)
            by_pos[pos] = {"pts": round(sum(room[:starts]), 1),
                           "depth": len(room), "starts": starts}
        rec = records.get(r["roster_id"])
        seats.append({
            "roster_id": r["roster_id"], "owner": r["owner"], "mine": r["mine"],
            "proj": round(proj, 1), "by_pos": by_pos,
            "record": {"wins": rec["wins"], "losses": rec["losses"],
                       "ties": rec["ties"], "fpts": round(rec["fpts"], 1)} if rec else None,
        })

    # Score each room against the field. Left unrounded: a z-score that has
    # been rounded no longer sums to zero, and the grid's glyphs are cut from
    # these thresholds.
    for pos in LEAGUE_POSITIONS:
        vals = [s["by_pos"][pos]["pts"] for s in seats]
        mean = sum(vals) / len(vals)
        sd = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5 or 1.0
        for s in seats:
            s["by_pos"][pos]["z"] = (s["by_pos"][pos]["pts"] - mean) / sd

    for s in seats:
        room = s["by_pos"]
        # Surplus is quality AND a spare body — a great room you must start
        # every week is not tradeable.
        surplus = sorted((p for p in LEAGUE_POSITIONS
                          if room[p]["z"] >= _READ_Z and room[p]["depth"] > room[p]["starts"]),
                         key=lambda p: -room[p]["z"])
        need = sorted((p for p in LEAGUE_POSITIONS if room[p]["z"] <= -_READ_Z),
                      key=lambda p: room[p]["z"])
        bits = []
        if surplus:
            bits.append("deep at " + "/".join(surplus[:2]))
        if need:
            bits.append("thin at " + "/".join(need[:2]))
        s["surplus"], s["need"] = surplus, need
        s["read"] = " · ".join(bits) or "balanced across the board"

    seats.sort(key=lambda s: -s["proj"])
    for i, s in enumerate(seats, start=1):
        s["rank"] = i

    played = any((s["record"] or {}).get("wins", 0) + (s["record"] or {}).get("losses", 0)
                 + (s["record"] or {}).get("ties", 0) for s in seats)
    return {"seats": seats, "records_ready": played,
            "note": None if played else "Records open Week 1."}


def what_would_it_take(conn: sqlite3.Connection, target_id: str,
                       limit: int = 6) -> dict:
    """Packages that would plausibly get you one named player.

    The suggester answers "what deals exist in this room". This answers the
    question a manager actually asks out loud — "what would it take to get
    HIM" — which no amount of scanning surfaces, because the deal you want is
    rarely the deal that scores highest across every seat.

    The filters are the other side's, not yours. A package only appears if the
    seat holding him ENDS UP BETTER: they lose their man and must get enough
    back that their own optimal lineup improves. Anything else is a wish, and
    a list of wishes is what makes a trade tool ignorable.
    """
    desk = _TradeDesk(conn)
    if not desk.my_ids:
        return {"target": None, "offers": [], "note": "The parlor opens once rosters exist."}
    if target_id not in desk.players:
        return {"target": None, "offers": [], "note": "No such player on the books."}

    holder = None
    for r in conn.execute("SELECT * FROM rosters"):
        if target_id in json.loads(r["players_json"] or "[]"):
            holder = r
            break
    if holder is None:
        return {"target": desk.row(target_id), "offers": [],
                "note": "Nobody rosters him — he is a waiver add, not a trade."}
    if desk.my and holder["roster_id"] == desk.my["roster_id"]:
        return {"target": desk.row(target_id), "offers": [],
                "note": "He is already yours."}

    their_ids = json.loads(holder["players_json"] or "[]")
    base_mine = desk.best(desk.my_ids)
    base_theirs = desk.best(their_ids)
    target_worth = desk.worth(target_id)

    # What I can offer: anything that is not a man I would refuse to lose.
    # Sorted by worth so the cheapest sufficient package is found first.
    mine = sorted((i for i in desk.my_ids if i in desk.players),
                  key=desk.worth, reverse=True)
    # A package is not worth enumerating if it cannot approach his value, and
    # not worth PROPOSING if it wildly exceeds it — overpaying by half is how
    # you win a trade and lose a season.
    singles = [[i] for i in mine]
    # THE WHOLE SHELF, not its top eight. `mine` is sorted worth-descending, so
    # slicing to 8 cut off exactly the cheap throw-ins a two-man package needs
    # — measured on the demo world, the one pair that cleared every filter for
    # one target was index 2 plus index 10, and the panel wrongly answered
    # "nothing you own gets him". A 15-man roster is 105 pairs; the filters
    # below discard almost all of them long before the optimizer runs.
    pairs = [[mine[i], mine[j]]
             for i in range(len(mine))
             for j in range(i + 1, len(mine))]
    # A sweetener from their side: him plus a spare of theirs, for a bigger
    # piece of mine. This is the shape that unlocks a stud when nothing I own
    # matches him one-for-one.
    their_spares = sorted(
        (i for i in their_ids if i in desk.players and i != target_id
         and i not in base_theirs.starter_ids),
        key=desk.worth, reverse=True)[:3]

    offers = []
    seen: set[tuple] = set()

    def consider(give: list[str], get: list[str]) -> None:
        key = (frozenset(give), frozenset(get))
        if key in seen:
            return
        seen.add(key)
        ca = trades_engine.consolidated([desk.worth(x) for x in give])
        cb = trades_engine.consolidated([desk.worth(x) for x in get])
        if max(ca, cb) <= 0 or abs(cb - ca) > 0.45 * max(ca, cb):
            return
        my_gain = desk.best([i for i in desk.my_ids if i not in give] + get).total - base_mine.total
        their_gain = desk.best([i for i in their_ids if i not in get] + give).total - base_theirs.total
        # THEIR filter, applied honestly: a seat that ends up worse says no,
        # and a package they decline is not an offer, it is a daydream.
        if their_gain < 0.5:
            return
        # ...and MINE. A deal that gets the man but leaves my starting lineup
        # no better is a trade I won, on paper, for nothing.
        if my_gain < 1.0:
            return
        detail = trades_engine.analyze(
            [desk.row(x) for x in give], [desk.row(x) for x in get],
            vbd_scale=desk.vbd_scale, market_scale=desk.mkt_scale)
        volume = sum(abs((desk.vals[x]["redraft_value"] or 0.0))
                     if x in desk.vals else 0.0 for x in give + get)
        cost = sum(desk.worth(x) for x in give)
        offers.append({
            # CHEAPEST acceptable first. Ranking by their_gain — which is what
            # this did on its first run — rewards overpaying: it put "give up
            # Nacua and Kelce, hand that seat +146 season points" at the top of
            # a list whose entire question is what the man costs. The floor of
            # what they will take is the answer; the margin above it is
            # information, not a target.
            # CHEAPEST BAND first, then what it does for you. Ranking by
            # their_gain — which this did on its first run — rewards
            # overpaying: it put "give up Nacua and Kelce, hand that seat +146
            # season points" at the top of a list whose entire question is what
            # the man costs. But strict cost ordering is false precision too:
            # 75.4 and 75.0 are the same price, and between them the package
            # that helps your lineup more is plainly the better ask. So cost is
            # quantized to a 5-point band and my_gain breaks ties inside it —
            # the band is wide enough that no realistic gain can jump one.
            "score": round(-(round(cost / 5.0) * 5.0) + 0.02 * my_gain, 3),
            "cost": round(cost, 1),
            "partner": holder["owner"] or f"roster {holder['roster_id']}",
            "partner_roster_id": holder["roster_id"],
            "give_ids": give, "receive_ids": get,
            "give": [desk.row(x) for x in give],
            "receive": [desk.row(x) for x in get],
            "my_gain": round(my_gain, 1),
            "their_gain": round(their_gain, 1),
            "vbd_edge": detail["vbd_edge"],
            "market_edge": detail["market_edge"],
            "summary": detail["summary"],
            "verdict": trades_engine.value_verdict(
                my_gain, detail["vbd_edge"], detail["market_edge"],
                len(give), len(get), market_volume=volume),
        })

    for g in singles:
        consider(g, [target_id])
    for g in pairs:
        consider(g, [target_id])
    for spare in their_spares:
        for g in pairs:
            consider(g, [target_id, spare])

    offers.sort(key=lambda o: -o["score"])
    kept = trades_engine.shortlist(offers, limit=limit)
    return {
        "target": desk.row(target_id),
        "target_name": desk.players[target_id]["name"],
        "holder": holder["owner"] or f"roster {holder['roster_id']}",
        "offers": kept,
        "considered": len(offers),
        "note": ("Every package here leaves the other seat better off than it "
                 "started — that is the filter. Cheapest first, ties broken by "
                 "what it does for your lineup: the answer is what he costs, "
                 "not the most you could bear to pay.")
        if kept else
        ("Nothing you own gets him without leaving that seat worse off. "
         "He is either their cornerstone or your shelf is the wrong shape."),
    }


class _TradeDesk:
    """The valuation every trade tool shares.

    Two tools price trades — the suggester that scans the room for deals, and
    the counter-offer generator that answers "what would it take for HIM".
    They have to agree about what a player is worth or the Parlor tells two
    stories about one roster, so the worth curve, the ROS projections, the
    lineup optimizer and the row shape live here once.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.rp = roster_positions(conn)
        self.players = _players_index(conn)
        self.cons = {r["player_id"]: r for r in
                     conn.execute("SELECT * FROM consensus WHERE week=0")}
        self.vals = {r["player_id"]: r for r in
                     conn.execute("SELECT * FROM player_values")}
        self.my = my_roster_row(conn)
        self.my_ids = json.loads(self.my["players_json"]) if self.my else []
        self.vbd_scale = max([(r["vbd"] or 0.0) for r in self.cons.values()] or [1.0]) or 1.0
        self.mkt_scale = max([(r["redraft_value"] or 0.0)
                              for r in self.vals.values()] or [1.0]) or 1.0
        self._worth: dict[str, float] = {}
        # The market's read on each club's priced games, for context on a
        # trade card. It moves no price — see schedule_strength() for why two
        # weeks of betting lines must not value a season asset — but "the man
        # you are buying has the softest priced run in the league" is worth
        # knowing before you send the offer.
        self.sos: dict[str, float] = {}
        try:
            # FROM THIS WEEK ON. At the default from_week=1 this averaged every
            # already-played week's closing line and sold a season-to-date
            # review as a look-ahead — it can render the opposite sign of the
            # schedule you are actually buying. A club implied 32 through a
            # played week 1-9 and 15.5 in the two weeks left reads +6.2 looking
            # back and -5.8 looking forward, and only one of those is a reason
            # to make the trade.
            wk = int(db.meta_get(conn, "current_week") or 1)
            read = schedule_strength(conn, from_week=max(1, wk))
            self.sos = {t["team"]: t["vs_league"] for t in read["teams"]
                        if t["vs_league"] is not None}
        except (sqlite3.Error, ValueError, TypeError):
            self.sos = {}

    def worth(self, pid: str) -> float:
        """Half projection, half market, both normalized — the currency both
        tools trade in."""
        if pid not in self._worth:
            v = (self.cons[pid]["vbd"] or 0.0) if pid in self.cons else 0.0
            m = (self.vals[pid]["redraft_value"] or 0.0) if pid in self.vals else 0.0
            self._worth[pid] = (50.0 * max(0.0, v) / self.vbd_scale
                                + 50.0 * max(0.0, m) / self.mkt_scale)
        return self._worth[pid]

    def projs(self, ids: list[str]) -> list[PlayerProj]:
        # ROS math: a one-week Out tag must not zero a season asset.
        return [PlayerProj(pid, self.players[pid]["pos"],
                           ((self.cons[pid]["pts_robust"] or 0.0)
                            if pid in self.cons else 0.0),
                           self.players[pid]["name"],
                           ros_status(self.players[pid]["injury_status"]))
                for pid in ids if pid in self.players]

    def best(self, ids: list[str]):
        return optimize(self.projs(ids), self.rp)

    def row(self, pid: str) -> dict:
        team = self.players[pid]["team"]
        return {"player_id": pid, "name": self.players[pid]["name"],
                "pos": self.players[pid]["pos"], "team": team,
                "vbd": (self.cons[pid]["vbd"] or 0.0) if pid in self.cons else 0.0,
                "market_value": ((self.vals[pid]["redraft_value"] or 0.0)
                                 if pid in self.vals else 0.0),
                "sos": self.sos.get(team or "")}


def suggest_trades(conn: sqlite3.Connection, limit: int = 8) -> dict:
    """Scan every opposing roster for deals that help BOTH starting lineups —
    mutual benefit is what actually gets accepted (the trade-finder lesson
    from FantasyPros/Dynasty Daddy). Candidates come from surplus-for-need:
    each side's best bench pieces and weakest starters, in 1-for-1 and
    consolidation (2-for-1) shapes, package-checked with the KTC-school
    curve, then graded by both sides' optimal-lineup deltas."""
    desk = _TradeDesk(conn)
    if not desk.my_ids:
        return {"trades": [], "note": "The parlor opens once rosters exist."}
    rp, players = desk.rp, desk.players
    vbd_scale, mkt_scale = desk.vbd_scale, desk.mkt_scale
    worth, best, row = desk.worth, desk.best, desk.row
    my, my_ids = desk.my, desk.my_ids
    base_mine = best(my_ids)
    proposals: dict[tuple, dict] = {}

    for r in conn.execute("SELECT * FROM rosters"):
        if my and r["roster_id"] == my["roster_id"]:
            continue
        their_ids = json.loads(r["players_json"] or "[]")
        if not their_ids:
            continue
        base_theirs = best(their_ids)

        def pools(ids: list[str], starters: set[str]) -> tuple[list[str], list[str]]:
            bench = sorted((i for i in ids if i not in starters and i in players),
                           key=lambda i: -worth(i))[:4]
            weak = sorted((i for i in starters if i in players), key=worth)[:2]
            return bench, weak

        my_bench, my_weak = pools(my_ids, base_mine.starter_ids)
        th_bench, th_weak = pools(their_ids, base_theirs.starter_ids)
        their_studs = sorted((i for i in base_theirs.starter_ids if i in players),
                             key=lambda i: -worth(i))[:2]
        my_studs = sorted((i for i in base_mine.starter_ids if i in players),
                          key=lambda i: -worth(i))[:2]

        cands: list[tuple[list[str], list[str]]] = []
        for g in my_bench + my_weak:
            for t in th_bench + th_weak:
                cands.append(([g], [t]))
        for i in range(len(my_bench)):
            for j in range(i + 1, len(my_bench)):
                for t in their_studs:
                    cands.append(([my_bench[i], my_bench[j]], [t]))
        for i in range(len(th_bench)):
            for j in range(i + 1, len(th_bench)):
                for g in my_studs:
                    cands.append(([g], [th_bench[i], th_bench[j]]))
        # 2-for-2: positional rebalances a 1-for-1 can't express (my WR + spare
        # RB for their RB + spare WR). Pools capped at 4 a side — 36 pairs per
        # partner keeps the Pi's Hungarian budget honest.
        my_two = (my_bench + my_weak)[:4]
        th_two = (th_bench + th_weak)[:4]
        for i in range(len(my_two)):
            for j in range(i + 1, len(my_two)):
                for k in range(len(th_two)):
                    for m in range(k + 1, len(th_two)):
                        cands.append(([my_two[i], my_two[j]], [th_two[k], th_two[m]]))

        for give, get in cands:
            ca = trades_engine.consolidated([worth(x) for x in give])
            cb = trades_engine.consolidated([worth(x) for x in get])
            # package pre-filter: nobody accepts a lopsided-by-half offer
            if max(ca, cb) <= 0 or abs(cb - ca) > 0.45 * max(ca, cb):
                continue
            my_gain = best([i for i in my_ids if i not in give] + get).total - base_mine.total
            if my_gain < 2.0:
                continue
            their_gain = best([i for i in their_ids if i not in get] + give).total - base_theirs.total
            if their_gain < -3.0:  # they must not be materially hurt, or they decline
                continue
            key = (frozenset(give), frozenset(get))
            score = my_gain + 0.3 * max(0.0, their_gain) - 0.1 * max(0.0, -their_gain)
            if key in proposals and proposals[key]["score"] >= score:
                continue
            detail = trades_engine.analyze([row(p) for p in give], [row(p) for p in get],
                                           vbd_scale=vbd_scale, market_scale=mkt_scale)
            proposals[key] = {
                "score": round(score, 2),
                "partner": r["owner"] or f"roster {r['roster_id']}",
                "partner_roster_id": r["roster_id"],
                # Through desk.row so a man carries the same fields — club and
                # schedule read included — wherever the Parlor draws him. The
                # suggester used to build a thinner dict of its own, which is
                # how the schedule chip reached the ask panel and not these.
                "give": [{"id": p, **desk.row(p)} for p in give],
                "receive": [{"id": p, **desk.row(p)} for p in get],
                # Flat id lists so the shortlist can reason about packages
                # without re-deriving them from the display rows.
                "give_ids": list(give),
                "receive_ids": list(get),
                "my_gain": round(my_gain, 1),
                "their_gain": round(their_gain, 1),
                "vbd_edge": detail["vbd_edge"],
                "market_edge": detail["market_edge"],
                "summary": detail["summary"],
            }

    # Enumerating packages produces the same deal many times over (a throw-in
    # that cracks neither lineup moves neither number). Shortlisting is what
    # turns that enumeration into a list a human can read — see
    # engines/trades.shortlist for the three filters and why each exists.
    top = trades_engine.shortlist(list(proposals.values()), limit=limit)
    for t in top:
        volume = sum(abs((desk.vals[p]["redraft_value"] or 0.0)) if p in desk.vals else 0.0
                     for p in t["give_ids"] + t["receive_ids"])
        t["verdict"] = trades_engine.value_verdict(
            t["my_gain"], t["vbd_edge"], t["market_edge"],
            len(t["give_ids"]), len(t["receive_ids"]), market_volume=volume)
    return {"trades": top,
            "considered": len(proposals),
            "note": "Both lineups improve or the other side isn't hurt — deals that can actually close. Advisory only."}


# ---------------------------------------------------------------------------
# The Scout's File — per-player dossier for the board
# ---------------------------------------------------------------------------

def player_dossier(conn: sqlite3.Connection, player_id: str) -> dict | None:
    """Everything the board can say about one player when clicked: per-source
    projections, expert-vs-street read, survival to my picks, roster-balance
    impact, and a few dry insight lines. Draft-time roster = my picks so far."""
    players = _players_index(conn)
    if player_id not in players:
        return None
    p = players[player_id]
    rp = roster_positions(conn)

    srcs = {r["source"]: r["pts"] for r in conn.execute(
        "SELECT source, pts FROM projections WHERE week=0 AND player_id=?", (player_id,))}
    ds_range = conn.execute(
        "SELECT floor, ceiling FROM projections WHERE week=0 AND source='draftsharks' "
        "AND player_id=?", (player_id,)).fetchone()
    cons = conn.execute("SELECT * FROM consensus WHERE week=0 AND player_id=?",
                        (player_id,)).fetchone()
    adp_rows = {r["source"]: r for r in conn.execute(
        "SELECT * FROM adp WHERE player_id=?", (player_id,))}
    ecr = adp_rows.get("fp_ecr")
    street = next((adp_rows[s] for s in ("sleeper", "demo", "ffc") if s in adp_rows), None)

    # Draft context: my roster so far + my next two picks.
    drow = conn.execute("SELECT * FROM drafts WHERE draft_id=?", (DEMO_DRAFT_ID,)).fetchone() \
        if settings.mode == "demo" else conn.execute(
            "SELECT * FROM drafts ORDER BY updated_at DESC LIMIT 1").fetchone()
    dsettings = json.loads(drow["settings_json"]) if drow and drow["settings_json"] else {}
    teams = int(dsettings.get("teams", settings.teams))
    rounds = int(dsettings.get("rounds", settings.rounds))
    my_slot = int(dsettings.get("slot", settings.my_roster_id))
    picks = conn.execute("SELECT * FROM draft_picks WHERE draft_id=? ORDER BY pick_no",
                         (drow["draft_id"],)).fetchall() if drow else []
    current_pick = len(picks) + 1
    my_next = draft_engine.next_pick_after(current_pick - 1, my_slot, teams, rounds)
    my_after = (draft_engine.next_pick_after(my_next, my_slot, teams, rounds)
                if my_next else None)
    my_ids = [x["player_id"] for x in picks if x["draft_slot"] == my_slot]

    surv_next = surv_wait = None
    if street or ecr:
        a = street or ecr
        stds = [r["stdev"] for r in adp_rows.values() if r["stdev"]]
        sigma = max(stds) if stds else None
        if my_next:
            surv_next = round(draft_engine.survival_prob(a["adp"], sigma, my_next), 3)
        if my_after:
            surv_wait = round(draft_engine.survival_prob(a["adp"], sigma, my_after), 3)

    cons_pts = {pid: (c["pts_robust"] or 0.0) for pid, c in
                ((r["player_id"], r) for r in conn.execute("SELECT * FROM consensus WHERE week=0"))}

    def projs(ids):
        return [PlayerProj(i, players[i]["pos"], cons_pts.get(i, 0.0),
                           players[i]["name"], ros_status(players[i]["injury_status"]))
                for i in ids if i in players]

    base = optimize(projs(my_ids), rp)
    with_him = optimize(projs(my_ids + [player_id]), rp)
    lineup_gain = round(with_him.total - base.total, 1)

    # Balance bars: my best current points per dedicated slot vs the position's
    # last-starter benchmark (teams × dedicated slots deep in consensus).
    dedicated = {q: rp.count(q) for q in ("QB", "RB", "WR", "TE", "K", "DEF")}
    by_pos_pts: dict[str, list[float]] = {}
    for pid2, pts in cons_pts.items():
        if pid2 in players:
            by_pos_pts.setdefault(players[pid2]["pos"], []).append(pts)
    balance = []
    for q, want in dedicated.items():
        if not want:
            continue
        ranked = sorted(by_pos_pts.get(q, []), reverse=True)
        bench_idx = min(teams * want - 1, len(ranked) - 1)
        benchmark = ranked[bench_idx] if ranked else 1.0
        mine = sorted((cons_pts.get(i, 0.0) for i in my_ids
                       if players[i]["pos"] == q), reverse=True)[:want]
        before = round(100 * (sum(mine) / max(want * benchmark, 1.0)))
        after_mine = sorted(mine + ([cons_pts.get(player_id, 0.0)] if p["pos"] == q else []),
                            reverse=True)[:want]
        after = round(100 * (sum(after_mine) / max(want * benchmark, 1.0)))
        balance.append({"pos": q, "have": len(mine), "want": want,
                        "before": min(before, 160), "after": min(after, 160)})

    # Insight lines, in the house voice.
    insights = []
    if ecr and street and (street["adp"] - ecr["adp"]) >= 4:
        insights.append(f"The room lets him fall — the experts have him {street['adp'] - ecr['adp']:.0f} "
                        "picks earlier than the street drafts him. A value window.")
    elif ecr and street and (ecr["adp"] - street["adp"]) >= 4:
        insights.append("The street reaches for him ahead of the experts' sheet — "
                        "you'll pay a premium over the consensus read.")
    if cons and cons["stdev"] and cons["pts_robust"]:
        rel = cons["stdev"] / max(cons["pts_robust"], 1)
        if rel > 0.06:
            insights.append(f"The sources argue about him (±{cons['stdev']:.0f} pts) — a swing pick.")
        elif len(srcs) >= 4:
            insights.append("Every source reads him the same — low-drama projection.")
    if p["bye"]:
        stacked = sum(1 for i in my_ids
                      if players[i]["bye"] == p["bye"] and players[i]["pos"] == p["pos"])
        if stacked:
            insights.append(f"Bye {p['bye']} stacks with {stacked} of your {p['pos']}s — "
                            "one dead week if you double up.")
    if my_ids:
        insights.append(f"Adds {lineup_gain:+.1f} season points to your best lineup today."
                        if lineup_gain > 0 else
                        "Depth today — he doesn't crack your starting lineup yet.")
    if p["injury_status"]:
        insights.append(f"Carries a {p['injury_status']} tag — the wire will tell you more than the sheet.")
    try:
        risk, games = p["injury_risk"], p["proj_games"]
    except (IndexError, KeyError):
        risk = games = None
    if risk is not None and risk >= 40:
        insights.append(f"The sharks put his injury risk at {risk:.0f}%"
                        + (f" — {games:.0f} games projected." if games else "."))

    return {
        "id": player_id, "name": p["name"], "pos": p["pos"], "team": p["team"],
        "bye": p["bye"], "injury": p["injury_status"],
        "sources": {k: round(v, 1) for k, v in sorted(srcs.items())},
        "consensus": round(cons["pts_robust"], 1) if cons else None,
        "spread": round(cons["stdev"], 1) if cons and cons["stdev"] else None,
        "tier": cons["tier"] if cons else None,
        "vbd": round(cons["vbd"], 1) if cons and cons["vbd"] is not None else None,
        "ecr": round(ecr["adp"], 1) if ecr else None,
        "street_adp": round(street["adp"], 1) if street else None,
        "ds_floor": round(ds_range["floor"], 1) if ds_range and ds_range["floor"] else None,
        "ds_ceiling": round(ds_range["ceiling"], 1) if ds_range and ds_range["ceiling"] else None,
        "injury_risk": risk, "proj_games": games,
        "survival_next": surv_next, "survival_wait": surv_wait,
        "my_next_pick": my_next,
        "lineup_gain": lineup_gain,
        "balance": balance,
        "insights": insights,
    }


def waiver_targets(conn: sqlite3.Connection, heat: dict[str, int] | None = None) -> dict:
    """Free agents worth a bid, priced against this league's own book.

    Found by FA score (points over the weakest body at the same position on my
    shelf), then RE-RANKED on what each man is worth to this roster — the
    lineup he cracks today plus a share of that score for option value. The
    bid is that rank's percentile of the league's winning bids; depth pays
    half. Tier-bucketed sizing was replaced by continuous pricing (see
    engines/waivers.py) — a three-step staircase priced a 33-point add and a
    2.5-point add identically. `heat` is Sleeper trending-add counts.
    """
    heat = heat or {}
    rp = roster_positions(conn)
    rostered: set[str] = set()
    for r in conn.execute("SELECT players_json FROM rosters"):
        rostered |= set(json.loads(r["players_json"]))
    # Pre-draft, nobody is rostered, so "free agents ranked by score" is just
    # the top of the player pool — Josh Allen at a $100 bid. Refuse honestly.
    if not rostered:
        return {"targets": [], "history_n": 0,
                "note": "Everyone's a free agent until the draft — "
                        "the street opens once rosters exist."}
    my = my_roster_row(conn)
    my_ids = json.loads(my["players_json"]) if my else []
    cons = {r["player_id"]: r for r in conn.execute("SELECT * FROM consensus WHERE week=0")}
    players = {r["sleeper_id"]: r for r in conn.execute("SELECT * FROM players")}

    # Bid history bucketed by value tier (the engine's contract). Demo history
    # labels each txn's tier in adds_json; unlabeled (live) bids bucket by bid
    # size, mirroring the demo generator's bands (hot 18+, solid 6+, dart <6).
    hist_by_tier: dict[str, list[float]] = {"hot": [], "solid": [], "dart": []}
    bids_hist: list[float] = []
    for r in conn.execute(
            "SELECT faab, adds_json FROM transactions WHERE type='waiver' AND faab IS NOT NULL"):
        faab = r["faab"]
        bids_hist.append(faab)
        try:
            tier = (json.loads(r["adds_json"] or "{}") or {}).get("tier")
        except (ValueError, TypeError):
            tier = None
        if tier not in hist_by_tier:
            tier = "hot" if faab >= 18 else "solid" if faab >= 6 else "dart"
        hist_by_tier[tier].append(faab)

    worst_by_pos: dict[str, float] = {}
    for pid in my_ids:
        if pid not in players or pid not in cons:
            continue
        pos = players[pid]["pos"]
        v = cons[pid]["pts_robust"] or 0
        worst_by_pos[pos] = min(worst_by_pos.get(pos, 1e9), v)

    # Score everyone first, then rank within this week's pool. Fixed point
    # thresholds silently break when the value scale changes: fa_score runs on
    # season-scale consensus in-season, so a 30/10 cut put every target in
    # "hot" and every bid flat-lined at the same dollar.
    scored = []
    for pid, c in cons.items():
        if pid in rostered or pid not in players:
            continue
        p = players[pid]
        score = waivers_engine.fa_score(c["pts_robust"] or 0,
                                        worst_by_pos.get(p["pos"], 0))
        if score <= 0:
            continue
        scored.append((pid, p, score))
    scored.sort(key=lambda t: -t[2])
    n = len(scored)

    # Would he crack the lineup? Computed for the whole shortlist up front,
    # because price depends on it: a man who does not start is depth, and
    # depth pays the depth price.
    my_pool = [PlayerProj(pid, players[pid]["pos"],
                          (cons[pid]["pts_robust"] or 0.0) if pid in cons else 0.0,
                          players[pid]["name"],
                          ros_status(players[pid]["injury_status"]))
               for pid in my_ids if pid in players]
    base_lineup = optimize(my_pool, rp)
    base_total = base_lineup.total
    shortlist = scored[:20]

    def gain_for(pid: str, pos: str, name: str) -> float:
        cand = PlayerProj(pid, pos,
                          (cons[pid]["pts_robust"] or 0.0) if pid in cons else 0.0,
                          name, ros_status(players[pid]["injury_status"]))
        return round(optimize(my_pool + [cand], rp).total - base_total, 1)

    gains = {pid: (gain_for(pid, p["pos"], p["name"]) if my_ids else None)
             for pid, p, _ in shortlist}

    # Re-rank on what he is worth TO THIS ROSTER, not to a generic one.
    # fa_score measures a man against the worst body at his position, which is
    # the right way to find him and the wrong way to price him: a receiver who
    # out-scores your WR5 but never cracks the lineup is worth less than a back
    # who starts on Sunday. So the ordering blends the immediate lineup gain
    # with a fraction of fa_score standing in for option value — depth, byes,
    # and the injury you haven't had yet.
    OPTION_WEIGHT = 0.35
    shortlist.sort(key=lambda t: -(max(0.0, gains.get(t[0]) or 0.0)
                                   + OPTION_WEIGHT * t[2]))

    # The denominator has to measure the SAME set the rank comes from. It used
    # to be `n` — every free agent with a pulse — while the rank ran over the
    # twenty being priced, so the whole visible ladder was squeezed into the
    # top 20/n of the book. Measured against the shipped price_at() and a real
    # 90-bid book (median $6, max $50): at a 300-man pool the twentieth target
    # was quoted $37 and all twenty tripped the 25%-of-budget confirm-twice
    # warning — the cry-wolf failure this house guards against everywhere else.
    # It is also the exact failure continuous pricing replaced tiers to fix,
    # arriving from the other end.
    #
    # The demo cannot reach it: 168 of its 182 players end up rostered, leaving
    # five free agents, where the two denominators nearly agree.
    #
    # Ranking the priced list against itself is also the faithful reading of
    # the rule in engines/waivers.py: the historical book records what this
    # room paid for men it actually bid on, not for the 300th-best free agent,
    # and the weeks with nothing worth having are already in that book as its
    # $1 and $2 entries.
    priced = len(shortlist)
    out = []
    for rank, (pid, p, score) in enumerate(shortlist):
        value_pct = 1.0 - (rank / (priced - 1)) if priced > 1 else 1.0
        starts = bool(gains.get(pid)) and (gains.get(pid) or 0) > 0
        if bids_hist:
            bid = waivers_engine.price_at(value_pct, bids_hist,
                                          settings.faab_budget, starts=starts)
        else:  # no book to read: fall back to the score-proportional rule
            bid = waivers_engine.size_bid(score, [], settings.faab_budget).bid
        out.append({
            "id": pid, "name": p["name"], "pos": p["pos"], "team": p["team"],
            "fa_score": round(score, 1), "bid": bid,
            "value_pct": round(value_pct, 3),
            "hard_confirm": bid > waivers_engine.HARD_CONFIRM_FRACTION * settings.faab_budget,
            # the row's OWN tier — `c` here would be a stale loop leftover
            "tier": cons[pid]["tier"] if pid in cons else None,
            "heat": heat.get(pid, 0),
            "lineup_gain": gains.get(pid),
        })
    for row, bid in zip(out, waivers_engine.enforce_ladder([r["bid"] for r in out])):
        row["bid"] = bid
        row["hard_confirm"] = bid > waivers_engine.HARD_CONFIRM_FRACTION * settings.faab_budget
    top = out

    # Schedule context: who the target plays this week, and the bid traps —
    # on bye now (he can't help the week you bought him) or next week.
    wk_now = int(db.meta_get(conn, "current_week") or 0)
    if wk_now:
        gnow = {g["team"]: g for g in conn.execute(
            "SELECT * FROM nfl_games WHERE season=? AND week=?",
            (settings.season, wk_now))}
        for t in top:
            g = gnow.get(t["team"])
            byew = players[t["id"]]["bye"]
            t["opp"] = (("" if g["is_home"] else "@") + (g["opponent"] or "")) if g else None
            t["bye_now"] = byew == wk_now
            t["bye_next"] = byew == wk_now + 1
            t["imp"] = g["implied_total"] if g else None
            t["wx"] = schedule.weather_flags(g)

    # Who leaves. A bid the owner cannot execute is half an answer, and in a
    # full-roster league every add IS a drop. The man to cut is the one whose
    # absence costs the optimal lineup least — computed by removing him and
    # re-optimising, never by raw points (a bench quarterback with a big
    # number is worth less than a startable flex with a small one).
    drop = drop_candidate(conn, my_ids, my_pool, rp, base_total)
    for t in top:
        t["drop"] = drop

    # The wire's word on each target, and the reason half of them are here:
    # a free agent whose starter just went on IR is not a value pick, he is a
    # job opening. That is how waiver weeks are actually won, and until the
    # wire existed this board could not see it.
    news = alerts.for_players(conn, [t["id"] for t in top])
    openings = job_openings(conn, rostered)
    for t in top:
        t["news"] = news.get(t["id"])
        t["opening"] = openings.get((t["team"], t["pos"]))

    return {"targets": top, "history_n": len(bids_hist),
            "pool": n, "budget": settings.faab_budget,
            "pricing": ("this league's own bids, indexed continuously by the "
                        "target's value percentile" if bids_hist
                        else "no bid history on the books — score-proportional"),
            "note": "Advisory only — waivers have no actuation path."}


def drop_candidate(conn: sqlite3.Connection, my_ids: list[str],
                   my_pool: list[PlayerProj], rp: list[str],
                   base_total: float) -> dict | None:
    """The cheapest man to cut: whose removal costs the optimal lineup least.

    Ties break toward the man already carrying a season-ending tag, then toward
    the lowest projection. Returns None on a roster with nothing spare — with
    every man starting, the honest answer is that there is no free add.
    """
    if len(my_pool) <= len([s for s in rp if s not in ("BN", "IR", "TAXI")]):
        return None
    best = None
    for p in my_pool:
        rest = [q for q in my_pool if q.player_id != p.player_id]
        cost = round(base_total - optimize(rest, rp).total, 1)
        key = (cost, 0 if (p.injury_status or "") else 1, p.proj)
        if best is None or key < best[0]:
            best = (key, {"id": p.player_id, "name": p.name, "pos": p.pos,
                          "cost": cost, "injury": p.injury_status})
    return best[1] if best else None


# How long a departure keeps the job behind it interesting.
JOB_OPENING_DAYS = 14


def job_openings(conn: sqlite3.Connection,
                 rostered: set[str]) -> dict[tuple[str, str], dict]:
    """{(team, pos): the departure that opened the job}.

    A wire item that puts a ROSTERED man on injured reserve, or releases him,
    leaves work behind at his club and his position. Every free agent sharing
    that (team, position) is a candidate for it. Only departures count — a
    questionable tag is not a job opening, and treating it as one would flag
    half the league every Friday.
    """
    out: dict[tuple[str, str], dict] = {}
    for r in conn.execute(
            "SELECT n.*, p.team, p.pos FROM news n "
            "JOIN players p ON p.sleeper_id = n.player_id "
            "WHERE n.departure=1 AND p.team IS NOT NULL "
            f"AND COALESCE(n.published_at, n.fetched_at) >= datetime('now', '-{JOB_OPENING_DAYS} days') "
            "ORDER BY COALESCE(n.published_at, n.fetched_at) DESC"):
        if r["player_id"] not in rostered:
            continue
        out.setdefault((r["team"], r["pos"]), {
            "name": r["name_raw"], "headline": r["headline"],
            "published_at": r["published_at"],
        })
    return out
