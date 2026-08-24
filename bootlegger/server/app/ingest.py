"""ETL: builds consensus/tiers/VBD in the DB, and (live mode) pulls Sleeper,
FFC ADP, and FantasyCalc into the tables. Demo mode seeds the same tables from
the fixture instead — every engine downstream is source-agnostic."""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from collections import defaultdict

import httpx

from . import db
from .config import settings
from .engines import consensus as cx
from .engines import tiers as tiers_engine
from .engines import vbd as vbd_engine
from .sleeper import SleeperClient
from .sources import fetch_fantasycalc_values, fetch_ffc_adp, normalize_name

# Tiering pools per position: deep enough to cover draftable players, shallow
# enough that the GMM sees structure instead of a waiver-wire tail.
TIER_POOLS = {"QB": 32, "RB": 64, "WR": 72, "TE": 28, "K": 16, "DEF": 16}


def compute_consensus(conn: sqlite3.Connection, week: int = 0) -> int:
    """Robust-average the per-source projections for `week`, then attach GMM
    tiers and VOLS VBD. Returns the number of players written."""
    rows = conn.execute(
        "SELECT p.player_id, p.pts, pl.pos FROM projections p "
        "JOIN players pl ON pl.sleeper_id = p.player_id WHERE p.week=?",
        (week,),
    ).fetchall()
    by_player: dict[str, list[float]] = defaultdict(list)
    pos_of: dict[str, str] = {}
    for r in rows:
        by_player[r["player_id"]].append(r["pts"])
        pos_of[r["player_id"]] = r["pos"]

    robust: dict[str, float] = {}
    stats: dict[str, tuple[float, float | None]] = {}
    for pid, vals in by_player.items():
        rb = cx.robust_mean(vals)
        robust[pid] = rb
        stats[pid] = (sum(vals) / len(vals), cx.source_spread(vals))

    by_pos: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for pid, pts in robust.items():
        by_pos[pos_of[pid]].append((pid, pts))
    vbd_map = vbd_engine.compute_vbd(by_pos)

    tier_map: dict[str, int] = {}
    for pos, players in by_pos.items():
        ranked = sorted(players, key=lambda t: -t[1])
        pool = ranked[: TIER_POOLS.get(pos, len(ranked))]
        tiers = tiers_engine.fit_tiers([pts for _, pts in pool])
        for (pid, _), tier in zip(pool, tiers):
            tier_map[pid] = tier

    conn.execute("DELETE FROM consensus WHERE week=?", (week,))
    conn.executemany(
        "INSERT INTO consensus(player_id,week,pts_mean,pts_robust,stdev,tier,vbd) "
        "VALUES(?,?,?,?,?,?,?)",
        [
            (pid, week, stats[pid][0], robust[pid], stats[pid][1],
             tier_map.get(pid), vbd_map.get(pid))
            for pid in robust
        ],
    )
    conn.commit()
    return len(robust)


# ---------------------------------------------------------------------------
# Live-mode ETL (unused in demo; each step degrades independently and loudly)
# ---------------------------------------------------------------------------

def etl_players(client: SleeperClient, conn: sqlite3.Connection) -> int:
    now = db.utcnow()
    rows = []
    for pid, p in client.relevant_players().items():
        rows.append({
            "sleeper_id": pid,
            "name": p.get("full_name") or f"{p.get('first_name','')} {p.get('last_name','')}".strip() or pid,
            "pos": p.get("position"),
            "team": p.get("team"),
            "bye": None,  # byes come from the schedule; nflreadpy lands in Phase 2
            "status": p.get("status") or "Active",
            "injury_status": p.get("injury_status"),
            "updated_at": now,
        })
    db.upsert_players(conn, rows)
    return len(rows)


def etl_league(client: SleeperClient, conn: sqlite3.Connection) -> None:
    lg = client.league(settings.league_id)
    conn.execute(
        "INSERT INTO league(league_id,settings_json,scoring_json) VALUES(?,?,?) "
        "ON CONFLICT(league_id) DO UPDATE SET settings_json=excluded.settings_json,"
        "scoring_json=excluded.scoring_json",
        (settings.league_id,
         json.dumps({k: lg.get(k) for k in ("name", "roster_positions", "settings", "previous_league_id")}),
         json.dumps(lg.get("scoring_settings", {}))),
    )
    conn.commit()


def etl_rosters(client: SleeperClient, conn: sqlite3.Connection) -> None:
    now = db.utcnow()
    users = {u["user_id"]: u.get("display_name", u["user_id"])
             for u in client.users(settings.league_id)}
    for r in client.rosters(settings.league_id):
        conn.execute(
            "INSERT INTO rosters(roster_id,owner,players_json,starters_json,updated_at) "
            "VALUES(?,?,?,?,?) ON CONFLICT(roster_id) DO UPDATE SET owner=excluded.owner,"
            "players_json=excluded.players_json,starters_json=excluded.starters_json,"
            "updated_at=excluded.updated_at",
            (r["roster_id"], users.get(r.get("owner_id"), r.get("owner_id")),
             json.dumps(r.get("players") or []), json.dumps(r.get("starters") or []), now),
        )
    conn.commit()


def etl_adp(conn: sqlite3.Connection) -> int:
    """FFC ADP joined to sleeper ids by normalized name+position."""
    now = db.utcnow()
    ffc = fetch_ffc_adp(teams=settings.teams, year=settings.season)
    lookup = {}
    for row in conn.execute("SELECT sleeper_id, name, pos FROM players").fetchall():
        lookup[(normalize_name(row["name"]), row["pos"])] = row["sleeper_id"]
    n = 0
    for p in ffc:
        pos = "DEF" if p["position"] in ("DST", "DEF", "PK") and p["position"] == "DST" else p["position"]
        pid = lookup.get((normalize_name(p["name"]), pos))
        if not pid:
            continue
        conn.execute(
            "INSERT INTO adp(player_id,source,adp,stdev,updated_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(player_id,source) DO UPDATE SET adp=excluded.adp,"
            "stdev=excluded.stdev,updated_at=excluded.updated_at",
            (pid, "ffc", p["adp"], p["stdev"], now),
        )
        n += 1
    conn.commit()
    return n


def etl_values(conn: sqlite3.Connection) -> int:
    now = db.utcnow()
    n = 0
    for v in fetch_fantasycalc_values():
        conn.execute(
            "INSERT INTO player_values(player_id,redraft_value,trend_30d,updated_at) "
            "VALUES(?,?,?,?) ON CONFLICT(player_id) DO UPDATE SET "
            "redraft_value=excluded.redraft_value,trend_30d=excluded.trend_30d,"
            "updated_at=excluded.updated_at",
            (v["sleeper_id"], v["redraft_value"], v["trend_30d"], now),
        )
        n += 1
    conn.commit()
    return n


def etl_draft_picks(client: SleeperClient, conn: sqlite3.Connection, draft_id: str) -> int:
    d = client.draft(draft_id)
    conn.execute(
        "INSERT INTO drafts(draft_id,status,settings_json,updated_at) VALUES(?,?,?,?) "
        "ON CONFLICT(draft_id) DO UPDATE SET status=excluded.status,"
        "settings_json=excluded.settings_json,updated_at=excluded.updated_at",
        (draft_id, d.get("status"), json.dumps(d.get("settings", {})), db.utcnow()),
    )
    picks = client.draft_picks(draft_id)
    for p in picks:
        conn.execute(
            "INSERT OR REPLACE INTO draft_picks(draft_id,pick_no,round,draft_slot,roster_id,player_id,ts) "
            "VALUES(?,?,?,?,?,?,?)",
            (draft_id, p["pick_no"], p.get("round"), p.get("draft_slot"),
             p.get("roster_id"), p.get("player_id"), db.utcnow()),
        )
    conn.commit()
    return len(picks)


def ping_healthchecks(ok: bool = True) -> None:
    """Dead-man switch: every scheduled job pings healthchecks.io (design doc §6)."""
    if not settings.healthchecks_url:
        return
    url = settings.healthchecks_url + ("" if ok else "/fail")
    try:
        httpx.get(url, timeout=10)
    except httpx.HTTPError:
        pass  # the ping's absence *is* the alert


def nightly(conn: sqlite3.Connection) -> dict:
    """The nightly ETL bundle for live mode."""
    client = SleeperClient()
    out = {"players": etl_players(client, conn)}
    if settings.league_id:
        etl_league(client, conn)
        etl_rosters(client, conn)
    out["adp"] = etl_adp(conn)
    out["values"] = etl_values(conn)
    out["consensus"] = compute_consensus(conn, week=0)
    ping_healthchecks(ok=True)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootlegger ETL")
    parser.add_argument("job", choices=["nightly", "draft-poll"], nargs="?", default="nightly")
    args = parser.parse_args()
    conn = db.connect()
    db.init_db(conn)
    if args.job == "nightly":
        print(json.dumps(nightly(conn)))
    elif args.job == "draft-poll":
        client = SleeperClient()
        draft_id = settings.draft_id
        if not draft_id and settings.league_id:
            drafts = client.league_drafts(settings.league_id)
            draft_id = drafts[0]["draft_id"] if drafts else ""
        if not draft_id:
            raise SystemExit("no draft id; set SLEEPER_DRAFT_ID or SLEEPER_LEAGUE_ID")
        while True:
            n = etl_draft_picks(client, conn, draft_id)
            print(f"picks={n}", flush=True)
            time.sleep(settings.draft_poll_seconds)


if __name__ == "__main__":
    main()
