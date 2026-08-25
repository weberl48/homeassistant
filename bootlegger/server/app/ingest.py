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
from .sources import (fetch_cbs_projections, fetch_espn_projections,
                      fetch_fantasycalc_values, fetch_fantasypros_projections,
                      fetch_ffc_adp, fetch_fftoday_projections, fetch_fp_ecr,
                      normalize_name)

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
    if ffc:
        conn.execute("DELETE FROM adp WHERE source='ffc'")
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


def _league_scoring(conn: sqlite3.Connection) -> str:
    """'ppr' | 'half' | 'std', from the league's rec scoring (default ppr)."""
    row = conn.execute("SELECT scoring_json FROM league").fetchone()
    if row and row["scoring_json"]:
        rec = (json.loads(row["scoring_json"]) or {}).get("rec")
        if rec == 0.5:
            return "half"
        if rec == 0:
            return "std"
    return "ppr"


def etl_projections(client: SleeperClient, conn: sqlite3.Connection, week: int = 0) -> int:
    """Sleeper public projections -> projections table, points field per league
    scoring. Week 0 also captures Sleeper's own ADP from the same blob — the
    league drafts ON Sleeper, so platform ADP is the best predictor of how this
    room actually picks (999/1000 are Sleeper's no-data placeholders)."""
    scoring = _league_scoring(conn)
    pts_field = {"ppr": "pts_ppr", "half": "pts_half_ppr", "std": "pts_std"}[scoring]
    adp_field = {"ppr": "adp_ppr", "half": "adp_half_ppr", "std": "adp_std"}[scoring]
    have = {r["sleeper_id"] for r in conn.execute("SELECT sleeper_id FROM players")}
    blob = client.projections(settings.season, week)
    # Replace, don't accrete: a player dropped from the feed must not keep his
    # last projection forever (season-ending injuries would stay draftable).
    if blob:
        conn.execute("DELETE FROM projections WHERE week=? AND source='sleeper'", (week,))
        if week == 0:
            conn.execute("DELETE FROM adp WHERE source='sleeper'")
    n = 0
    now = db.utcnow()
    for pid, v in blob.items():
        if pid not in have:
            continue
        pts = v.get(pts_field) or v.get("pts_ppr")
        if pts:
            conn.execute(
                "INSERT OR REPLACE INTO projections(player_id,week,source,pts,floor,ceiling) "
                "VALUES(?,?,?,?,?,?)",
                (pid, week, "sleeper", float(pts), None, None),
            )
            n += 1
        if week == 0:
            adp = v.get(adp_field) or v.get("adp_ppr")
            if adp and float(adp) < 900:
                conn.execute(
                    "INSERT INTO adp(player_id,source,adp,stdev,updated_at) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(player_id,source) DO UPDATE SET adp=excluded.adp,"
                    "stdev=excluded.stdev,updated_at=excluded.updated_at",
                    (pid, "sleeper", float(adp), None, now),
                )
    conn.commit()
    return n


def etl_fp_ecr(conn: sqlite3.Connection) -> dict:
    """FantasyPros expert-consensus ranks -> adp table (source fp_ecr), and bye
    weeks backfilled onto players (the Sleeper feed leaves byes null)."""
    data = fetch_fp_ecr(scoring=_league_scoring(conn))
    lookup = {}
    for row in conn.execute("SELECT sleeper_id, name, pos FROM players").fetchall():
        lookup[(normalize_name(row["name"]), row["pos"])] = row["sleeper_id"]
    now = db.utcnow()
    if data["players"]:  # only clear when the scrape actually delivered
        conn.execute("DELETE FROM adp WHERE source='fp_ecr'")
    matched = byes = 0
    for p in data["players"]:
        pid = lookup.get((normalize_name(p["name"]), p["position"]))
        if not pid:
            continue
        conn.execute(
            "INSERT INTO adp(player_id,source,adp,stdev,updated_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(player_id,source) DO UPDATE SET adp=excluded.adp,"
            "stdev=excluded.stdev,updated_at=excluded.updated_at",
            (pid, "fp_ecr", p["rank_ave"], p["rank_std"], now),
        )
        matched += 1
        if p["bye"]:
            conn.execute("UPDATE players SET bye=? WHERE sleeper_id=?", (p["bye"], pid))
            byes += 1
    conn.commit()
    return {"experts": data["experts"], "matched": matched, "byes": byes}


def etl_espn_projections(conn: sqlite3.Connection, week: int = 0) -> int:
    """ESPN projections -> projections table (source espn). ESPN's keyless
    default league is PPR-scored, so only run for PPR leagues. DST joins by
    team nickname (ESPN "Ravens D/ST" vs Sleeper "Baltimore Ravens")."""
    if _league_scoring(conn) != "ppr":
        return 0
    lookup = {}
    def_by_nickname = {}
    for row in conn.execute("SELECT sleeper_id, name, pos FROM players").fetchall():
        lookup[(normalize_name(row["name"]), row["pos"])] = row["sleeper_id"]
        if row["pos"] == "DEF" and row["name"]:
            def_by_nickname[row["name"].split()[-1].lower()] = row["sleeper_id"]
    rows = fetch_espn_projections(settings.season, week)
    if rows:
        conn.execute("DELETE FROM projections WHERE week=? AND source='espn'", (week,))
    n = 0
    for p in rows:
        if p["position"] == "DEF":
            nick = p["name"].split(" D/ST")[0].split()[-1].lower()
            pid = def_by_nickname.get(nick)
        else:
            pid = lookup.get((normalize_name(p["name"]), p["position"]))
        if not pid:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO projections(player_id,week,source,pts,floor,ceiling) "
            "VALUES(?,?,?,?,?,?)",
            (pid, week, "espn", p["pts"], None, None),
        )
        n += 1
    conn.commit()
    return n


def _name_lookup(conn: sqlite3.Connection) -> dict:
    return {(normalize_name(r["name"]), r["pos"]): r["sleeper_id"]
            for r in conn.execute("SELECT sleeper_id, name, pos FROM players")}


def _write_source_projections(conn: sqlite3.Connection, source: str,
                              rows: list[dict], week: int = 0) -> int:
    """Name+pos join and delete-then-write for a scraped projections source."""
    lookup = _name_lookup(conn)
    if rows:
        conn.execute("DELETE FROM projections WHERE week=? AND source=?", (week, source))
    n = 0
    for p in rows:
        pid = lookup.get((normalize_name(p["name"]), p["position"]))
        if not pid:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO projections(player_id,week,source,pts,floor,ceiling) "
            "VALUES(?,?,?,?,?,?)", (pid, week, source, p["pts"], None, None))
        n += 1
    conn.commit()
    return n


def etl_cbs_projections(conn: sqlite3.Connection) -> int:
    """CBS season projections (their PPR pages — full-PPR leagues only)."""
    if _league_scoring(conn) != "ppr":
        return 0
    return _write_source_projections(conn, "cbs", fetch_cbs_projections(settings.season))


def etl_fftoday_projections(conn: sqlite3.Connection) -> int:
    """FFToday raw-stat projections scored with the league's own settings."""
    row = conn.execute("SELECT scoring_json FROM league").fetchone()
    scoring = (json.loads(row["scoring_json"]) or {}) if row and row["scoring_json"] else {}
    return _write_source_projections(
        conn, "fftoday", fetch_fftoday_projections(settings.season, scoring))


def etl_fp_projections(conn: sqlite3.Connection, week: int = 0) -> dict:
    """FantasyPros aggregate point projections -> projections table (source
    fantasypros). No-ops without FANTASYPROS_API_KEY. Partial position
    failures are reported, not fatal."""
    if not settings.fantasypros_api_key:
        return {"rows": 0, "failed": []}
    scoring = {"ppr": "PPR", "half": "HALF", "std": "STD"}[_league_scoring(conn)]
    lookup = {}
    for row in conn.execute("SELECT sleeper_id, name, pos FROM players").fetchall():
        lookup[(normalize_name(row["name"]), row["pos"])] = row["sleeper_id"]
    fetched = fetch_fantasypros_projections(settings.fantasypros_api_key,
                                            settings.season, scoring, week=week)
    # Full success clears stale rows; a partial fetch only upserts, so the
    # failed positions keep yesterday's numbers instead of vanishing.
    if fetched["rows"] and not fetched["failed"]:
        conn.execute("DELETE FROM projections WHERE week=? AND source='fantasypros'", (week,))
    n = 0
    for p in fetched["rows"]:
        pid = lookup.get((normalize_name(p["name"]), p["position"]))
        if not pid:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO projections(player_id,week,source,pts,floor,ceiling) "
            "VALUES(?,?,?,?,?,?)",
            (pid, week, "fantasypros", p["pts"], None, None),
        )
        n += 1
    conn.commit()
    return {"rows": n, "failed": fetched["failed"]}


def etl_draft_picks(client: SleeperClient, conn: sqlite3.Connection, draft_id: str,
                    full: bool = True) -> int:
    """full=True refreshes the draft document (status, draft_order) too;
    full=False is the hot path — picks only, one HTTP call — so the poller
    can run a sub-second cadence during a live draft. Both paths touch
    drafts.updated_at: that row is the freshness heartbeat the board watches."""
    if full:
        d = client.draft(draft_id)
        # A snake draft cares about the DRAFT SLOT, not the roster id. Sleeper
        # publishes draft_order (user_id -> slot) once the order is set; merge
        # our slot into the stored settings so the board tracks it automatically.
        dsettings = d.get("settings", {}) or {}
        order = d.get("draft_order") or {}
        if settings.user_id and settings.user_id in order:
            dsettings = {**dsettings, "slot": order[settings.user_id]}
        conn.execute(
            "INSERT INTO drafts(draft_id,status,settings_json,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(draft_id) DO UPDATE SET status=excluded.status,"
            "settings_json=excluded.settings_json,updated_at=excluded.updated_at",
            (draft_id, d.get("status"), json.dumps(dsettings), db.utcnow()),
        )
    else:
        conn.execute("UPDATE drafts SET updated_at=? WHERE draft_id=?",
                     (db.utcnow(), draft_id))
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
    out["projections"] = etl_projections(client, conn, week=0)
    try:
        out["fp_ecr"] = etl_fp_ecr(conn)
    except Exception as e:  # a scrape may break; the board must not
        out["fp_ecr"] = f"failed: {e}"
    try:
        out["espn"] = etl_espn_projections(conn)
    except Exception as e:
        out["espn"] = f"failed: {e}"
    try:
        # 403 until FantasyPros enables Projections on the key — never fatal.
        out["fp_projections"] = etl_fp_projections(conn)
    except Exception as e:
        out["fp_projections"] = f"failed: {e}"
    for label, etl in (("cbs", etl_cbs_projections), ("fftoday", etl_fftoday_projections)):
        try:
            out[label] = etl(conn)
        except Exception as e:  # scrapes break; the consensus must not
            out[label] = f"failed: {e}"
    out["consensus"] = compute_consensus(conn, week=0)
    # In-season: weekly projections feed the Sunday lineup card.
    state = client.nfl_state() or {}
    wk = state.get("week") or 0
    if state.get("season_type") == "regular" and wk:
        out[f"projections_w{wk}"] = etl_projections(client, conn, week=wk)
        try:
            out[f"espn_w{wk}"] = etl_espn_projections(conn, week=wk)
        except Exception as e:
            out[f"espn_w{wk}"] = f"failed: {e}"
        try:
            out[f"fp_w{wk}"] = etl_fp_projections(conn, week=wk)
        except Exception as e:
            out[f"fp_w{wk}"] = f"failed: {e}"
        out[f"consensus_w{wk}"] = compute_consensus(conn, week=wk)
    # Persist the report — /health serves it so a quietly dying scrape source
    # becomes visible on the board and alertable from HA, not buried in
    # docker logs nobody reads.
    db.meta_set(conn, "nightly_report", json.dumps({"ts": db.utcnow(), "out": {
        k: v for k, v in out.items()}}, default=str))
    conn.commit()
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
        # Draft-night latency: a snappy client timeout (a hung call must not
        # blind the board), picks-only fetches on the hot path with the full
        # draft doc every 10th cycle, and adaptive cadence — sub-second while
        # the draft is live, polite when nothing is happening.
        client = SleeperClient(timeout=5.0)
        status = None
        cycle = 0
        while True:
            # The poller must outlive Sleeper hiccups: if this process dies,
            # the API keeps serving stale picks while claiming wire-live. The
            # board watches drafts.updated_at and banners when it goes stale.
            try:
                full = cycle % 10 == 0 or status is None
                n = etl_draft_picks(client, conn, draft_id, full=full)
                row = conn.execute("SELECT status FROM drafts WHERE draft_id=?",
                                   (draft_id,)).fetchone()
                status = row["status"] if row else None
                print(f"picks={n} status={status}", flush=True)
            except Exception as e:
                print(f"poll error (retrying): {e}", flush=True)
            cycle += 1
            if status == "drafting":
                time.sleep(0.6)
            elif status == "complete":
                time.sleep(30.0)
            else:
                time.sleep(settings.draft_poll_seconds)


if __name__ == "__main__":
    main()
