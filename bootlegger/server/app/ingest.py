"""ETL: builds consensus/tiers/VBD in the DB, and (live mode) pulls Sleeper,
FFC ADP, and FantasyCalc into the tables. Demo mode seeds the same tables from
the fixture instead — every engine downstream is source-agnostic."""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timezone

import httpx

from . import db, sources
from .config import settings
from .engines import calibration as cal
from .engines import consensus as cx
from .engines import tiers as tiers_engine
from .engines import vbd as vbd_engine
from .schedule import backfill_byes, etl_schedule, refresh_weather
from .sleeper import SleeperClient
from .engines import wire as wire_engine
from .sources import (fetch_cbs_projections, fetch_draftsharks, fetch_espn_projections,
                      fetch_fantasycalc_values, fetch_fantasypros_projections,
                      fetch_ffc_adp, fetch_fftoday_projections, fetch_fp_ecr,
                      fetch_nflverse_injuries, fetch_rotowire_news, normalize_name)

# Tiering pools per position: deep enough to cover draftable players, shallow
# enough that the GMM sees structure instead of a waiver-wire tail.
TIER_POOLS = {"QB": 32, "RB": 64, "WR": 72, "TE": 28, "K": 16, "DEF": 16}


def _league_baselines(conn: sqlite3.Connection) -> dict[str, int] | None:
    """VOLS baselines derived from the league's own roster shape and size;
    None (no league row yet) falls back to the design-doc constants. For the
    canonical 12-team 2-flex shape the derivation reproduces the constants
    exactly (test-pinned), so going live with this changed nothing."""
    row = conn.execute("SELECT settings_json FROM league").fetchone()
    if not row:
        return None
    s = json.loads(row["settings_json"] or "{}")
    rp = s.get("roster_positions") or []
    if not rp:
        return None
    teams = int((s.get("settings") or {}).get("num_teams") or settings.teams)
    return vbd_engine.derive_baselines(teams, rp)


def compute_consensus(conn: sqlite3.Connection, week: int = 0) -> int:
    """Robust-average the per-source projections for `week`, then attach GMM
    tiers and VOLS VBD. Returns the number of players written."""
    rows = conn.execute(
        "SELECT p.player_id, p.source, p.pts, pl.pos FROM projections p "
        "JOIN players pl ON pl.sleeper_id = p.player_id WHERE p.week=?",
        (week,),
    ).fetchall()
    by_player: dict[str, list[float]] = defaultdict(list)
    by_source: dict[str, dict[str, float]] = defaultdict(dict)
    pos_of: dict[str, str] = {}
    for r in rows:
        by_player[r["player_id"]].append(r["pts"])
        by_source[r["player_id"]][r["source"]] = r["pts"]
        pos_of[r["player_id"]] = r["pos"]

    # In-season, sources vote by how right they have been. Week 0 keeps the
    # equal-weight robust mean: a season-long projection has nothing realized
    # to be scored against until the season is over, which is exactly when it
    # has stopped mattering. See engines/calibration.py.
    wmap: dict[str, float] = {}
    weight_note = "equal weight (draft-season projections)"
    if week > 0:
        sources = sorted({r["source"] for r in rows})
        wmap, weight_note = cal.weights(
            cal.score_sources(conn, settings.season), sources)
    calibrated = bool(wmap) and any(
        abs(w - 1.0 / len(wmap)) > 1e-6 for w in wmap.values())
    db.meta_set(conn, f"consensus_weights_w{week}",
                json.dumps({"note": weight_note,
                            "weights": {k: round(v, 3) for k, v in wmap.items()}}))

    robust: dict[str, float] = {}
    stats: dict[str, tuple[float, float | None]] = {}
    for pid, vals in by_player.items():
        # The robust mean stays the fallback for thin coverage: with two or
        # three sources, weighting is noise dressed as precision.
        if calibrated and len(vals) >= 4:
            rb = cal.weighted_mean(by_source[pid], wmap)
        else:
            rb = cx.robust_mean(vals)
        robust[pid] = rb
        stats[pid] = (sum(vals) / len(vals), cx.source_spread(vals))

    by_pos: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for pid, pts in robust.items():
        by_pos[pos_of[pid]].append((pid, pts))
    vbd_map = vbd_engine.compute_vbd(by_pos, _league_baselines(conn))

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
            "bye": None,  # rebuilt every nightly by schedule.backfill_byes (ECR fallback)
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
        # Sleeper keeps the record in settings and splits the score across
        # fpts (whole) + fpts_decimal (hundredths). A league with no games
        # played yet sends no settings block at all, which reads as 0-0.
        rs = r.get("settings") or {}
        fpts = float(rs.get("fpts") or 0) + float(rs.get("fpts_decimal") or 0) / 100.0
        conn.execute(
            "INSERT INTO rosters(roster_id,owner,players_json,starters_json,updated_at,"
            "wins,losses,ties,fpts) "
            "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(roster_id) DO UPDATE SET owner=excluded.owner,"
            "players_json=excluded.players_json,starters_json=excluded.starters_json,"
            "updated_at=excluded.updated_at,wins=excluded.wins,losses=excluded.losses,"
            "ties=excluded.ties,fpts=excluded.fpts",
            (r["roster_id"], users.get(r.get("owner_id"), r.get("owner_id")),
             json.dumps(r.get("players") or []), json.dumps(r.get("starters") or []), now,
             int(rs.get("wins") or 0), int(rs.get("losses") or 0),
             int(rs.get("ties") or 0), round(fpts, 2)),
        )
    conn.commit()


def etl_adp(conn: sqlite3.Connection) -> int:
    """FFC ADP joined to sleeper ids by normalized name+position."""
    now = db.utcnow()
    ffc = fetch_ffc_adp(teams=settings.teams, year=settings.season)
    if len(ffc) >= MIN_WEEKLY_ROWS:
        conn.execute("DELETE FROM adp WHERE source='ffc'")
    else:
        return 0  # stub response — keep yesterday's ADP
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
    # Replace, don't accrete — but only when the batch is real: an API hiccup
    # returning placeholder-only entries must not wipe the source for a day.
    usable_pts = sum(1 for v in blob.values() if v.get(pts_field) or v.get("pts_ppr"))
    usable_adp = sum(1 for v in blob.values()
                     if (v.get(adp_field) or v.get("adp_ppr") or 999) < 900)
    floor = MIN_SOURCE_ROWS if week == 0 else MIN_WEEKLY_ROWS
    if usable_pts >= floor:
        conn.execute("DELETE FROM projections WHERE week=? AND source='sleeper'", (week,))
        if week == 0 and usable_adp >= MIN_WEEKLY_ROWS:
            conn.execute("DELETE FROM adp WHERE source='sleeper'")
    else:
        return 0  # keep yesterday's rows; the skip shows in the nightly report
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


def etl_injuries(conn: sqlite3.Connection) -> dict:
    """Official practice reports (nflverse) onto players.practice_status /
    report_status. Cleared before each fill so stale reports never linger —
    an empty fetch (file not yet published, or a quiet week) leaves the
    columns honestly NULL rather than frozen at last week."""
    rows = fetch_nflverse_injuries(settings.season)
    conn.execute("UPDATE players SET practice_status=NULL, report_status=NULL")
    if not rows:
        conn.commit()
        return {"week": None, "matched": 0}
    lookup = {}
    for r in conn.execute("SELECT sleeper_id, name, pos, team FROM players").fetchall():
        lookup[(normalize_name(r["name"]), r["pos"])] = r["sleeper_id"]
    matched = 0
    for r in rows:
        pid = lookup.get((normalize_name(r["name"]), r["position"]))
        if not pid:
            continue
        conn.execute(
            "UPDATE players SET practice_status=?, report_status=? WHERE sleeper_id=?",
            (r["practice_status"], r["report_status"], pid))
        matched += 1
    conn.commit()
    return {"week": rows[0]["week"], "matched": matched}



# ---------------------------------------------------------------------------
# The wire
# ---------------------------------------------------------------------------

def _in_season(conn: sqlite3.Connection) -> bool:
    """Has a regular-season game actually kicked off yet?

    The wire grader needs this: before Week 1, "won't play Friday" is about an
    exhibition. Read from the schedule rather than the calendar, because the
    schedule is the thing that knows — and a board with no schedule loaded
    assumes IN season, since the expensive mistake is going quiet during the
    year, not being chatty in August.
    """
    row = conn.execute(
        "SELECT MIN(kickoff_utc) k FROM nfl_games WHERE season=? AND week=1 "
        "AND kickoff_utc IS NOT NULL", (settings.season,)).fetchone()
    if not row or not row["k"]:
        return True
    try:
        return datetime.fromisoformat(row["k"]) <= datetime.now(timezone.utc)
    except ValueError:
        return True


def _rostered_ids(conn: sqlite3.Connection) -> set[str]:
    """Every player any seat in this league holds — the disambiguation prior
    for name collisions, and the reason a wire item is 'league' not 'street'."""
    out: set[str] = set()
    for r in conn.execute("SELECT players_json FROM rosters"):
        try:
            out |= set(json.loads(r["players_json"] or "[]"))
        except (ValueError, TypeError):
            continue
    return out


def etl_news(conn: sqlite3.Connection) -> dict:
    """Poll the wire, classify, join to Sleeper ids, persist, and report what
    the poll could not see.

    The gap number is the point of the exercise: RotoWire hands out five items
    per request and stamps them with a monotonic id, so a poll that lands more
    than five ids past the last one PROVES news was published and missed. That
    goes to meta where /health and the board can show it — a wire that quietly
    skips a Sunday scratch is exactly the silent failure this house forbids.
    """
    now = db.utcnow()
    players = [dict(r) for r in conn.execute(
        "SELECT sleeper_id, name, pos, team FROM players")]
    index = wire_engine.build_index(players)
    prefer = _rostered_ids(conn)

    in_season = _in_season(conn)
    rows: list[tuple] = []
    health: dict[str, dict] = {}
    gap = 0
    seqs: list[int] = []

    # RotoWire first, and separately, because it is the only feed that can
    # PROVE it missed something: five items, monotonic ids. The others carry
    # far more news and no such guarantee, so the gap number stays a RotoWire
    # property rather than becoming a claim the rest cannot support.
    try:
        items = fetch_rotowire_news()
        seqs = [i["seq"] for i in items if i["seq"] is not None]
        last_seq = db.meta_get(conn, "wire_last_seq")
        gap = wire_engine.gap_since(int(last_seq) if last_seq else None, seqs)
        for it in items:
            rows.append((
                it["guid"], it["seq"], "rotowire",
                wire_engine.match(it["name"], index, prefer),
                it["name"], it["headline"], it["body"], it["link"],
                wire_engine.severity(it["headline"], it["body"], in_season),
                wire_engine.ailment(it["body"]),
                1 if wire_engine.is_departure(it["headline"], it["body"]) else 0,
                it["published_at"], now,
            ))
        health["rotowire"] = {"ok": True, "items": len(items), "at": now}
    except Exception as exc:                       # noqa: BLE001 — reported, never raised
        # A feed that dies degrades to a VISIBLE hole in coverage, never to a
        # poll that quietly returns fewer sources than it did yesterday.
        health["rotowire"] = {"ok": False, "error": type(exc).__name__, "at": now}

    # The rest of the wire. Each is independent: one 500 costs its own items
    # and nothing else, and says so by name.
    for source in sources.GENERAL_RSS:
        try:
            items = sources.fetch_general_news(source)
            matched = 0
            for it in items:
                pid, phrase = wire_engine.scan_name(it["title"], index, prefer)
                if pid:
                    matched += 1
                rows.append((
                    it["guid"], None, source, pid,
                    phrase or it["title"][:80], it["title"], it["body"], it["link"],
                    wire_engine.severity(it["title"], it["body"], in_season),
                    wire_engine.ailment(it["body"]),
                    1 if wire_engine.is_departure(it["title"], it["body"]) else 0,
                    it["published_at"], now,
                ))
            health[source] = {"ok": True, "items": len(items),
                              "matched": matched, "at": now}
        except Exception as exc:                   # noqa: BLE001
            health[source] = {"ok": False, "error": type(exc).__name__, "at": now}

    db.meta_set(conn, "wire_sources", json.dumps(health))
    db.meta_set(conn, "wire_in_season", "1" if in_season else "0")
    live = [k for k, v in health.items() if v.get("ok")]
    if not rows:
        # A quiet wire and a DEAD wire are not the same event, and only one of
        # them is allowed to advance the freshness clock. Stamping wire_last_ok
        # unconditionally here meant a poll in which all five feeds raised
        # reported as a successful poll: /health showed a current timestamp and
        # a zero fail streak while nothing had been fetched at all.
        if live:
            db.meta_set(conn, "wire_last_ok", now)
            return {"fetched": 0, "new": 0, "gap": 0, "sources": health,
                    "live_sources": len(live), "of": len(health)}
        raise RuntimeError(
            "every wire source failed: "
            + ", ".join(f"{k} ({v.get('error')})" for k, v in sorted(health.items())))
    before = conn.execute("SELECT COUNT(*) c FROM news").fetchone()["c"]
    # INSERT OR IGNORE, never REPLACE: re-polling must not reset pushed_at and
    # re-alarm on an item the owner has already been told about.
    conn.executemany(
        "INSERT OR IGNORE INTO news(guid,seq,source,player_id,name_raw,headline,body,"
        "link,severity,ailment,departure,published_at,fetched_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    # ...but the grade IS re-derived for the items still in the window. The
    # classifier gets better (it has already been corrected twice against live
    # copy), and without this a mis-grade written on first sight would stand
    # forever behind the OR IGNORE. Only severity/ailment/departure and the
    # player join move; pushed_at is never touched here, so nothing re-alarms
    # for an item already notified.
    conn.executemany(
        "UPDATE news SET severity=?, ailment=?, departure=?, player_id=COALESCE(?, player_id) "
        "WHERE guid=? AND pushed_at IS NULL",
        [(r[8], r[9], r[10], r[3], r[0]) for r in rows])
    conn.commit()
    added = conn.execute("SELECT COUNT(*) c FROM news").fetchone()["c"] - before

    if seqs:
        db.meta_set(conn, "wire_last_seq", str(max(seqs)))
    db.meta_set(conn, "wire_last_ok", now)
    if gap:
        total = int(db.meta_get(conn, "wire_gap_total") or 0) + gap
        db.meta_set(conn, "wire_gap_total", str(total))
        db.meta_set(conn, "wire_last_gap", json.dumps({"n": gap, "at": now}))
    unmatched = sum(1 for r in rows if r[3] is None)
    return {"fetched": len(rows), "new": added, "gap": gap,
            "unmatched": unmatched, "sources": health,
            "live_sources": len(live), "of": len(health)}



def etl_matchups(conn: sqlite3.Connection, client: "SleeperClient",
                 week: int) -> dict:
    """This week's pairings and last week's realized scores.

    Sleeper's matchups endpoint hands back one row per roster carrying a shared
    `matchup_id`; the opponent is simply the other roster wearing the same id.
    `points` is the realized score, which is the whole reason to persist this:
    (actual - projected) over enough roster-weeks is what lets the win
    probability quote THIS league's spread instead of a magazine's.
    """
    if not settings.league_id:
        return {"week": week, "rows": 0}
    rows = client.matchups(settings.league_id, week) or []
    by_matchup: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("matchup_id") is not None:
            by_matchup[r["matchup_id"]].append(r)
    projected = {r["player_id"]: (r["pts_robust"] or 0.0) for r in conn.execute(
        "SELECT player_id, pts_robust FROM consensus WHERE week=?", (week,))}
    # Sleeper's matchup payload carries players_points: every rostered player's
    # realized score under THIS league's scoring. It is the only place that
    # number exists without re-deriving it, and it is what makes source
    # calibration possible at all.
    actuals = []
    for r in rows:
        for pid, pts in (r.get("players_points") or {}).items():
            if pts is not None:
                actuals.append((settings.season, week, pid, float(pts), db.utcnow()))
    if actuals:
        conn.executemany(
            "INSERT INTO player_week_actuals(season,week,player_id,pts,updated_at) "
            "VALUES(?,?,?,?,?) ON CONFLICT(season,week,player_id) DO UPDATE SET "
            "pts=excluded.pts, updated_at=excluded.updated_at", actuals)

    written = 0
    for mid, pair in by_matchup.items():
        for r in pair:
            opp = next((o for o in pair if o is not r), None)
            starters = r.get("starters") or []
            proj_for = round(sum(projected.get(p, 0.0) for p in starters), 1)
            opp_starters = (opp or {}).get("starters") or []
            proj_against = round(sum(projected.get(p, 0.0) for p in opp_starters), 1)
            conn.execute(
                "INSERT INTO matchups(week,roster_id,opp_roster_id,proj_for,proj_against,"
                "points_for,matchup_id) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(week,roster_id) DO UPDATE SET "
                "opp_roster_id=excluded.opp_roster_id,proj_for=excluded.proj_for,"
                "proj_against=excluded.proj_against,points_for=excluded.points_for,"
                "matchup_id=excluded.matchup_id",
                (week, r.get("roster_id"), (opp or {}).get("roster_id"),
                 proj_for, proj_against, r.get("points"), mid))
            written += 1
    conn.commit()
    return {"week": week, "rows": written, "actuals": len(actuals)}



# How many seasons back the room's habits are read from. Beyond three the
# managers, and often the league, are not the same room any more.
HISTORY_SEASONS = 3


def etl_draft_history(conn: sqlite3.Connection, client: "SleeperClient",
                      seasons: int = HISTORY_SEASONS) -> dict:
    """Walk previous_league_id back and store past drafts' completed picks.

    This is the only evidence that exists for how THIS room drafts — Sleeper
    keeps no historical ADP, so the past is readable only through its own pick
    order (engines/room.py explains what is done with it). Stored into the same
    drafts / draft_picks tables the live draft uses, so nothing downstream needs
    a second code path.
    """
    if not settings.league_id:
        return {"leagues": 0, "drafts": 0, "picks": 0}
    league_id = settings.league_id
    leagues = drafts = picks = 0
    seen: set[str] = set()
    for _ in range(seasons + 1):
        if not league_id or league_id in seen:
            break
        seen.add(league_id)
        try:
            info = client.league(league_id) or {}
        except Exception:
            break
        prev = info.get("previous_league_id")
        if league_id != settings.league_id:     # the current draft has its own poller
            leagues += 1
            try:
                for d in client.league_drafts(league_id) or []:
                    if d.get("status") != "complete":
                        continue
                    did = str(d.get("draft_id"))
                    st = d.get("settings") or {}
                    conn.execute(
                        "INSERT OR REPLACE INTO drafts(draft_id,status,settings_json,updated_at) "
                        "VALUES(?,?,?,?)",
                        (did, "complete", json.dumps({
                            "teams": st.get("teams"), "rounds": st.get("rounds"),
                            "season": d.get("season"), "historical": True}), db.utcnow()))
                    drafts += 1
                    rows = client.draft_picks(did) or []
                    for pk in rows:
                        meta = pk.get("metadata") or {}
                        pos = (meta.get("position") or "").upper().replace("DST", "DEF")
                        conn.execute(
                            "INSERT OR REPLACE INTO draft_picks"
                            "(draft_id,pick_no,round,draft_slot,roster_id,player_id,ts,pos) "
                            "VALUES(?,?,?,?,?,?,?,?)",
                            (did, pk.get("pick_no"), pk.get("round"),
                             pk.get("draft_slot"), pk.get("roster_id"),
                             str(pk.get("player_id")), db.utcnow(), pos or None))
                    picks += len(rows)
            except Exception as e:
                return {"leagues": leagues, "drafts": drafts, "picks": picks,
                        "stopped": str(e)}
        league_id = prev
    conn.commit()
    return {"leagues": leagues, "drafts": drafts, "picks": picks}


def etl_fp_ecr(conn: sqlite3.Connection) -> dict:
    """FantasyPros expert-consensus ranks -> adp table (source fp_ecr), and bye
    weeks backfilled onto players (the Sleeper feed leaves byes null)."""
    data = fetch_fp_ecr(scoring=_league_scoring(conn))
    lookup = {}
    for row in conn.execute("SELECT sleeper_id, name, pos FROM players").fetchall():
        lookup[(normalize_name(row["name"]), row["pos"])] = row["sleeper_id"]
    now = db.utcnow()
    if len(data["players"]) >= MIN_SOURCE_ROWS:  # a real delivery, not a stub
        conn.execute("DELETE FROM adp WHERE source='fp_ecr'")
    else:
        return {"experts": data["experts"], "matched": 0, "byes": 0,
                "skipped": f"only {len(data['players'])} players parsed"}
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
            # Fallback only: the schedule-derived byes (etl_schedule, earlier
            # in the nightly) are authoritative; ECR fills whatever they miss.
            if conn.execute("UPDATE players SET bye=? WHERE sleeper_id=? AND bye IS NULL",
                            (p["bye"], pid)).rowcount:
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


# Guards on delete-then-write: one half-broken fetch must never thin a source.
# A batch must join enough players AND land in a sane points range before it
# is allowed to replace yesterday's data (a misparsed column reads ~20 pts).
MIN_SOURCE_ROWS = 100
MIN_WEEKLY_ROWS = 40
SANE_MEDIAN = (50.0, 500.0)
SANE_WEEKLY_MEDIAN = (4.0, 40.0)


def _batch_ok(joined: list[tuple], week: int) -> bool:
    floor = MIN_SOURCE_ROWS if week == 0 else MIN_WEEKLY_ROWS
    if len(joined) < floor:
        return False
    pts = sorted(p for _, p in joined)
    med = pts[len(pts) // 2]
    lo, hi = SANE_MEDIAN if week == 0 else SANE_WEEKLY_MEDIAN
    return lo <= med <= hi


# How far a source may shrink against its own recent best before the board
# calls it drifting. A scrape whose page changed usually still parses — it just
# parses FEWER rows, which the absolute floor above happily lets through. This
# is the canary that catches that: the source is measured against itself.
DRIFT_FRACTION = 0.6


def _source_health_note(conn: sqlite3.Connection, source: str, week: int,
                        rows: int) -> dict:
    """Record what a source delivered, and judge it against its own best.

    Absolute floors catch a source that dies. They do not catch one that half
    dies: a CBS or FFToday page that changes its markup typically still parses,
    just into a fraction of the rows. Watching each source against its own high
    watermark is what makes that visible on the day it happens rather than in
    December when the consensus has quietly been running on four sources.
    """
    key = f"source_stat_{source}_w{week}"
    try:
        prev = json.loads(db.meta_get(conn, key) or "{}")
    except ValueError:
        prev = {}
    best = max(int(prev.get("best") or 0), rows)
    status = "ok"
    if rows == 0:
        status = "skipped"           # the batch guard kept yesterday's rows
    elif best and rows < DRIFT_FRACTION * best:
        status = "drifting"
    note = {"rows": rows, "best": best, "status": status,
            "at": db.utcnow() if rows else prev.get("at")}
    db.meta_set(conn, key, json.dumps(note))
    return note


def _write_source_projections(conn: sqlite3.Connection, source: str,
                              rows: list[dict], week: int = 0) -> int:
    """Name+pos join, batch sanity, then delete-then-write. A batch that is
    too small or has an insane median keeps yesterday's rows and returns 0 —
    the nightly report and the source-health sensor surface the skip."""
    lookup = _name_lookup(conn)
    joined = []
    for p in rows:
        pid = lookup.get((normalize_name(p["name"]), p["position"]))
        if pid:
            joined.append((pid, p["pts"], p.get("floor"), p.get("ceiling")))
    if not _batch_ok([(a, b) for a, b, *_ in joined], week):
        _source_health_note(conn, source, week, 0)
        return 0
    conn.execute("DELETE FROM projections WHERE week=? AND source=?", (week, source))
    for pid, pts, floor, ceiling in joined:
        conn.execute(
            "INSERT OR REPLACE INTO projections(player_id,week,source,pts,floor,ceiling) "
            "VALUES(?,?,?,?,?,?)", (pid, week, source, pts, floor, ceiling))
    conn.commit()
    _source_health_note(conn, source, week, len(joined))
    return len(joined)


def etl_cbs_projections(conn: sqlite3.Connection, week: int = 0) -> int:
    """CBS projections (their PPR pages — full-PPR leagues only).

    Week 0 is the draft-season table; a week number pulls that week's, which is
    what stops the in-season consensus running on three sources when the
    preseason one runs on six.

    UNVERIFIED for weeks until the season starts: probed 2026-08-26, CBS served
    SEASON totals from the week-3 URL (Josh Allen at 419), presumably because
    no 2026 week has been played. That is exactly the silent wrong-scale bug
    the weekly median guard exists for — _batch_ok rejects the batch (median
    124.9 against a 4-40 weekly band) and the source is recorded as skipped
    rather than poisoning the consensus. If CBS still serves season numbers in
    week 1, this source will simply never fill in-season, visibly, in the
    nightly report. Do not "fix" that by loosening the guard.
    """
    if _league_scoring(conn) != "ppr":
        return 0
    return _write_source_projections(
        conn, "cbs", fetch_cbs_projections(settings.season, week=week), week=week)


def etl_fftoday_projections(conn: sqlite3.Connection) -> int:
    """FFToday raw-stat projections scored with the league's own settings."""
    row = conn.execute("SELECT scoring_json FROM league").fetchone()
    scoring = (json.loads(row["scoring_json"]) or {}) if row and row["scoring_json"] else {}
    return _write_source_projections(
        conn, "fftoday", fetch_fftoday_projections(settings.season, scoring))


def etl_draftsharks(conn: sqlite3.Connection) -> int:
    """Draft Sharks house projections (their 3-year award-winning numbers)
    with floor/ceiling, plus injury risk + projected games onto players.
    Session cookie from DS_COOKIE_FILE; missing/expired = source off, which
    the source-health alert surfaces."""
    if _league_scoring(conn) != "ppr":  # only the PPR slug is mapped
        return 0
    from pathlib import Path
    cf = Path(settings.ds_cookie_file)
    if not cf.exists():
        return 0
    rows = fetch_draftsharks(cf.read_text().strip())
    n = _write_source_projections(conn, "draftsharks", rows)
    if n:
        lookup = _name_lookup(conn)
        for p in rows:
            pid = lookup.get((normalize_name(p["name"]), p["position"]))
            if pid and (p.get("injury_pct") is not None or p.get("proj_games") is not None):
                conn.execute("UPDATE players SET injury_risk=?, proj_games=? WHERE sleeper_id=?",
                             (p.get("injury_pct"), p.get("proj_games"), pid))
        conn.commit()
    return n


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
    joined = []
    for p in fetched["rows"]:
        pid = lookup.get((normalize_name(p["name"]), p["position"]))
        if pid:
            joined.append((pid, p["pts"]))
    # Full, sane success clears stale rows; a partial or suspicious batch only
    # upserts, so old rows survive instead of vanishing on a bad fetch.
    if not fetched["failed"] and _batch_ok(joined, week):
        conn.execute("DELETE FROM projections WHERE week=? AND source='fantasypros'", (week,))
    for pid, pts in joined:
        conn.execute(
            "INSERT OR REPLACE INTO projections(player_id,week,source,pts,floor,ceiling) "
            "VALUES(?,?,?,?,?,?)", (pid, week, "fantasypros", pts, None, None))
    conn.commit()
    return {"rows": len(joined), "failed": fetched["failed"]}


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
    # The season's clock. Everything week-shaped (the lineup scanner, the
    # week card, weekly projections) needs to know what week it is; without
    # this they were all pinned to week 1 forever.
    try:
        state = client.nfl_state() or {}
        wk = int(state.get("week") or 0)
        # Preseason weeks also count up in state/nfl (Aug 2026 reported week 3
        # — of PRESEASON). Only a regular-season week may drive the week-shaped
        # machinery; outside it the meta keeps its last value (default 1, which
        # is correct for the draft-to-Week-1 window).
        if wk > 0 and state.get("season_type") == "regular":
            db.meta_set(conn, "current_week", str(wk))
            out["current_week"] = wk
        else:
            out["current_week"] = f"kept (state: {state.get('season_type')} wk {wk})"
    except Exception as e:
        print(f"state/nfl unavailable ({e}); keeping last known week", flush=True)
    if settings.league_id:
        etl_league(client, conn)
        etl_rosters(client, conn)
    # The schedule layer: kickoff times for the don't-act rules and locks,
    # byes straight from the source (FP-ECR scrape remains the fallback),
    # and this week's outdoor-game weather. Byes and weather read the
    # PERSISTED nfl_games table, so they run even when tonight's nflverse
    # fetch fails — etl_players has already nulled every bye, and restoring
    # them is pure DB work that must not die with someone else's network.
    try:
        out["schedule"] = etl_schedule(conn, settings.season)
    except Exception as e:  # the board must not die with the schedule mirror
        out["schedule"] = f"failed: {e}"
    try:
        out["byes"] = backfill_byes(conn, settings.season)
        wk_now = int(db.meta_get(conn, "current_week") or 0)
        if wk_now:
            out["weather"] = refresh_weather(conn, settings.season, wk_now)
    except Exception as e:
        out["byes"] = f"failed: {e}"
    try:
        out["injuries"] = etl_injuries(conn)
    except Exception as e:
        out["injuries"] = f"failed: {e}"
    try:
        out["news"] = etl_news(conn)
    except Exception as e:  # the wire is polled every few minutes anyway
        out["news"] = f"failed: {e}"
    try:
        # Past drafts change slowly; re-reading them nightly is cheap and means
        # a newly-linked previous season is picked up without a special run.
        out["history"] = etl_draft_history(conn, client)
    except Exception as e:
        out["history"] = f"failed: {e}"
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
    for label, etl in (("cbs", etl_cbs_projections), ("fftoday", etl_fftoday_projections),
                       ("draftsharks", etl_draftsharks)):
        try:
            out[label] = etl(conn)
        except Exception as e:  # scrapes break; the consensus must not
            out[label] = f"failed: {e}"
    out["consensus"] = compute_consensus(conn, week=0)
    # In-season: weekly projections feed the Sunday lineup card. A failed
    # state read skips the weekly block — it must not abort the run before
    # the report persists and the dead-man ping fires.
    try:
        state = client.nfl_state() or {}
    except Exception:
        state = {}
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
        try:
            out[f"cbs_w{wk}"] = etl_cbs_projections(conn, week=wk)
        except Exception as e:
            out[f"cbs_w{wk}"] = f"failed: {e}"
        out[f"consensus_w{wk}"] = compute_consensus(conn, week=wk)
        # Pairings for the week ahead, plus the realized scores of the weeks
        # behind — the win-probability model reads both.
        for target in {wk, max(1, wk - 1)}:
            try:
                out[f"matchups_w{target}"] = etl_matchups(conn, client, target)
            except Exception as e:
                out[f"matchups_w{target}"] = f"failed: {e}"
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
        last_target = None
        while True:
            # The poller must outlive Sleeper hiccups: if this process dies,
            # the API keeps serving stale picks while claiming wire-live. The
            # board watches drafts.updated_at and banners when it goes stale.
            try:
                # Scrimmage mode: the UI can point the wire at a practice room
                # by writing its draft id to meta; clearing it reverts to the
                # league draft. Resolved every cycle — no container surgery.
                target = db.meta_get(conn, "practice_draft_id") or draft_id
                if target != last_target:
                    status, cycle = None, 0   # full doc immediately on a switch
                    if last_target is not None:
                        # Backstop for the clear/poll race: a cycle already in
                        # flight for the OLD target can re-upsert rows the API
                        # just deleted, leaving a ghost draft that contests the
                        # newest-draft rule. On every switch, sweep anything
                        # that is neither the new target nor the league draft.
                        conn.execute(
                            "DELETE FROM draft_picks WHERE draft_id NOT IN (?,?)",
                            (target, draft_id))
                        conn.execute(
                            "DELETE FROM drafts WHERE draft_id NOT IN (?,?)",
                            (target, draft_id))
                        conn.commit()
                    last_target = target
                full = cycle % 10 == 0 or status is None
                n = etl_draft_picks(client, conn, target, full=full)
                row = conn.execute("SELECT status FROM drafts WHERE draft_id=?",
                                   (target,)).fetchone()
                status = row["status"] if row else None
                print(f"draft={target} picks={n} status={status}", flush=True)
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
