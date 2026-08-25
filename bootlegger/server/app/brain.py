"""Read-side assembly: the draft board and the weekly lineup card, built as
pure queries over the DB so the API, the recs scanner, and hands all see the
same world."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Any

from . import db
from .config import DEMO_ROSTER_POSITIONS, settings
from .demo import DEMO_DRAFT_ID, slot_for_pick
from .engines import draft as draft_engine
from .engines import grades as grades_engine
from .engines import trades as trades_engine
from .engines import waivers as waivers_engine
from .engines.draft import Candidate
from .engines.lineup import INactive, PlayerProj, diff_lineup, optimize

SUGGESTION_COUNT = 5

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
            surv = draft_engine.survival_prob(a["adp"], a["stdev"], my_next)
            row["survival"] = round(surv, 3)
            wait_surv = (draft_engine.survival_prob(a["adp"], a["stdev"], my_after)
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
    suggestions = [
        {**r, "reason": reasons.get(r["id"], "")} for r in available[:SUGGESTION_COUNT]
    ]

    # The experts' best available — shown on The Call when the room disagrees.
    experts_call = None
    ranked_avail = [(ecr_rank[r["id"]], r) for r in available if r["id"] in ecr_rank]
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
         "team": players[p["player_id"]]["team"]}
        for p in my_picks
    ]

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
            # heartbeat: the poller refreshes this every cycle; the frontend
            # banners when it goes stale during a live draft
            "synced_at": drow["updated_at"] if drow else None,
        },
        "players": board_rows,
        "suggestions": suggestions,
        "recent_picks": recent,
        "my_roster": my_roster,
        "roster_positions": rp,
        "experts_call": experts_call,
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
    pool = [
        PlayerProj(pid, players[pid]["pos"], projs.get(pid, 0.0),
                   name=players[pid]["name"],
                   injury_status=players[pid]["injury_status"],
                   on_bye=players[pid]["bye"] == week)
        for pid in ids if pid in players
    ]
    d = diff_lineup(pool, starters, rp)

    def describe(pid: str) -> dict:
        p = players[pid]
        raw = round(projs.get(pid, 0.0), 1)
        zeroed = (p["injury_status"] or "") in INactive or p["bye"] == week
        # proj is what the player counts for (0 when Out/bye) so rows always
        # reconcile with the totals; proj_full keeps the healthy number.
        return {"id": pid, "name": p["name"], "pos": p["pos"], "team": p["team"],
                "proj": 0.0 if zeroed else raw, "proj_full": raw,
                "injury": p["injury_status"], "bye": p["bye"] == week}

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

    return {
        "week": week, "ready": True,
        "owner": roster["owner"],
        "actual": actual_rows, "optimal": optimal_rows, "bench": bench_rows,
        "actual_total": round(d.actual_total, 1),
        "optimal_total": round(d.optimal.total if d.optimal else 0.0, 1),
        "delta": round(d.delta, 1),
        "injury_flag": d.injury_flag,
        "material": d.material,
        "swaps": [
            {**s,
             "out": describe(s["out_id"]), "in": describe(s["in_id"])}
            for s in d.swaps
        ],
        "lineup_hash": lineup_hash(starters),
        "rec": dict(open_rec) if open_rec else None,
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
        parts.append("; ".join(why) + ".")
    parts.append(f"Net {card['delta']:+.1f} projected points.")
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
                           players[pid]["name"], players[pid]["injury_status"])
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


def suggest_trades(conn: sqlite3.Connection, limit: int = 8) -> dict:
    """Scan every opposing roster for deals that help BOTH starting lineups —
    mutual benefit is what actually gets accepted (the trade-finder lesson
    from FantasyPros/Dynasty Daddy). Candidates come from surplus-for-need:
    each side's best bench pieces and weakest starters, in 1-for-1 and
    consolidation (2-for-1) shapes, package-checked with the KTC-school
    curve, then graded by both sides' optimal-lineup deltas."""
    rp = roster_positions(conn)
    players = _players_index(conn)
    cons = {r["player_id"]: r for r in conn.execute("SELECT * FROM consensus WHERE week=0")}
    vals = {r["player_id"]: r for r in conn.execute("SELECT * FROM player_values")}
    my = my_roster_row(conn)
    my_ids = json.loads(my["players_json"]) if my else []
    if not my_ids:
        return {"trades": [], "note": "The parlor opens once rosters exist."}

    vbd_scale = max([(r["vbd"] or 0.0) for r in cons.values()] or [1.0]) or 1.0
    mkt_scale = max([(r["redraft_value"] or 0.0) for r in vals.values()] or [1.0]) or 1.0

    _worth: dict[str, float] = {}

    def worth(pid: str) -> float:
        if pid not in _worth:
            v = (cons[pid]["vbd"] or 0.0) if pid in cons else 0.0
            m = (vals[pid]["redraft_value"] or 0.0) if pid in vals else 0.0
            _worth[pid] = 50.0 * max(0.0, v) / vbd_scale + 50.0 * max(0.0, m) / mkt_scale
        return _worth[pid]

    def projs(ids: list[str]) -> list[PlayerProj]:
        return [PlayerProj(pid, players[pid]["pos"],
                           ((cons[pid]["pts_robust"] or 0.0) if pid in cons else 0.0),
                           players[pid]["name"], players[pid]["injury_status"])
                for pid in ids if pid in players]

    def best(ids: list[str]):
        return optimize(projs(ids), rp)

    def row(pid: str) -> dict:
        return {"player_id": pid, "name": players[pid]["name"], "pos": players[pid]["pos"],
                "vbd": (cons[pid]["vbd"] or 0.0) if pid in cons else 0.0,
                "market_value": (vals[pid]["redraft_value"] or 0.0) if pid in vals else 0.0}

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
                "give": [{"id": p, "name": players[p]["name"], "pos": players[p]["pos"]} for p in give],
                "receive": [{"id": p, "name": players[p]["name"], "pos": players[p]["pos"]} for p in get],
                "my_gain": round(my_gain, 1),
                "their_gain": round(their_gain, 1),
                "vbd_edge": detail["vbd_edge"],
                "market_edge": detail["market_edge"],
                "summary": detail["summary"],
            }

    top = sorted(proposals.values(), key=lambda t: -t["score"])[:limit]
    return {"trades": top,
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
                           players[i]["name"], players[i]["injury_status"])
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
    """Free agents ranked by FA score with tier-bucketed bid sizing and a
    would-he-start lineup signal. `heat` is Sleeper trending-add counts."""
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

    out = []
    for pid, c in cons.items():
        if pid in rostered or pid not in players:
            continue
        p = players[pid]
        score = waivers_engine.fa_score(c["pts_robust"] or 0,
                                        worst_by_pos.get(p["pos"], 0))
        if score <= 0:
            continue
        band = "hot" if score >= 30 else "solid" if score >= 10 else "dart"
        tier_hist = hist_by_tier[band]
        # thin tier history (< 3 bids) falls back to the whole book
        advice = waivers_engine.size_bid(
            score, tier_hist if len(tier_hist) >= 3 else bids_hist,
            settings.faab_budget)
        out.append({
            "id": pid, "name": p["name"], "pos": p["pos"], "team": p["team"],
            "fa_score": round(score, 1), "bid": advice.bid,
            "hard_confirm": advice.hard_confirm, "tier": c["tier"],
            "heat": heat.get(pid, 0),
        })
    out.sort(key=lambda r: -r["fa_score"])
    top = out[:20]

    # "Would he start?" — adding the candidate to my roster and re-optimizing
    # tells whether he cracks the lineup (gain > 0) or is depth.
    if my_ids:
        base = [PlayerProj(pid, players[pid]["pos"],
                           (cons[pid]["pts_robust"] or 0.0) if pid in cons else 0.0,
                           players[pid]["name"], players[pid]["injury_status"])
                for pid in my_ids if pid in players]
        base_total = optimize(base, rp).total
        for t in top:
            cand = PlayerProj(t["id"], t["pos"],
                              (cons[t["id"]]["pts_robust"] or 0.0) if t["id"] in cons else 0.0,
                              t["name"], players[t["id"]]["injury_status"])
            t["lineup_gain"] = round(optimize(base + [cand], rp).total - base_total, 1)
    else:
        for t in top:
            t["lineup_gain"] = None

    return {"targets": top, "history_n": len(bids_hist),
            "note": "Advisory only — waivers have no actuation path."}
