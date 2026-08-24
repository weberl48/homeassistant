"""Read-side assembly: the draft board and the weekly lineup card, built as
pure queries over the DB so the API, the recs scanner, and hands all see the
same world."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from . import db
from .config import DEMO_ROSTER_POSITIONS, settings
from .demo import DEMO_DRAFT_ID, slot_for_pick
from .engines import draft as draft_engine
from .engines.draft import Candidate
from .engines.lineup import INactive, PlayerProj, diff_lineup

SUGGESTION_COUNT = 5


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
    drow = conn.execute("SELECT * FROM drafts WHERE draft_id=?", (DEMO_DRAFT_ID,)).fetchone() \
        if settings.mode == "demo" else conn.execute("SELECT * FROM drafts LIMIT 1").fetchone()
    dsettings = json.loads(drow["settings_json"]) if drow and drow["settings_json"] else {}
    teams = int(dsettings.get("teams", settings.teams))
    rounds = int(dsettings.get("rounds", settings.rounds))
    my_slot = int(dsettings.get("slot", settings.my_roster_id))
    draft_id = drow["draft_id"] if drow else None
    status = drow["status"] if drow else "pre_draft"

    picks = conn.execute(
        "SELECT * FROM draft_picks WHERE draft_id=? ORDER BY pick_no", (draft_id,)
    ).fetchall() if draft_id else []
    picked = {p["player_id"]: p for p in picks}
    current_pick = len(picks) + 1
    total_picks = teams * rounds
    if status == "complete" or current_pick > total_picks:
        status = "complete"
        on_clock_slot, my_next = None, None
    else:
        on_clock_slot = slot_for_pick(current_pick)
        my_next = draft_engine.next_pick_after(current_pick - 1, my_slot, teams, rounds)

    players = _players_index(conn)
    cons = {r["player_id"]: r for r in conn.execute("SELECT * FROM consensus WHERE week=0")}
    adp = {r["player_id"]: r for r in conn.execute("SELECT * FROM adp")}

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
            pools.setdefault(p["pos"], []).append(
                Candidate(pid, p["pos"], c["vbd"] or 0.0, surv))
        board_rows.append(row)

    # Suggestion scores for available players.
    scores: dict[str, float] = {}
    reasons: dict[str, str] = {}
    for pos, pool in pools.items():
        mult = draft_engine.roster_need_multiplier(pos, my_counts, rp)
        for cand in pool:
            s = draft_engine.suggestion_score(cand, pool, mult)
            scores[cand.player_id] = s
            e_next = cand.vbd - (s / mult if mult else 0)
            reasons[cand.player_id] = _reason(cand, e_next, mult)
    for row in board_rows:
        if row["id"] in scores:
            row["score"] = round(scores[row["id"]], 1)

    available = [r for r in board_rows if "pick_no" not in r]
    available.sort(key=lambda r: -(r.get("score") if r.get("score") is not None else -999))
    suggestions = [
        {**r, "reason": reasons.get(r["id"], "")} for r in available[:SUGGESTION_COUNT]
    ]

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
            "id": draft_id, "status": status, "teams": teams, "rounds": rounds,
            "current_pick": min(current_pick, total_picks),
            "total_picks": total_picks,
            "round": min((current_pick - 1) // teams + 1, rounds),
            "on_clock_slot": on_clock_slot,
            "on_the_clock_me": on_clock_slot == my_slot,
            "my_slot": my_slot, "my_next_pick": my_next,
        },
        "players": board_rows,
        "suggestions": suggestions,
        "recent_picks": recent,
        "my_roster": my_roster,
    }


def _reason(cand: Candidate, e_next: float, mult: float) -> str:
    gap = cand.vbd - e_next
    surv = f"{cand.survival:.0%} odds he survives to your next pick"
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
        return {"week": week, "ready": False}
    players = _players_index(conn)
    projs = week_projections(conn, week)
    ids = json.loads(roster["players_json"])
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
