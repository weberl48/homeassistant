"""SQLite (WAL) storage. Schema follows the design doc §3, plus operational
tables the runtime needs (drafts, draft_picks, jobs, devices, meta)."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS players(
    sleeper_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    pos TEXT NOT NULL,
    team TEXT,
    bye INTEGER,
    status TEXT DEFAULT 'Active',
    injury_status TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS projections(
    player_id TEXT NOT NULL,
    week INTEGER NOT NULL,          -- 0 = rest-of-season / draft
    source TEXT NOT NULL,
    pts REAL NOT NULL,
    floor REAL,
    ceiling REAL,
    PRIMARY KEY(player_id, week, source)
);
CREATE TABLE IF NOT EXISTS consensus(
    player_id TEXT NOT NULL,
    week INTEGER NOT NULL,
    pts_mean REAL,
    pts_robust REAL,
    stdev REAL,
    tier INTEGER,
    vbd REAL,
    PRIMARY KEY(player_id, week)
);
-- NOTE: `adp` holds pick-position ADP for sources sleeper/ffc/demo but EXPERT
-- CONSENSUS RANK for source fp_ecr (rank ~ ADP early, diverges in the tail).
-- brain.py's composition prefers true ADP and falls back to rank; never
-- average across sources without checking `source`.
CREATE TABLE IF NOT EXISTS adp(
    player_id TEXT NOT NULL,
    source TEXT NOT NULL,
    adp REAL NOT NULL,
    stdev REAL,
    updated_at TEXT,
    PRIMARY KEY(player_id, source)
);
CREATE TABLE IF NOT EXISTS player_values(   -- FantasyCalc; "values" is a SQL keyword
    player_id TEXT PRIMARY KEY,
    redraft_value REAL,
    trend_30d REAL,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS league(
    league_id TEXT PRIMARY KEY,
    settings_json TEXT,
    scoring_json TEXT
);
CREATE TABLE IF NOT EXISTS rosters(
    roster_id INTEGER PRIMARY KEY,
    owner TEXT,
    players_json TEXT,
    starters_json TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS matchups(
    week INTEGER NOT NULL,
    roster_id INTEGER NOT NULL,
    opp_roster_id INTEGER,
    proj_for REAL,
    proj_against REAL,
    PRIMARY KEY(week, roster_id)
);
CREATE TABLE IF NOT EXISTS transactions(
    txn_id TEXT PRIMARY KEY,
    week INTEGER,
    type TEXT,
    adds_json TEXT,
    drops_json TEXT,
    faab INTEGER,
    status TEXT,
    ts TEXT
);
CREATE TABLE IF NOT EXISTS recommendations(
    rec_id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,             -- lineup | waiver | trade | injury
    week INTEGER,
    payload_json TEXT NOT NULL,
    rationale TEXT,
    state TEXT NOT NULL DEFAULT 'proposed',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS actions_log(
    action_id INTEGER PRIMARY KEY AUTOINCREMENT,
    rec_id INTEGER REFERENCES recommendations(rec_id),
    step TEXT NOT NULL,
    screenshot_path TEXT,
    api_state_before TEXT,
    api_state_after TEXT,
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rules(
    rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    threshold REAL,
    enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS drafts(
    draft_id TEXT PRIMARY KEY,
    status TEXT,                    -- pre_draft | drafting | complete
    settings_json TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS draft_picks(
    draft_id TEXT NOT NULL,
    pick_no INTEGER NOT NULL,
    round INTEGER,
    draft_slot INTEGER,
    roster_id INTEGER,
    player_id TEXT,
    ts TEXT,
    PRIMARY KEY(draft_id, pick_no)
);
CREATE TABLE IF NOT EXISTS jobs(            -- the approval queue hands consumes
    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
    rec_id INTEGER NOT NULL REFERENCES recommendations(rec_id),
    payload_json TEXT NOT NULL,     -- {rec_id, week, swaps, lineup_hash_expected, expires_at}
    state TEXT NOT NULL DEFAULT 'queued',   -- queued|running|done|failed|expired|aborted
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS devices(
    push_token TEXT PRIMARY KEY,
    platform TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS meta(
    key TEXT PRIMARY KEY,
    value TEXT
);
-- One row per (team, week): the NFL schedule from the team's point of view.
-- team/opponent use Sleeper's code vocabulary (nflverse's LA is stored as LAR).
-- kickoff_utc NULL = time not yet announced. Weather columns are filled only
-- for outdoor home games inside the forecast window; weather_at is the fetch
-- timestamp that drives staleness.
CREATE TABLE IF NOT EXISTS nfl_games(
    season INTEGER NOT NULL,
    week INTEGER NOT NULL,
    team TEXT NOT NULL,
    opponent TEXT,
    is_home INTEGER,
    kickoff_utc TEXT,
    roof TEXT,
    stadium TEXT,
    neutral_site INTEGER DEFAULT 0,
    wind_mph REAL,
    precip_prob REAL,
    temp_f REAL,
    weather_at TEXT,
    spread REAL,          -- team-perspective: positive = this team favored
    total_line REAL,
    implied_total REAL,   -- (total_line + spread)/2 — the market's opponent-strength read
    PRIMARY KEY(season, week, team)
);
-- The wire. One row per published news item, keyed by the feed's own guid so
-- a re-poll is idempotent. player_id is NULL when the name could not be joined
-- confidently — the item still shows under its printed name rather than being
-- dropped (engines/wire.py explains why a guess is worse than a gap).
-- pushed_at is the dedupe for notifications: an item notifies at most once.
CREATE TABLE IF NOT EXISTS news(
    guid TEXT PRIMARY KEY,
    seq INTEGER,                    -- monotonic feed id; drives gap detection
    source TEXT NOT NULL,           -- rotowire | pft
    player_id TEXT,
    name_raw TEXT NOT NULL,
    headline TEXT NOT NULL,
    body TEXT,
    link TEXT,
    severity TEXT NOT NULL,         -- out|doubtful|questionable|practice|role|info
    ailment TEXT,
    departure INTEGER NOT NULL DEFAULT 0,
    published_at TEXT,
    fetched_at TEXT NOT NULL,
    pushed_at TEXT,
    seen_severity TEXT              -- grade at last judgement; see alerts.pending()
);
-- What every player ACTUALLY scored, under this league's own scoring, read
-- from Sleeper's matchup payload. This is the yardstick the projection sources
-- are measured against (engines/calibration.py) — without it every source
-- votes equally forever, however wrong it has been.
CREATE TABLE IF NOT EXISTS player_week_actuals(
    season INTEGER NOT NULL,
    week INTEGER NOT NULL,
    player_id TEXT NOT NULL,
    pts REAL NOT NULL,
    updated_at TEXT,
    PRIMARY KEY(season, week, player_id)
);
CREATE INDEX IF NOT EXISTS news_player ON news(player_id, published_at DESC);
CREATE INDEX IF NOT EXISTS news_pub ON news(published_at DESC);
"""

DEFAULT_RULES: list[tuple[str, float | None]] = [
    ("questionable_near_kickoff", 3.0),   # hours
    ("kickoff_passed", None),             # binding lock: never drive a started slot
    ("weather_flag_on_game", None),
    ("source_disagreement", 0.25),        # relative spread
    ("any_drop_involved", None),
    ("any_faab_involved", None),
]

_local = threading.local()


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _migrate(conn: sqlite3.Connection) -> None:
    """Guarded ALTERs for columns added after first ship."""
    for stmt in ("ALTER TABLE players ADD COLUMN injury_risk REAL",
                 "ALTER TABLE players ADD COLUMN proj_games REAL",
                 "ALTER TABLE nfl_games ADD COLUMN spread REAL",
                 "ALTER TABLE nfl_games ADD COLUMN total_line REAL",
                 "ALTER TABLE nfl_games ADD COLUMN implied_total REAL",
                 "ALTER TABLE players ADD COLUMN practice_status TEXT",
                 "ALTER TABLE players ADD COLUMN report_status TEXT",
                 # WHAT grade an item carried when the alert filter last judged
                 # it. seen_at alone was a one-way latch: a rival's man passed
                 # over as "Questionable" could be re-graded to "on injured
                 # reserve" in place and never be considered again. See
                 # alerts.pending().
                 "ALTER TABLE news ADD COLUMN seen_severity TEXT",
                 # Season record, carried in Sleeper's roster.settings block.
                 "ALTER TABLE rosters ADD COLUMN wins INTEGER NOT NULL DEFAULT 0",
                 "ALTER TABLE rosters ADD COLUMN losses INTEGER NOT NULL DEFAULT 0",
                 "ALTER TABLE rosters ADD COLUMN ties INTEGER NOT NULL DEFAULT 0",
                 "ALTER TABLE rosters ADD COLUMN fpts REAL NOT NULL DEFAULT 0",
                 # Realized weekly score, so the win-probability model can
                 # measure this league's own scoring spread instead of
                 # assuming one (engines/matchup.sigma_from_history).
                 "ALTER TABLE matchups ADD COLUMN points_for REAL",
                 "ALTER TABLE matchups ADD COLUMN matchup_id INTEGER",
                 # The position AS DRAFTED. Historical picks include men who
                 # have since retired off the players table entirely, and
                 # dropping them would bend the room's early-round curve — the
                 # exact part of it that matters (engines/room.py).
                 "ALTER TABLE draft_picks ADD COLUMN pos TEXT",
                 # `pushed_at` was doing double duty — the alert filter stamped
                 # every item it CONSIDERED, including the ones it deliberately
                 # stayed silent about. That made "notified" unreadable and
                 # froze a street item's grade forever. seen_at is the filter's
                 # own mark; pushed_at now means a phone actually rang.
                 "ALTER TABLE news ADD COLUMN seen_at TEXT"):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # already there
    conn.commit()


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """One connection per thread; WAL so the pollers and API can share the file."""
    path = Path(db_path or settings.db_path)
    key = str(path)
    conns = getattr(_local, "conns", None)
    if conns is None:
        conns = _local.conns = {}
    conn = conns.get(key)
    if conn is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conns[key] = conn
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    for name, threshold in DEFAULT_RULES:
        conn.execute(
            "INSERT OR IGNORE INTO rules(name, threshold, enabled) VALUES(?,?,1)",
            (name, threshold),
        )
    conn.commit()
    _migrate(conn)


def meta_get(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def upsert_players(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> None:
    conn.executemany(
        "INSERT INTO players(sleeper_id,name,pos,team,bye,status,injury_status,updated_at) "
        "VALUES(:sleeper_id,:name,:pos,:team,:bye,:status,:injury_status,:updated_at) "
        "ON CONFLICT(sleeper_id) DO UPDATE SET name=excluded.name,pos=excluded.pos,"
        "team=excluded.team,bye=excluded.bye,status=excluded.status,"
        "injury_status=excluded.injury_status,updated_at=excluded.updated_at",
        list(rows),
    )
    conn.commit()


def log_action(
    conn: sqlite3.Connection,
    rec_id: int | None,
    step: str,
    screenshot_path: str | None = None,
    before: Any = None,
    after: Any = None,
) -> None:
    conn.execute(
        "INSERT INTO actions_log(rec_id,step,screenshot_path,api_state_before,api_state_after,ts) "
        "VALUES(?,?,?,?,?,?)",
        (
            rec_id,
            step,
            screenshot_path,
            json.dumps(before) if before is not None else None,
            json.dumps(after) if after is not None else None,
            utcnow(),
        ),
    )
    conn.commit()
