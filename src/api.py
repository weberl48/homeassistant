from __future__ import annotations

from datetime import datetime
from typing import Callable

from fastapi import FastAPI, HTTPException

from . import config, risk
from .alpaca_client import AlpacaClient
from .journal import Journal


def build_app(
    cfg: config.Config,
    client: AlpacaClient,
    journal: Journal,
    jobs: dict[str, Callable[[], None]],
    lifespan=None,
) -> FastAPI:
    app = FastAPI(title="alpaca-bot", version="0.2.0", lifespan=lifespan)

    def _account_status_value(s) -> str:
        return s.value if hasattr(s, "value") else str(s).split(".")[-1]

    @app.get("/healthz")
    def healthz():
        try:
            acct = client.account()
        except Exception as e:
            raise HTTPException(503, f"alpaca unreachable: {e}")
        return {
            "ok": True,
            "mode": cfg.alpaca_mode,
            "account_number": acct.account_number,
            "status": _account_status_value(acct.status),
            "actual_equity": acct.equity,
            "managed_equity": risk.managed_equity(acct.equity, cfg.managed_equity_ceiling_usd),
            "ceiling_usd": cfg.managed_equity_ceiling_usd,
            "now": datetime.utcnow().isoformat() + "Z",
        }

    @app.get("/status")
    def status():
        acct = client.account()
        positions = client.positions()
        clock = client.clock()
        cb = journal.get_circuit_breaker()
        hb = journal.get_heartbeat()
        breaker = "ok"
        if cb.get("drawdown_halt_active"):
            breaker = "halted_drawdown"
        elif cb.get("sod_equity") and (cb["sod_equity"] - acct.equity) / cb["sod_equity"] > risk.DAILY_LOSS_HALT_PCT:
            breaker = "halted_daily"
        elif (cb.get("consecutive_losses") or 0) >= risk.CONSECUTIVE_LOSS_HALT:
            breaker = "halted_consecutive_losses"

        latest_signals = journal.latest_signals_per_symbol("mean_reversion")
        latest_signals_crypto = journal.latest_signals_per_symbol("crypto_mean_reversion")
        closed_today = journal.closed_trades_since(1)
        realized_today = sum((t.get("realized_pnl") or 0) for t in closed_today)
        unrealized = sum((p.get("unrealized_pl") or 0) for p in positions)
        journal_md = journal.get_today_markdown() or ""
        # Cap markdown in attributes to keep HA recorder happy
        if len(journal_md) > 8000:
            journal_md = journal_md[:8000] + "\n\n_(truncated)_"

        return {
            "mode": cfg.alpaca_mode,
            "account_status": _account_status_value(acct.status),
            "actual_equity": acct.equity,
            "managed_equity": risk.managed_equity(acct.equity, cfg.managed_equity_ceiling_usd),
            "cash": acct.cash,
            "open_positions": positions,
            "open_position_count": len(positions),
            "market_open": clock["is_open"],
            "strategies": {
                "mean_reversion": cfg.strategy_mean_reversion_enabled,
                "political_copy": cfg.strategy_political_enabled,
                "crypto_mean_reversion": cfg.strategy_crypto_enabled,
            },
            "circuit_breaker": breaker,
            "consecutive_losses": cb.get("consecutive_losses") or 0,
            "all_time_high": cb.get("all_time_high") or 0,
            "last_run_at": hb.get("last_run_at"),
            "last_job": hb.get("last_job"),
            "realized_pnl_today": round(realized_today, 2),
            "unrealized_pnl": round(unrealized, 2),
            "total_pnl_today": round(realized_today + unrealized, 2),
            "closed_trades_today_count": len(closed_today),
            "latest_signals": latest_signals,
            "latest_signals_crypto": latest_signals_crypto,
            "journal_today_md": journal_md,
        }

    @app.get("/config")
    def get_config():
        return {
            "mode": cfg.alpaca_mode,
            "ceiling_usd": cfg.managed_equity_ceiling_usd,
            "strategies": {
                "mean_reversion": cfg.strategy_mean_reversion_enabled,
                "political_copy": cfg.strategy_political_enabled,
                "crypto_mean_reversion": cfg.strategy_crypto_enabled,
            },
            "bucket_caps": {k.value: v for k, v in risk.BUCKET_CAP_USD.items()},
            "per_order_caps": {k.value: v for k, v in risk.PER_ORDER_CAP_USD.items()},
            "max_concurrent": {k.value: v for k, v in risk.MAX_CONCURRENT_POSITIONS.items()},
        }

    @app.get("/journal/today")
    def journal_today():
        md = journal.get_today_markdown()
        return {"date": datetime.utcnow().date().isoformat(), "markdown": md or "(no entry yet)"}

    @app.get("/signals/today")
    def signals_today():
        return {"signals": journal.signals_today()}

    @app.post("/trigger/{job_name}")
    def trigger(job_name: str):
        # On-demand job invocation. Useful for testing outside market hours.
        # Paper mode only — refuse in live to prevent fat-finger.
        if cfg.is_live:
            raise HTTPException(403, "trigger endpoint disabled in live mode")
        if job_name not in jobs:
            raise HTTPException(404, f"unknown job; available: {list(jobs.keys())}")
        jobs[job_name]()
        return {"ok": True, "job": job_name, "ran_at": datetime.utcnow().isoformat() + "Z"}

    return app
