"""FastAPI backend: serves the JSON API for the mobile app and the web
fallback surface. In demo mode it also runs the draft simulator, the lineup
scanner, and the hands worker in-process so `uvicorn app.api:app` is the whole
show; live mode splits those into the compose services."""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import brain, db, demo, recs
from .config import settings
from .engines import lineup as lineup_engine
from .engines import trades as trades_engine
from .engines import waivers as waivers_engine
from .sleeper import SleeperClient

log = logging.getLogger("bootlegger.api")
WEB_DIR = Path(__file__).parent / "web"


def require_token(x_bootlegger_token: str | None = Header(default=None)) -> None:
    """Gate for state-changing routes. Off until BOOTLEGGER_API_TOKEN is set —
    the port is LAN-only today, but enabling the hands makes an open mutation
    surface unacceptable. Clients send X-Bootlegger-Token; the web board reads
    localStorage['bootlegger.token']."""
    if settings.api_token and x_bootlegger_token != settings.api_token:
        raise HTTPException(401, "X-Bootlegger-Token required")


MUTATES = [Depends(require_token)]


def get_conn() -> sqlite3.Connection:
    conn = db.connect()
    return conn


async def _demo_loops(app: FastAPI) -> None:
    """One ticker drives the simulated draft, the lineup scanner, and hands."""
    from hands import worker as hands_worker
    conn = db.connect()
    while True:
        try:
            demo.tick(conn, lambda: brain.suggest_my_pick(conn))
            recs.scan_lineup(conn, week=1)
            hands_worker.run_once(conn)
        except Exception:
            log.exception("demo loop error")
        await asyncio.sleep(1.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = db.connect()
    db.init_db(conn)
    if settings.mode == "demo":
        demo.seed(conn)
        task = asyncio.create_task(_demo_loops(app))
        log.info("demo mode: simulated draft + scanner + hands running in-process")
        yield
        task.cancel()
    else:
        yield


app = FastAPI(title="Bootlegger", lifespan=lifespan)


EXPECTED_SOURCES = ("sleeper", "espn", "fantasypros", "cbs", "fftoday", "draftsharks")


@app.get("/health")
def health():
    conn = get_conn()
    drow = conn.execute("SELECT status FROM drafts ORDER BY updated_at DESC LIMIT 1").fetchone()
    last = conn.execute("SELECT MAX(updated_at) AS t FROM players").fetchone()
    # Source health: a scrape that quietly dies must be visible here — the
    # board's colophon and an HA sensor both read this.
    counts = {r["source"]: r["n"] for r in conn.execute(
        "SELECT source, COUNT(*) AS n FROM projections WHERE week=0 GROUP BY source")}
    live_sources = [s for s in EXPECTED_SOURCES if counts.get(s, 0) >= 50]
    nightly = db.meta_get(conn, "nightly_report")
    return {
        "ok": True,
        "mode": settings.mode,
        "draft_status": drow["status"] if drow else None,
        "players_updated_at": last["t"],
        "approve_required": settings.approve_required,
        "hands_dry_run": settings.hands_dry_run,
        "projection_sources": counts,
        "sources_live": len(live_sources) if settings.mode == "live" else None,
        "sources_expected": len(EXPECTED_SOURCES),
        "sources_missing": [s for s in EXPECTED_SOURCES if s not in live_sources]
                           if settings.mode == "live" else [],
        "nightly_report": json.loads(nightly) if nightly else None,
    }


# Board cache: the payload is fully determined by (draft, pick count, status)
# until the nightly refreshes source tables, so 1 Hz draft-night polling from
# the web board + overlay costs one rebuild per actual pick, not per request.
# synced_at is patched fresh every hit — it's the staleness heartbeat and must
# never be served stale itself. TTL backstops nightly-data refresh.
_board_cache: dict = {"key": None, "board": None, "ts": 0.0}


@app.get("/api/draft/board")
def draft_board():
    conn = get_conn()
    drow = conn.execute(
        "SELECT draft_id, status, updated_at FROM drafts "
        "ORDER BY updated_at DESC LIMIT 1").fetchone()
    if drow:
        n = conn.execute("SELECT COUNT(*) FROM draft_picks WHERE draft_id=?",
                         (drow["draft_id"],)).fetchone()[0]
        key = (drow["draft_id"], n, drow["status"])
        if _board_cache["key"] == key and time.time() - _board_cache["ts"] < 20:
            board = _board_cache["board"]
            board["draft"]["synced_at"] = drow["updated_at"]
            return board
    board = brain.get_board(conn)
    if drow:
        _board_cache.update(key=key, board=board, ts=time.time())
    return board


@app.get("/api/draft/player/{player_id}")
def draft_player(player_id: str):
    """The scout's file: everything the board knows about one player."""
    d = brain.player_dossier(get_conn(), player_id)
    if not d:
        raise HTTPException(404, "unknown player")
    return d


@app.post("/api/draft/reset", dependencies=MUTATES)
def draft_reset():
    if settings.mode != "demo":
        raise HTTPException(400, "reset exists only in demo mode")
    demo.reset_draft(get_conn())
    return {"ok": True}


@app.get("/api/week/{week}")
def week_card(week: int):
    return brain.get_week_card(get_conn(), week)


@app.get("/api/recs")
def get_recs():
    return recs.list_recs(get_conn())


@app.post("/api/recs/{rec_id}/approve", dependencies=MUTATES)
def approve_rec(rec_id: int):
    conn = get_conn()
    try:
        job_id = recs.approve(conn, rec_id)
    except recs.BadTransition as e:
        raise HTTPException(409, str(e))
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"ok": True, "job_id": job_id}


@app.post("/api/recs/{rec_id}/snooze", dependencies=MUTATES)
def snooze_rec(rec_id: int):
    try:
        recs.snooze(get_conn(), rec_id)
    except recs.BadTransition as e:
        raise HTTPException(409, str(e))
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}


@app.post("/api/recs/{rec_id}/ignore", dependencies=MUTATES)
def ignore_rec(rec_id: int):
    try:
        recs.ignore(get_conn(), rec_id)
    except recs.BadTransition as e:
        raise HTTPException(409, str(e))
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}


@app.get("/api/audit")
def audit(limit: int = 100):
    conn = get_conn()
    rows = conn.execute(
        "SELECT a.*, r.kind, r.week FROM actions_log a "
        "LEFT JOIN recommendations r ON r.rec_id = a.rec_id "
        "ORDER BY a.action_id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/rules")
def get_rules():
    return [dict(r) for r in get_conn().execute("SELECT * FROM rules ORDER BY rule_id")]


@app.post("/api/rules/{rule_id}/toggle", dependencies=MUTATES)
def toggle_rule(rule_id: int):
    conn = get_conn()
    row = conn.execute("SELECT enabled FROM rules WHERE rule_id=?", (rule_id,)).fetchone()
    if not row:
        raise HTTPException(404, "no such rule")
    conn.execute("UPDATE rules SET enabled=? WHERE rule_id=?",
                 (0 if row["enabled"] else 1, rule_id))
    conn.commit()
    return {"ok": True, "enabled": not row["enabled"]}


# The street's pulse: Sleeper trending adds, cached 15 min. The refresh runs
# in a background thread so the request path never blocks on the wire — a
# request during refresh serves the previous counts (or none on cold start).
_trending: dict = {"ts": 0.0, "counts": {}, "refreshing": False}


def _refresh_trending() -> None:
    try:
        _trending["counts"] = {t["player_id"]: t["count"]
                               for t in SleeperClient().trending_adds(hours=24, limit=50)}
    except Exception:
        pass  # heat goes quiet, never the endpoint
    _trending["ts"] = time.time()
    _trending["refreshing"] = False


def _trending_counts() -> dict[str, int]:
    if time.time() - _trending["ts"] > 900 and not _trending["refreshing"]:
        _trending["refreshing"] = True
        threading.Thread(target=_refresh_trending, daemon=True).start()
    return _trending["counts"]


@app.get("/api/waivers")
def waiver_targets(week: int = 1):
    """Free agents ranked by FA score with sized bids (design doc §4). Route
    owns transport (the trending cache is a network concern); the logic lives
    in brain.waiver_targets."""
    return brain.waiver_targets(get_conn(), heat=_trending_counts())


class TradeBody(BaseModel):
    give: list[str]
    receive: list[str]
    their_roster_id: int | None = None  # grade their side's lineup too


@app.post("/api/trades/analyze", dependencies=MUTATES)
def analyze_trade(body: TradeBody):
    conn = get_conn()
    cons = {r["player_id"]: r for r in conn.execute("SELECT * FROM consensus WHERE week=0")}
    players = {r["sleeper_id"]: r for r in conn.execute("SELECT * FROM players")}
    vals = {r["player_id"]: r for r in conn.execute("SELECT * FROM player_values")}

    def rows(ids: list[str]) -> list[dict]:
        out = []
        for pid in ids:
            if pid not in players:
                raise HTTPException(404, f"unknown player {pid}")
            out.append({
                "player_id": pid, "name": players[pid]["name"], "pos": players[pid]["pos"],
                "vbd": (cons.get(pid) or {"vbd": 0})["vbd"] or 0,
                "market_value": (vals.get(pid) or {"redraft_value": 0})["redraft_value"] or 0,
            })
        return out

    vbd_scale = max([(r["vbd"] or 0.0) for r in cons.values()] or [1.0]) or 1.0
    mkt_scale = max([(r["redraft_value"] or 0.0) for r in vals.values()] or [1.0]) or 1.0
    result = trades_engine.analyze(rows(body.give), rows(body.receive),
                                   vbd_scale=vbd_scale, market_scale=mkt_scale)

    # Roster context — the question a raw value delta can't answer: does MY
    # best lineup actually improve? A trade can win VBD and hand me a third TE.
    def _projs(ids: list[str]) -> list[lineup_engine.PlayerProj]:
        return [lineup_engine.PlayerProj(
                    pid, players[pid]["pos"],
                    (cons.get(pid) or {"pts_robust": 0})["pts_robust"] or 0.0,
                    players[pid]["name"], players[pid]["injury_status"])
                for pid in ids if pid in players]

    my = brain.my_roster_row(conn)
    my_ids = json.loads(my["players_json"]) if my else []
    if my_ids:
        rp = brain.roster_positions(conn)
        before = lineup_engine.optimize(_projs(my_ids), rp).total
        after_ids = [i for i in my_ids if i not in set(body.give)] + list(body.receive)
        after = lineup_engine.optimize(_projs(after_ids), rp).total
        result["lineup_impact"] = {
            "ros_points_before": round(before, 1),
            "ros_points_after": round(after, 1),
            "starters_delta": round(after - before, 1),
            "note": "Season-total consensus points of your optimal lineup, before and after.",
        }
    else:
        result["lineup_impact"] = None

    result["their_lineup_impact"] = None
    if body.their_roster_id is not None:
        tr = conn.execute("SELECT * FROM rosters WHERE roster_id=?",
                          (body.their_roster_id,)).fetchone()
        their_ids = json.loads(tr["players_json"]) if tr else []
        if their_ids:
            rp = brain.roster_positions(conn)
            t_before = lineup_engine.optimize(_projs(their_ids), rp).total
            t_after_ids = [i for i in their_ids if i not in set(body.receive)] + list(body.give)
            t_after = lineup_engine.optimize(_projs(t_after_ids), rp).total
            result["their_lineup_impact"] = {
                "ros_points_before": round(t_before, 1),
                "ros_points_after": round(t_after, 1),
                "starters_delta": round(t_after - t_before, 1),
            }
    return result


@app.get("/api/trades/suggest")
def trade_suggestions(limit: int = 8):
    """The parlor: mutually beneficial deals scanned across every roster."""
    return brain.suggest_trades(get_conn(), limit=max(1, min(limit, 20)))


class DeviceBody(BaseModel):
    push_token: str
    platform: str = "android"


@app.post("/api/devices")
def register_device(body: DeviceBody):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO devices(push_token,platform,created_at) VALUES(?,?,?)",
        (body.push_token, body.platform, db.utcnow()))
    conn.commit()
    return {"ok": True}


@app.get("/api/players/search")
def player_search(q: str):
    conn = get_conn()
    rows = conn.execute(
        "SELECT sleeper_id AS id, name, pos, team FROM players "
        "WHERE name LIKE ? ORDER BY name LIMIT 12", (f"%{q}%",)).fetchall()
    return [dict(r) for r in rows]


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")
