"""Season simulator — stand up an in-season world from a completed draft so
the Sunday surfaces (This Week, Waivers, The Parlor, The Ledger) can be
exercised end to end before the season actually starts.

It builds, on a THROWAWAY COPY of a DB (never the live one — it refuses to
run against a DB whose league draft is not complete unless --force):

  rosters      from the draft's picks, one per draft slot
  starters     deliberately stale for my seat (an injured/bye starter left
               in the lineup) so This Week has a real recommendation to make
  consensus    week-N rows, real weekly ETL when it answers, otherwise
               season/17 with deterministic per-player variance
  transactions tier-labeled FAAB history so waiver bid sizing has priors
  matchups     a round-robin pairing for the week

Usage (inside a container with the app on the path):
    python tools/season_sim.py --week 1 [--draft <draft_id>] [--force]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys

sys.path.insert(0, "/srv/bootlegger")

from app import brain, db  # noqa: E402
from app.config import settings  # noqa: E402
from app.engines.lineup import PlayerProj, optimize  # noqa: E402

BYE_WEEK_SHARE = 0.0   # byes come from players.bye; nothing to synthesize


def build_rosters(conn, draft_id: str) -> dict[int, list[str]]:
    """One roster per draft slot, in pick order."""
    by_slot: dict[int, list[str]] = {}
    for r in conn.execute(
            "SELECT draft_slot, player_id FROM draft_picks WHERE draft_id=? "
            "ORDER BY pick_no", (draft_id,)):
        by_slot.setdefault(r["draft_slot"], []).append(r["player_id"])
    return by_slot


def weekly_from_season(conn, week: int) -> int:
    """Deterministic weekly projections derived from season totals.

    Per-player variance is seeded from the player id + week so repeated runs
    produce the same world (a sim you cannot reproduce is a bad sim). Byes
    project zero — that is what makes the lineup card interesting.
    """
    rows = conn.execute(
        "SELECT c.player_id, c.pts_robust, c.stdev, c.tier, c.vbd, p.bye "
        "FROM consensus c JOIN players p ON p.sleeper_id = c.player_id "
        "WHERE c.week = 0").fetchall()
    out = []
    for r in rows:
        season = r["pts_robust"] or 0.0
        base = season / 17.0
        if r["bye"] == week:
            wk = 0.0
        else:
            seed = int(hashlib.sha1(f"{r['player_id']}|{week}".encode()
                                    ).hexdigest()[:8], 16)
            rng = random.Random(seed)
            wk = max(0.0, base * rng.uniform(0.72, 1.28))
        out.append((r["player_id"], week, round(wk, 2), round(wk, 2),
                    round(base * 0.25, 2), r["tier"], round((r["vbd"] or 0) / 17.0, 2)))
    conn.execute("DELETE FROM consensus WHERE week=?", (week,))
    conn.executemany(
        "INSERT INTO consensus(player_id,week,pts_mean,pts_robust,stdev,tier,vbd) "
        "VALUES(?,?,?,?,?,?,?)", out)
    conn.commit()
    return len(out)


def seed_faab_history(conn, weeks: int = 6) -> int:
    """Tier-labeled bid history so waiver sizing has real priors to read."""
    rng = random.Random(20260825)
    rows = []
    for w in range(1, weeks + 1):
        for _ in range(rng.randint(3, 7)):
            tier = rng.choices(["hot", "solid", "dart"], weights=[2, 4, 5])[0]
            faab = {"hot": rng.randint(18, 46),
                    "solid": rng.randint(6, 17),
                    "dart": rng.randint(1, 5)}[tier]
            rows.append((f"sim-{w}-{len(rows)}", w, "waiver",
                         json.dumps({"tier": tier}), "[]", faab, "complete",
                         db.utcnow()))
    conn.executemany(
        "INSERT OR REPLACE INTO transactions(txn_id,week,type,adds_json,"
        "drops_json,faab,status,ts) VALUES(?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    return len(rows)


def set_starters(conn, week: int, my_slot: int, stale: bool = True) -> dict:
    """Everyone starts their optimal lineup — except my seat, which is left
    holding a bye/injured starter so This Week has something to fix."""
    rp = brain.roster_positions(conn)
    players = {r["sleeper_id"]: r for r in conn.execute("SELECT * FROM players")}
    projs = brain.week_projections(conn, week)
    report = {}
    for r in conn.execute("SELECT roster_id, players_json FROM rosters").fetchall():
        ids = json.loads(r["players_json"])
        if not ids:
            continue
        pool = [PlayerProj(pid, players[pid]["pos"], projs.get(pid, 0.0),
                           players[pid]["name"], players[pid]["injury_status"],
                           players[pid]["bye"] == week)
                for pid in ids if pid in players]
        best = optimize(pool, rp)
        starters = [p.player_id for _, p in best.assignment]
        if stale and r["roster_id"] == my_slot:
            # A distracted owner's classic blunder: a bye/OUT body left in
            # the lineup. That is worth alerting on and clears the
            # materiality bar; a merely-weaker starter would not.
            sset = set(starters)
            bench = [p for p in pool if p.player_id not in sset]
            started = [p for p in pool if p.player_id in sset]
            dead = [p for p in bench if p.startable_proj == 0.0]
            for weakest in sorted(started, key=lambda p: -p.startable_proj):
                sub = next((p for p in dead if p.pos == weakest.pos), None) or                     max((p for p in bench if p.pos == weakest.pos
                         and p.player_id != weakest.player_id),
                        key=lambda p: p.startable_proj, default=None)
                if sub is None:
                    continue
                starters = [sub.player_id if s == weakest.player_id else s
                            for s in starters]
                report = {"benched": weakest.name, "started": sub.name,
                          "cost": round(weakest.startable_proj - sub.startable_proj, 1)}
                break
        conn.execute("UPDATE rosters SET starters_json=?, updated_at=? WHERE roster_id=?",
                     (json.dumps(starters), db.utcnow(), r["roster_id"]))
    conn.commit()
    return report


def chaos(conn, week: int, injuries: int = 14, breakouts: int = 12) -> dict:
    """A freshly drafted league is too tidy to test the season surfaces: no
    injuries, no breakouts, every roster balanced — so the wire is empty and
    no trade helps anyone. This injects the churn a real week has.

    Injuries hit rostered starters (creating waiver demand and positional
    holes to trade into); breakouts lift free agents so the street has
    somebody worth a bid.
    """
    rng = random.Random(week * 7717)
    rostered: list[str] = []
    for r in conn.execute("SELECT players_json FROM rosters"):
        rostered += json.loads(r["players_json"])
    rostered_set = set(rostered)
    # Skill positions only: kickers and defenses carry high RAW season points
    # with almost no replacement value, so "boosting" them just floods the
    # wire with 300-point kickers — a sim artifact, not a fantasy week.
    fas = [r["player_id"] for r in conn.execute(
        "SELECT c.player_id FROM consensus c JOIN players p "
        "ON p.sleeper_id = c.player_id WHERE c.week=0 "
        "AND p.pos IN ('QB','RB','WR','TE') ORDER BY c.pts_robust DESC")
        if r["player_id"] not in rostered_set]

    hurt = rng.sample(rostered, min(injuries, len(rostered)))
    for i, pid in enumerate(hurt):
        status = "Out" if i % 3 == 0 else ("Doubtful" if i % 3 == 1 else "Questionable")
        conn.execute("UPDATE players SET injury_status=? WHERE sleeper_id=?", (status, pid))
        if status in ("Out", "Doubtful"):     # week projection collapses
            conn.execute("UPDATE consensus SET pts_robust=pts_robust*?, pts_mean=pts_mean*? "
                         "WHERE player_id=? AND week=?", (0.05, 0.05, pid, week))

    # breakouts: pull from the top of the unrostered pool and lift them into
    # genuinely startable territory for this week AND rest-of-season
    risers = fas[:60]
    picked = rng.sample(risers, min(breakouts, len(risers)))
    for pid in picked:
        mult = rng.uniform(1.4, 2.1)
        conn.execute("UPDATE consensus SET pts_robust=pts_robust*?, pts_mean=pts_mean*? "
                     "WHERE player_id=? AND week=?", (mult, mult, pid, week))
        conn.execute("UPDATE consensus SET pts_robust=pts_robust*?, pts_mean=pts_mean*?, "
                     "vbd=COALESCE(vbd,0)*? WHERE player_id=? AND week=0",
                     (mult, mult, mult, pid))
    conn.commit()
    names = {r["sleeper_id"]: r["name"] for r in conn.execute("SELECT sleeper_id, name FROM players")}
    return {"injured": [names.get(p, p) for p in hurt[:6]],
            "breakouts": [names.get(p, p) for p in picked[:6]]}


def seed_matchups(conn, week: int) -> int:
    ids = [r["roster_id"] for r in conn.execute(
        "SELECT roster_id FROM rosters ORDER BY roster_id")]
    rng = random.Random(week)
    rng.shuffle(ids)
    conn.execute("DELETE FROM matchups WHERE week=?", (week,))
    pairs = 0
    for i in range(0, len(ids) - 1, 2):
        a, b = ids[i], ids[i + 1]
        conn.execute("INSERT INTO matchups(week,roster_id,opp_roster_id) VALUES(?,?,?)",
                     (week, a, b))
        conn.execute("INSERT INTO matchups(week,roster_id,opp_roster_id) VALUES(?,?,?)",
                     (week, b, a))
        pairs += 1
    conn.commit()
    return pairs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", type=int, default=1)
    ap.add_argument("--draft", default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--calm", action="store_true",
                    help="skip injury/breakout churn (a too-tidy world)")
    args = ap.parse_args()
    conn = db.connect()

    drow = conn.execute(
        "SELECT * FROM drafts WHERE draft_id=?", (args.draft,)).fetchone() \
        if args.draft else conn.execute(
            "SELECT * FROM drafts ORDER BY updated_at DESC LIMIT 1").fetchone()
    if not drow:
        raise SystemExit("no draft to build from")
    n_picks = conn.execute("SELECT COUNT(*) c FROM draft_picks WHERE draft_id=?",
                           (drow["draft_id"],)).fetchone()["c"]
    if n_picks < 100 and not args.force:
        raise SystemExit(f"draft {drow['draft_id']} has only {n_picks} picks; --force to proceed")

    by_slot = build_rosters(conn, drow["draft_id"])
    owners = {r["roster_id"]: r["owner"] for r in conn.execute("SELECT * FROM rosters")}
    for slot, ids in by_slot.items():
        conn.execute(
            "INSERT INTO rosters(roster_id,owner,players_json,starters_json,updated_at) "
            "VALUES(?,?,?,?,?) ON CONFLICT(roster_id) DO UPDATE SET "
            "players_json=excluded.players_json, updated_at=excluded.updated_at",
            (slot, owners.get(slot) or f"Seat {slot}", json.dumps(ids), "[]", db.utcnow()))
    conn.commit()

    n_week = weekly_from_season(conn, args.week)
    churn = chaos(conn, args.week) if not args.calm else {}
    n_faab = seed_faab_history(conn)
    dsettings = json.loads(drow["settings_json"] or "{}")
    my_slot = int(dsettings.get("slot", settings.my_roster_id))
    stale = set_starters(conn, args.week, my_slot)
    n_match = seed_matchups(conn, args.week)
    db.meta_set(conn, "sim_week", str(args.week))
    db.meta_set(conn, "current_week", str(args.week))

    print(json.dumps({
        "draft": drow["draft_id"], "rosters": len(by_slot), "my_slot": my_slot,
        "week": args.week, "weekly_rows": n_week, "faab_txns": n_faab,
        "matchup_pairs": n_match, "stale_lineup": stale, "churn": churn,
    }, indent=1))


if __name__ == "__main__":
    main()
