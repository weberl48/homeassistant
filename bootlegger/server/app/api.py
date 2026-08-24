"""FastAPI backend: serves the JSON API for the mobile app and the web
fallback surface. In demo mode it also runs the draft simulator, the lineup
scanner, and the hands worker in-process so `uvicorn app.api:app` is the whole
show; live mode splits those into the compose services."""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import brain, db, demo, recs
from .config import settings
from .engines import trades as trades_engine
from .engines import waivers as waivers_engine
from .sleeper import SleeperClient

log = logging.getLogger("bootlegger.api")
WEB_DIR = Path(__file__).parent / "web"


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


@app.get("/health")
def health():
    conn = get_conn()
    drow = conn.execute("SELECT status FROM drafts LIMIT 1").fetchone()
    last = conn.execute("SELECT MAX(updated_at) AS t FROM players").fetchone()
    return {
        "ok": True,
        "mode": settings.mode,
        "draft_status": drow["status"] if drow else None,
        "players_updated_at": last["t"],
        "approve_required": settings.approve_required,
        "hands_dry_run": settings.hands_dry_run,
    }


@app.get("/api/draft/board")
def draft_board():
    return brain.get_board(get_conn())


@app.post("/api/draft/reset")
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


@app.post("/api/recs/{rec_id}/approve")
def approve_rec(rec_id: int):
    conn = get_conn()
    try:
        job_id = recs.approve(conn, rec_id)
    except recs.BadTransition as e:
        raise HTTPException(409, str(e))
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"ok": True, "job_id": job_id}


@app.post("/api/recs/{rec_id}/snooze")
def snooze_rec(rec_id: int):
    try:
        recs.snooze(get_conn(), rec_id)
    except recs.BadTransition as e:
        raise HTTPException(409, str(e))
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}


@app.post("/api/recs/{rec_id}/ignore")
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


@app.post("/api/rules/{rule_id}/toggle")
def toggle_rule(rule_id: int):
    conn = get_conn()
    row = conn.execute("SELECT enabled FROM rules WHERE rule_id=?", (rule_id,)).fetchone()
    if not row:
        raise HTTPException(404, "no such rule")
    conn.execute("UPDATE rules SET enabled=? WHERE rule_id=?",
                 (0 if row["enabled"] else 1, rule_id))
    conn.commit()
    return {"ok": True, "enabled": not row["enabled"]}


# The street's pulse: Sleeper trending adds, cached 15 min so the 15s waiver
# poll never hammers the wire. Failure means heat goes quiet, never the endpoint.
_trending: dict = {"ts": 0.0, "counts": {}}


def _trending_counts() -> dict[str, int]:
    if time.time() - _trending["ts"] > 900:
        try:
            _trending["counts"] = {t["player_id"]: t["count"]
                                   for t in SleeperClient().trending_adds(hours=24, limit=50)}
        except Exception:
            pass
        _trending["ts"] = time.time()
    return _trending["counts"]


@app.get("/api/waivers")
def waiver_targets(week: int = 1):
    """Free agents ranked by FA score with sized bids (design doc §4)."""
    conn = get_conn()
    heat = _trending_counts()
    rostered: set[str] = set()
    for r in conn.execute("SELECT players_json FROM rosters"):
        rostered |= set(json.loads(r["players_json"]))
    my = brain.my_roster_row(conn)
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
    return {"targets": out[:20], "history_n": len(bids_hist),
            "note": "Advisory only — waivers have no actuation path."}


class TradeBody(BaseModel):
    give: list[str]
    receive: list[str]


@app.post("/api/trades/analyze")
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

    return trades_engine.analyze(rows(body.give), rows(body.receive))


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
