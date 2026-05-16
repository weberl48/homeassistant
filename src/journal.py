"""SQLite-backed journal. Single file, WAL mode, no ORM."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    order_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    strategy TEXT NOT NULL,
    side TEXT NOT NULL,
    notional_usd REAL NOT NULL,
    limit_price REAL NOT NULL,
    status TEXT NOT NULL,
    filled_avg_price REAL,
    filled_qty REAL,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    realized_pnl REAL,
    exit_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_open ON trades(closed_at) WHERE closed_at IS NULL;

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    strategy TEXT NOT NULL,
    symbol TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    fired INTEGER NOT NULL,
    reason TEXT,
    data_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(ts);
CREATE INDEX IF NOT EXISTS idx_signals_latest ON signals(strategy, symbol, ts);

CREATE TABLE IF NOT EXISTS account_snapshots (
    ts TEXT PRIMARY KEY,
    actual_equity REAL NOT NULL,
    managed_equity REAL NOT NULL,
    cash REAL NOT NULL,
    open_position_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS heartbeat (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_run_at TEXT NOT NULL,
    last_job TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS circuit_breaker_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    drawdown_halt_active INTEGER NOT NULL DEFAULT 0,
    all_time_high REAL NOT NULL DEFAULT 0,
    sod_equity REAL NOT NULL DEFAULT 0,
    sod_date TEXT,
    consecutive_losses INTEGER NOT NULL DEFAULT 0,
    last_loss_at TEXT
);

CREATE TABLE IF NOT EXISTS ceiling_alert_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    has_alerted INTEGER NOT NULL DEFAULT 0,
    last_alert_at TEXT
);

CREATE TABLE IF NOT EXISTS day_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL,
    symbol TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_day_trades_date ON day_trades(trade_date);

CREATE TABLE IF NOT EXISTS journal_entries (
    entry_date TEXT PRIMARY KEY,
    markdown TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

INSERT OR IGNORE INTO heartbeat (id, last_run_at, last_job) VALUES (1, datetime('now'), 'init');
INSERT OR IGNORE INTO circuit_breaker_state (id) VALUES (1);
INSERT OR IGNORE INTO ceiling_alert_state (id) VALUES (1);
"""


@dataclass(frozen=True)
class OpenTrade:
    order_id: str
    symbol: str
    strategy: str
    notional_usd: float
    filled_avg_price: float | None
    filled_qty: float | None
    opened_at: datetime


class Journal:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        with self._conn() as c:
            c.executescript(SCHEMA)
            c.execute("PRAGMA journal_mode=WAL")
            # Migration: add data_json column to signals table if it doesn't already exist.
            cols = {r["name"] for r in c.execute("PRAGMA table_info(signals)").fetchall()}
            if "data_json" not in cols:
                c.execute("ALTER TABLE signals ADD COLUMN data_json TEXT")

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # --- heartbeat ---
    def heartbeat(self, job: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE heartbeat SET last_run_at=?, last_job=? WHERE id=1",
                (datetime.utcnow().isoformat() + "Z", job),
            )

    def get_heartbeat(self) -> dict:
        with self._conn() as c:
            row = c.execute("SELECT last_run_at, last_job FROM heartbeat WHERE id=1").fetchone()
            return dict(row) if row else {"last_run_at": None, "last_job": None}

    # --- signals ---
    def write_signal(self, strategy: str, symbol: str, signal_type: str, fired: bool,
                     reason: str, data: dict | None = None) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO signals (ts, strategy, symbol, signal_type, fired, reason, data_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (datetime.utcnow().isoformat() + "Z", strategy, symbol, signal_type,
                 int(fired), reason, json.dumps(data) if data else None),
            )

    def signals_today(self) -> list[dict]:
        today = date.today().isoformat()
        with self._conn() as c:
            rows = c.execute(
                "SELECT ts, strategy, symbol, signal_type, fired, reason, data_json "
                "FROM signals WHERE ts >= ? ORDER BY ts",
                (today,),
            ).fetchall()
            return [dict(r) for r in rows]

    def latest_signals_per_symbol(self, strategy: str) -> list[dict]:
        """Most recent ENTRY signal per (strategy, symbol). Used by the HA dashboard."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT s.ts, s.symbol, s.fired, s.reason, s.data_json "
                "FROM signals s "
                "INNER JOIN (SELECT symbol, MAX(ts) AS max_ts FROM signals "
                "            WHERE strategy=? AND signal_type='entry' GROUP BY symbol) latest "
                "ON s.symbol = latest.symbol AND s.ts = latest.max_ts "
                "WHERE s.strategy=? AND s.signal_type='entry' "
                "ORDER BY s.symbol",
                (strategy, strategy),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                if d.get("data_json"):
                    try:
                        d["data"] = json.loads(d["data_json"])
                    except (ValueError, TypeError):
                        d["data"] = None
                else:
                    d["data"] = None
                d.pop("data_json", None)
                out.append(d)
            return out

    # --- trades ---
    def write_trade(self, order_id: str, symbol: str, strategy: str, side: str,
                    notional_usd: float, limit_price: float, status: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO trades (order_id, symbol, strategy, side, notional_usd, limit_price, status, opened_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (order_id, symbol, strategy, side, notional_usd, limit_price, status, datetime.utcnow().isoformat() + "Z"),
            )

    def update_trade_fill(self, order_id: str, status: str, filled_avg_price: float | None, filled_qty: float | None) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE trades SET status=?, filled_avg_price=?, filled_qty=? WHERE order_id=?",
                (status, filled_avg_price, filled_qty, order_id),
            )

    def close_trade(self, symbol: str, strategy: str, exit_price: float, exit_reason: str) -> float | None:
        """Mark the open trade for (symbol, strategy) as closed; return realized pnl."""
        with self._conn() as c:
            row = c.execute(
                "SELECT order_id, filled_avg_price, filled_qty FROM trades "
                "WHERE symbol=? AND strategy=? AND side='buy' AND closed_at IS NULL "
                "ORDER BY opened_at LIMIT 1",
                (symbol, strategy),
            ).fetchone()
            if not row or row["filled_avg_price"] is None or row["filled_qty"] is None:
                return None
            pnl = (exit_price - row["filled_avg_price"]) * row["filled_qty"]
            c.execute(
                "UPDATE trades SET closed_at=?, realized_pnl=?, exit_reason=? WHERE order_id=?",
                (datetime.utcnow().isoformat() + "Z", pnl, exit_reason, row["order_id"]),
            )
            return pnl

    def list_open_trades(self) -> list[OpenTrade]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT order_id, symbol, strategy, notional_usd, filled_avg_price, filled_qty, opened_at "
                "FROM trades WHERE side='buy' AND closed_at IS NULL"
            ).fetchall()
            return [
                OpenTrade(
                    order_id=r["order_id"],
                    symbol=r["symbol"],
                    strategy=r["strategy"],
                    notional_usd=r["notional_usd"],
                    filled_avg_price=r["filled_avg_price"],
                    filled_qty=r["filled_qty"],
                    opened_at=datetime.fromisoformat(r["opened_at"].replace("Z", "")),
                )
                for r in rows
            ]

    def list_pending_orders(self) -> list[str]:
        """Order IDs for trades whose status is not yet a terminal state."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT order_id FROM trades WHERE status NOT IN ('filled', 'canceled', 'expired', 'rejected')"
            ).fetchall()
            return [r["order_id"] for r in rows]

    # --- snapshots ---
    def snapshot_account(self, actual_equity: float, managed_equity_v: float, cash: float, open_count: int) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO account_snapshots (ts, actual_equity, managed_equity, cash, open_position_count) "
                "VALUES (?, ?, ?, ?, ?)",
                (datetime.utcnow().isoformat() + "Z", actual_equity, managed_equity_v, cash, open_count),
            )

    # --- circuit breaker ---
    def get_circuit_breaker(self) -> dict:
        with self._conn() as c:
            row = c.execute(
                "SELECT drawdown_halt_active, all_time_high, sod_equity, sod_date, consecutive_losses, last_loss_at "
                "FROM circuit_breaker_state WHERE id=1"
            ).fetchone()
            return dict(row) if row else {}

    def set_sod_equity(self, equity: float) -> None:
        today = date.today().isoformat()
        with self._conn() as c:
            row = c.execute("SELECT sod_date FROM circuit_breaker_state WHERE id=1").fetchone()
            if row and row["sod_date"] == today:
                return
            c.execute(
                "UPDATE circuit_breaker_state SET sod_equity=?, sod_date=? WHERE id=1",
                (equity, today),
            )

    def update_ath(self, equity: float) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE circuit_breaker_state SET all_time_high=MAX(all_time_high, ?) WHERE id=1",
                (equity,),
            )

    def trip_drawdown_halt(self) -> None:
        with self._conn() as c:
            c.execute("UPDATE circuit_breaker_state SET drawdown_halt_active=1 WHERE id=1")

    def record_trade_outcome(self, was_loss: bool) -> None:
        with self._conn() as c:
            if was_loss:
                c.execute(
                    "UPDATE circuit_breaker_state SET consecutive_losses=consecutive_losses+1, last_loss_at=? WHERE id=1",
                    (datetime.utcnow().isoformat() + "Z",),
                )
            else:
                c.execute("UPDATE circuit_breaker_state SET consecutive_losses=0 WHERE id=1")

    # --- ceiling alert ---
    def get_ceiling_alert(self) -> dict:
        with self._conn() as c:
            row = c.execute("SELECT has_alerted, last_alert_at FROM ceiling_alert_state WHERE id=1").fetchone()
            return dict(row) if row else {"has_alerted": 0, "last_alert_at": None}

    def set_ceiling_alert(self, has_alerted: bool, last_alert_at: datetime | None) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE ceiling_alert_state SET has_alerted=?, last_alert_at=? WHERE id=1",
                (int(has_alerted), last_alert_at.isoformat() + "Z" if last_alert_at else None),
            )

    # --- aggregations for weekly commentary ---
    def closed_trades_since(self, days: int) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT symbol, strategy, side, notional_usd, filled_avg_price, filled_qty, "
                "opened_at, closed_at, realized_pnl, exit_reason "
                "FROM trades WHERE closed_at IS NOT NULL AND closed_at >= datetime('now', ?) "
                "ORDER BY closed_at",
                (f"-{int(days)} days",),
            ).fetchall()
            return [dict(r) for r in rows]

    def recent_markdowns(self, days: int) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT entry_date, markdown FROM journal_entries "
                "WHERE entry_date >= date('now', ?) ORDER BY entry_date",
                (f"-{int(days)} days",),
            ).fetchall()
            return [dict(r) for r in rows]

    # --- daily journal markdown ---
    def get_today_markdown(self) -> str | None:
        today = date.today().isoformat()
        with self._conn() as c:
            row = c.execute("SELECT markdown FROM journal_entries WHERE entry_date=?", (today,)).fetchone()
            return row["markdown"] if row else None

    def set_today_markdown(self, md: str) -> None:
        today = date.today().isoformat()
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO journal_entries (entry_date, markdown, updated_at) VALUES (?, ?, ?)",
                (today, md, datetime.utcnow().isoformat() + "Z"),
            )
