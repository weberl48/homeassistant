"""Head-to-head mock: Bootlegger's Call vs FantasyPros ECR best-available.

Runs simulated 12-team, 15-round snake drafts on a THROWAWAY COPY of the live
DB. The Bootlegger seat picks brain.get_board()'s top suggestion each turn —
the real production path (horizon regret, ECR blend, need multipliers). The
FP seat drafts best available by FantasyPros expert-consensus rank (what
Draft Wizard recommends), with the same light positional sanity caps the
ADP-driven opponents get. Slot pairs are mirrored across sims so neither
advisor banks draft-position luck.

Grading: each final roster's optimal starting lineup under three judges —
our 3-source consensus, FantasyPros' own projections, ESPN's projections.

Usage (inside the container): python tools/h2h_mock.py
"""
from __future__ import annotations

import json
import random
import shutil
import sqlite3
import sys

sys.path.insert(0, "/src")
from app import brain, db  # noqa: E402
from app.engines.lineup import PlayerProj, optimize  # noqa: E402

TEAMS, ROUNDS = 12, 15
SIM_PAIRS = [(2, 11), (11, 2), (4, 9), (9, 4), (6, 7), (7, 6), (1, 12), (12, 1)]
SRC_DB = "/data/bootlegger.db"
WORK_DB = "/tmp/h2h.db"


def snake_slot(pick_no: int) -> int:
    rnd = (pick_no - 1) // TEAMS
    idx = (pick_no - 1) % TEAMS
    return idx + 1 if rnd % 2 == 0 else TEAMS - idx


def load_world(conn: sqlite3.Connection):
    players = {r["sleeper_id"]: dict(r) for r in conn.execute("SELECT * FROM players")}
    cons = {r["player_id"]: dict(r) for r in conn.execute("SELECT * FROM consensus WHERE week=0")}
    projs = {}
    for r in conn.execute("SELECT player_id, source, pts FROM projections WHERE week=0"):
        projs.setdefault(r["source"], {})[r["player_id"]] = r["pts"]
    adp_rows = {}
    ecr = {}
    for r in conn.execute("SELECT * FROM adp"):
        pref = {"sleeper": 0, "ffc": 1, "fp_ecr": 2}.get(r["source"], 9)
        cur = adp_rows.get(r["player_id"])
        if cur is None or pref < cur[0]:
            adp_rows[r["player_id"]] = (pref, r["adp"], r["stdev"])
        if r["source"] == "fp_ecr":
            ecr[r["player_id"]] = r["adp"]
    adp = {pid: (v[1], v[2]) for pid, v in adp_rows.items()}
    return players, cons, projs, adp, ecr


def pos_of(players, pid):
    return players[pid]["pos"]


def sanity_ok(players, roster: list[str], pid: str, rnd: int) -> bool:
    """Light realism caps shared by opponents and the FP seat."""
    pos = pos_of(players, pid)
    n = sum(1 for p in roster if pos_of(players, p) == pos)
    if pos in ("K", "DEF"):
        # real mock data: first DST went R8, first K R9 — rooms do not wait
        return rnd >= 8 and n < 1
    if pos in ("QB", "TE") and n >= 2:
        return False
    if pos in ("RB", "WR") and n >= 7:
        return False
    if rnd >= 14 and pos not in ("K", "DEF"):
        # last two rounds must fill K/DEF if still missing
        missing = [p for p in ("K", "DEF")
                   if not any(pos_of(players, x) == p for x in roster)]
        if len(missing) >= 16 - rnd:
            return False
    return True


def open_needs(players, roster: list[str], rp) -> tuple[set[str], bool]:
    """Positions with an unfilled dedicated starter slot, and whether a FLEX
    share is still open — the roster awareness Draft Wizard layers over ECR."""
    dedicated = {p: rp.count(p) for p in ("QB", "RB", "WR", "TE", "K", "DEF")}
    flex_slots = sum(1 for s in rp if s in ("FLEX", "SUPER_FLEX", "SUPERFLEX",
                                            "WRRB_FLEX", "REC_FLEX"))
    have = {}
    for p in roster:
        have[pos_of(players, p)] = have.get(pos_of(players, p), 0) + 1
    open_pos = {p for p, want in dedicated.items() if want and have.get(p, 0) < want}
    flex_used = sum(max(0, have.get(p, 0) - dedicated.get(p, 0))
                    for p in ("RB", "WR", "TE"))
    return open_pos, flex_used < flex_slots


def opponent_pick(rng, players, adp, taken, roster, rnd) -> str:
    cands = []
    for pid, (mean, std) in adp.items():
        if pid in taken or pid not in players:
            continue
        sigma = std if std else max(2.0, 0.15 * mean)
        cands.append((mean + rng.gauss(0, sigma), pid))
    cands.sort()
    for _, pid in cands:
        if sanity_ok(players, roster, pid, rnd):
            return pid
    return cands[0][1]


def fp_pick(players, ecr, taken, roster, rnd, rp) -> str:
    """Need-weighted ECR best-available — the Draft Wizard behavior: fill open
    starting slots by expert rank first, then best available."""
    ranked = sorted(((r, pid) for pid, r in ecr.items()
                     if pid not in taken and pid in players))
    open_pos, flex_open = open_needs(players, roster, rp)
    starters_open = bool(open_pos) or flex_open
    if starters_open:
        for _, pid in ranked:
            pos = pos_of(players, pid)
            if not sanity_ok(players, roster, pid, rnd):
                continue
            if pos in open_pos or (flex_open and pos in ("RB", "WR", "TE")):
                return pid
    for _, pid in ranked:
        if sanity_ok(players, roster, pid, rnd):
            return pid
    return ranked[0][1]


def grade(players, roster, pts_map, rp) -> float:
    pool = [PlayerProj(pid, players[pid]["pos"], pts_map.get(pid, 0.0) or 0.0,
                       players[pid]["name"]) for pid in roster if pid in players]
    return round(optimize(pool, rp).total, 1)


def run_sim(sim_no: int, bl_slot: int, fp_slot: int, conn, world, rp) -> dict:
    players, cons, projs, adp, ecr = world
    rng = random.Random(1000 + sim_no)
    draft_id = f"h2h-{sim_no}"
    conn.execute("DELETE FROM draft_picks WHERE draft_id LIKE 'h2h-%'")
    conn.execute("DELETE FROM drafts WHERE draft_id LIKE 'h2h-%'")
    conn.execute(
        "INSERT INTO drafts(draft_id,status,settings_json,updated_at) VALUES(?,?,?,?)",
        (draft_id, "drafting",
         json.dumps({"teams": TEAMS, "rounds": ROUNDS, "slot": bl_slot}),
         db.utcnow()))
    conn.commit()

    taken: set[str] = set()
    rosters: dict[int, list[str]] = {s: [] for s in range(1, TEAMS + 1)}
    divergences = []

    for pick_no in range(1, TEAMS * ROUNDS + 1):
        slot = snake_slot(pick_no)
        rnd = (pick_no - 1) // TEAMS + 1
        if slot == bl_slot:
            board = brain.get_board(conn)
            sugg = [s for s in board["suggestions"] if s["id"] not in taken]
            pid = sugg[0]["id"] if sugg else opponent_pick(rng, players, adp, taken, rosters[slot], rnd)
            if rnd <= 8 and ecr:
                fp_would = fp_pick(players, ecr, taken, rosters[slot], rnd, rp)
                if fp_would != pid:
                    divergences.append({"round": rnd, "bl": players[pid]["name"],
                                        "fp_would": players[fp_would]["name"]})
        elif slot == fp_slot:
            pid = fp_pick(players, ecr, taken, rosters[slot], rnd, rp)
        else:
            pid = opponent_pick(rng, players, adp, taken, rosters[slot], rnd)
        taken.add(pid)
        rosters[slot].append(pid)
        conn.execute(
            "INSERT INTO draft_picks(draft_id,pick_no,round,draft_slot,roster_id,player_id,ts) "
            "VALUES(?,?,?,?,?,?,?)",
            (draft_id, pick_no, rnd, slot, slot, pid, db.utcnow()))
        conn.execute("UPDATE drafts SET updated_at=? WHERE draft_id=?",
                     (db.utcnow(), draft_id))
        if pick_no % TEAMS == 0:
            conn.commit()
    conn.commit()

    out = {"sim": sim_no, "bl_slot": bl_slot, "fp_slot": fp_slot,
           "divergences": divergences[:6]}
    for name, pts_map in [("consensus", {p: c["pts_robust"] for p, c in cons.items()}),
                          ("fp_judge", projs.get("fantasypros", {})),
                          ("espn_judge", projs.get("espn", {}))]:
        out[f"bl_{name}"] = grade(players, rosters[bl_slot], pts_map, rp)
        out[f"fp_{name}"] = grade(players, rosters[fp_slot], pts_map, rp)
    out["bl_roster"] = [players[p]["name"] for p in rosters[bl_slot]]
    out["fp_roster"] = [players[p]["name"] for p in rosters[fp_slot]]
    return out


def main():
    shutil.copy(SRC_DB, WORK_DB)
    conn = sqlite3.connect(WORK_DB)
    conn.row_factory = sqlite3.Row
    import app.db as adb
    adb.connect = lambda: conn  # brain uses passed conn; keep helpers consistent
    world = load_world(conn)
    rp = brain.roster_positions(conn)
    results = []
    for i, (bl, fp) in enumerate(SIM_PAIRS):
        r = run_sim(i, bl, fp, conn, world, rp)
        results.append(r)
        print(f"sim {i}: BL(slot {bl}) cons {r['bl_consensus']} fpj {r['bl_fp_judge']} "
              f"| FP(slot {fp}) cons {r['fp_consensus']} fpj {r['fp_fp_judge']}", flush=True)
    agg = {}
    for k in ("consensus", "fp_judge", "espn_judge"):
        agg[k] = {
            "bootlegger": round(sum(r[f"bl_{k}"] for r in results) / len(results), 1),
            "fantasypros": round(sum(r[f"fp_{k}"] for r in results) / len(results), 1),
        }
    print("=AGGREGATE=")
    print(json.dumps(agg, indent=1))
    print("=DETAIL=")
    print(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
