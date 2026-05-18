"""APScheduler jobs. BackgroundScheduler in America/New_York timezone."""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import config, llm_commentary, risk
from .alpaca_client import AlpacaClient
from .journal import Journal
from .strategies import crypto_mean_reversion as cmr
from .strategies import mean_reversion as mr

log = logging.getLogger("scheduler")
ET = ZoneInfo("America/New_York")


def _build_account_state(
    client: AlpacaClient,
    journal: Journal,
    cfg: config.Config,
) -> tuple[risk.AccountState, float]:
    """Snapshot everything risk.evaluate_order needs, plus return actual_equity for logging."""
    acct = client.account()
    cb = journal.get_circuit_breaker()
    open_trades = journal.list_open_trades()

    journal.set_sod_equity(acct.equity)
    journal.update_ath(acct.equity)

    positions = []
    for ot in open_trades:
        if ot.filled_avg_price is None or ot.filled_qty is None:
            continue
        cost_basis = ot.filled_avg_price * ot.filled_qty
        positions.append(risk.Position(
            symbol=ot.symbol,
            strategy=risk.Strategy(ot.strategy),
            qty=ot.filled_qty,
            cost_basis=cost_basis,
            opened_at=ot.opened_at,
        ))

    allowlist: set[str] = set(mr.WATCHLIST)
    if cfg.strategy_crypto_enabled:
        allowlist |= set(cmr.WATCHLIST)

    state = risk.AccountState(
        actual_equity=acct.equity,
        cash=acct.cash,
        sod_equity=cb.get("sod_equity") or acct.equity,
        all_time_high_equity=cb.get("all_time_high") or acct.equity,
        open_positions=positions,
        day_trade_dates=[],  # TODO: populate from day_trades table once same-day closes are tracked
        consecutive_losses=cb.get("consecutive_losses") or 0,
        last_loss_at=datetime.fromisoformat(cb["last_loss_at"].replace("Z", "")) if cb.get("last_loss_at") else None,
        drawdown_halt_active=bool(cb.get("drawdown_halt_active")),
        symbol_allowlist=frozenset(allowlist),
    )
    return state, acct.equity


def build_scheduler(
    cfg: config.Config,
    client: AlpacaClient,
    journal: Journal,
) -> tuple[BackgroundScheduler, dict]:
    scheduler = BackgroundScheduler(timezone=ET)

    def _safe(job_name: str, fn):
        """Wrap a job: log entry/exit, persist heartbeat, never let exceptions kill the scheduler."""
        def wrapped():
            log.info("[%s] start", job_name)
            try:
                fn()
            except Exception:
                log.exception("[%s] failed", job_name)
            finally:
                journal.heartbeat(job_name)
                log.info("[%s] done", job_name)
        return wrapped

    def market_open_check():
        clock = client.clock()
        log.info("market open=%s next_open=%s", clock["is_open"], clock["next_open"])

    def pre_session_signals():
        if not cfg.strategy_mean_reversion_enabled:
            log.info("mean_reversion disabled, skipping")
            return
        held = {ot.symbol for ot in journal.list_open_trades() if ot.strategy == "mean_reversion"}
        signals = mr.compute_entries(client, excluded_symbols=held)
        for s in signals:
            data = {
                "rsi": round(s.rsi, 2),
                "ema": round(s.ema, 2),
                "atr": round(s.atr, 4),
                "last_close": round(s.last_close, 2),
                "stretch_threshold": round(s.ema - mr.ENTRY_STRETCH_ATR_MULT * s.atr, 2),
                "entry_rsi_threshold": mr.ENTRY_RSI_THRESHOLD,
            }
            journal.write_signal("mean_reversion", s.symbol, "entry", s.fired, s.reason, data=data)
            log.info("signal %s entry fired=%s | %s", s.symbol, s.fired, s.reason)

    def trading_session():
        if not cfg.strategy_mean_reversion_enabled:
            return
        held = {ot.symbol for ot in journal.list_open_trades() if ot.strategy == "mean_reversion"}
        entries = [s for s in mr.compute_entries(client, excluded_symbols=held) if s.fired]
        if not entries:
            log.info("no entry signals")
            return
        state, _ = _build_account_state(client, journal, cfg)
        for s in entries:
            intent = risk.OrderIntent(
                symbol=s.symbol,
                strategy=risk.Strategy.MEAN_REVERSION,
                side="buy",
                notional_usd=s.notional_usd,
                limit_price=s.suggested_limit,
                ask_price=s.suggested_limit / (1 + mr.LIMIT_PREMIUM_OVER_ASK),
            )
            decision = risk.evaluate_order(intent, state, cfg.managed_equity_ceiling_usd)
            if not decision.allow:
                journal.write_signal("mean_reversion", s.symbol, "entry_rejected", False, decision.reason)
                log.warning("REJECT %s: %s", s.symbol, decision.reason)
                continue
            try:
                order = client.place_limit(s.symbol, s.notional_usd, "buy", s.suggested_limit)
                journal.write_trade(
                    order_id=order["id"], symbol=s.symbol, strategy="mean_reversion",
                    side="buy", notional_usd=s.notional_usd, limit_price=s.suggested_limit,
                    status=order["status"],
                )
                log.info("PLACED buy %s notional=$%.2f limit=%.2f order_id=%s",
                         s.symbol, s.notional_usd, s.suggested_limit, order["id"])
            except Exception:
                log.exception("order placement failed for %s", s.symbol)

    def position_monitor():
        # Reconcile pending order statuses
        for order_id in journal.list_pending_orders():
            try:
                o = client.trading.get_order_by_id(order_id)
                journal.update_trade_fill(
                    order_id=order_id,
                    status=str(o.status.value if hasattr(o.status, "value") else o.status),
                    filled_avg_price=float(o.filled_avg_price) if o.filled_avg_price else None,
                    filled_qty=float(o.filled_qty) if o.filled_qty else None,
                )
            except Exception:
                log.exception("order reconcile failed for %s", order_id)

        # Check exits
        open_trades = journal.list_open_trades()
        if not open_trades:
            return
        exits = [e for e in mr.compute_exits(client, open_trades) if e.fired]
        for e in exits:
            journal.write_signal("mean_reversion", e.symbol, "exit", True, e.reason)
            try:
                ask = client.latest_ask(e.symbol)
                limit = round(ask * (1 - mr.LIMIT_PREMIUM_OVER_ASK), 2)
                # Match the position's notional for the SELL
                ot = next((o for o in open_trades if o.symbol == e.symbol and o.strategy == "mean_reversion"), None)
                if not ot or ot.filled_qty is None:
                    continue
                # Sell by qty, not notional, to fully exit the position
                from alpaca.trading.requests import LimitOrderRequest
                from alpaca.trading.enums import OrderSide, TimeInForce
                req = LimitOrderRequest(
                    symbol=e.symbol, qty=ot.filled_qty,
                    side=OrderSide.SELL, time_in_force=TimeInForce.DAY,
                    limit_price=limit,
                )
                o = client.trading.submit_order(req)
                pnl = journal.close_trade(e.symbol, "mean_reversion", e.last_close, e.reason)
                journal.record_trade_outcome(was_loss=(pnl is not None and pnl < 0))
                log.info("EXIT %s qty=%.4f limit=%.2f pnl=%s reason=%s",
                         e.symbol, ot.filled_qty, limit, f"${pnl:.2f}" if pnl else "?", e.reason)
            except Exception:
                log.exception("exit failed for %s", e.symbol)

    def pre_close_exit():
        # Force-close any mean-rev position held >= EXIT_MAX_HOLD_DAYS regardless of price
        # (already handled by compute_exits during regular monitor; this is the safety net at 15:55)
        position_monitor()

    def eod_journal():
        acct = client.account()
        m_eq = risk.managed_equity(acct.equity, cfg.managed_equity_ceiling_usd)
        positions = client.positions()
        journal.snapshot_account(acct.equity, m_eq, acct.cash, len(positions))

        # Drawdown halt check
        cb = journal.get_circuit_breaker()
        if risk.evaluate_drawdown_halt(acct.equity, cb.get("all_time_high") or 0):
            journal.trip_drawdown_halt()
            log.warning("DRAWDOWN HALT TRIPPED at equity=$%.2f ath=$%.2f",
                        acct.equity, cb.get("all_time_high") or 0)

        # Structured stub first — guaranteed even if LLM call fails
        sigs = journal.signals_today()
        md = f"# {datetime.now(ET).date()}\n\n"
        md += f"**Equity:** ${acct.equity:,.2f} (managed: ${m_eq:.2f})\n"
        md += f"**Cash:** ${acct.cash:,.2f}\n"
        md += f"**Open positions:** {len(positions)}\n\n"
        md += f"## Signals today ({len(sigs)})\n"
        for s in sigs:
            md += f"- `{s['ts']}` {s['strategy']}/{s['symbol']} {s['signal_type']} fired={s['fired']} — {s['reason']}\n"

        # Haiku commentary — best-effort, failure must not lose the stub
        if cfg.anthropic_api_key:
            try:
                commentary = llm_commentary.generate_daily(cfg.anthropic_api_key, journal, acct, m_eq)
                md += "\n\n## Commentary\n\n" + commentary
            except Exception:
                log.exception("LLM daily commentary failed; keeping structured stub only")
                md += "\n\n_Commentary unavailable — see logs._\n"

        journal.set_today_markdown(md)

    def _check_crypto_exits() -> int:
        """Check + execute exits on held crypto positions.

        Returns the number of crypto positions that were open at scan time. Shared between
        the full crypto_scan (every 30m) and the lightweight crypto_position_monitor (every 5m).

        IMPORTANT: sell qty is read from Alpaca's live position (not our stored filled_qty)
        because Alpaca's order.filled_qty reports the GROSS amount including their crypto fee,
        while the actual position holds only the NET amount. Submitting the gross value triggers
        "insufficient balance" rejections that leave the position stuck in a retry loop.
        """
        open_trades = journal.list_open_trades()
        crypto_opens = [ot for ot in open_trades if ot.strategy == "crypto_mean_reversion"]
        if not crypto_opens:
            return 0

        exits = [e for e in cmr.compute_exits(client, crypto_opens) if e.fired]
        if not exits:
            return len(crypto_opens)

        # Snapshot Alpaca's live positions once for all exits this cycle.
        try:
            live_qty = {p["symbol"]: float(p["qty"]) for p in client.positions()}
        except Exception:
            log.exception("crypto exit: failed to fetch live positions, skipping cycle")
            return len(crypto_opens)

        for e in exits:
            journal.write_signal("crypto_mean_reversion", e.symbol, "exit", True, e.reason)
            try:
                actual_qty = live_qty.get(e.symbol)
                if actual_qty is None or actual_qty <= 0:
                    # Position already gone (manual close, prior exit fill, etc.) — sync DB and move on
                    log.warning("crypto exit %s: no live position; marking closed in DB", e.symbol)
                    journal.close_trade(e.symbol, "crypto_mean_reversion", e.last_close,
                                        e.reason + " [no_live_position]")
                    continue
                ask = client.latest_crypto_ask(e.symbol)
                limit = round(ask * (1 - cmr.LIMIT_PREMIUM_OVER_ASK), 2)
                client.place_crypto_sell_qty(e.symbol, actual_qty, limit)
                pnl = journal.close_trade(e.symbol, "crypto_mean_reversion", e.last_close, e.reason)
                journal.record_trade_outcome(was_loss=(pnl is not None and pnl < 0))
                log.info("CRYPTO EXIT %s qty=%.8f limit=%.2f pnl=%s reason=%s",
                         e.symbol, actual_qty, limit, f"${pnl:.2f}" if pnl else "?", e.reason)
            except Exception:
                log.exception("crypto exit failed for %s", e.symbol)
        return len(crypto_opens)

    def crypto_position_monitor():
        """Lightweight 5-min job: exit-only check on open crypto positions.
        Skips entirely if no positions open — no API calls wasted polling empty state.
        Complements the every-30-min crypto_scan by tightening stop-loss reaction time."""
        if not cfg.strategy_crypto_enabled:
            return
        n = _check_crypto_exits()
        if n == 0:
            return  # silent skip — most invocations land here when no positions are open
        log.info("crypto_position_monitor: checked exits on %d position(s)", n)

    def crypto_scan():
        """Full 24/7 crypto job: reconcile pending crypto orders, check exits, compute + place entries.
        Runs every 30 min around the clock — crypto markets never close."""
        if not cfg.strategy_crypto_enabled:
            return

        # 1) Reconcile any pending crypto orders (status: new -> filled/etc).
        #    Filter by strategy via the trades table since list_pending_orders returns all.
        open_trades_all = journal.list_open_trades()
        pending_crypto_ids = {
            ot.order_id for ot in open_trades_all
            if ot.strategy == "crypto_mean_reversion" and ot.filled_avg_price is None
        }
        pending_now = set(journal.list_pending_orders()) & pending_crypto_ids
        for order_id in pending_now:
            try:
                o = client.trading.get_order_by_id(order_id)
                journal.update_trade_fill(
                    order_id=order_id,
                    status=str(o.status.value if hasattr(o.status, "value") else o.status),
                    filled_avg_price=float(o.filled_avg_price) if o.filled_avg_price else None,
                    filled_qty=float(o.filled_qty) if o.filled_qty else None,
                )
            except Exception:
                log.exception("crypto order reconcile failed for %s", order_id)

        # 2) Check exits on held crypto positions (shared with crypto_position_monitor).
        _check_crypto_exits()

        # 3) Compute + log entry signals (one row per symbol, always written for visibility).
        held = {ot.symbol for ot in crypto_opens}
        signals = cmr.compute_entries(client, excluded_symbols=held)
        for s in signals:
            data = {
                "rsi": round(s.rsi, 2),
                "ema": round(s.ema, 2),
                "atr": round(s.atr, 4),
                "last_close": round(s.last_close, 2),
                "stretch_threshold": round(s.ema - cmr.ENTRY_STRETCH_ATR_MULT * s.atr, 2),
                "entry_rsi_threshold": cmr.ENTRY_RSI_THRESHOLD,
            }
            journal.write_signal("crypto_mean_reversion", s.symbol, "entry", s.fired, s.reason, data=data)
            log.info("crypto signal %s entry fired=%s | %s", s.symbol, s.fired, s.reason)

        # 4) Place orders for fired entries through the risk gates.
        entries = [s for s in signals if s.fired]
        if not entries:
            return
        state, _ = _build_account_state(client, journal, cfg)
        for s in entries:
            intent = risk.OrderIntent(
                symbol=s.symbol,
                strategy=risk.Strategy.CRYPTO_MEAN_REVERSION,
                side="buy",
                notional_usd=s.notional_usd,
                limit_price=s.suggested_limit,
                ask_price=s.suggested_limit / (1 + cmr.LIMIT_PREMIUM_OVER_ASK),
            )
            decision = risk.evaluate_order(intent, state, cfg.managed_equity_ceiling_usd)
            if not decision.allow:
                journal.write_signal("crypto_mean_reversion", s.symbol, "entry_rejected", False, decision.reason)
                log.warning("REJECT crypto %s: %s", s.symbol, decision.reason)
                continue
            try:
                order = client.place_crypto_limit(s.symbol, s.notional_usd, "buy", s.suggested_limit)
                journal.write_trade(
                    order_id=order["id"], symbol=s.symbol, strategy="crypto_mean_reversion",
                    side="buy", notional_usd=s.notional_usd, limit_price=s.suggested_limit,
                    status=order["status"],
                )
                log.info("PLACED crypto buy %s notional=$%.2f limit=%.2f order_id=%s",
                         s.symbol, s.notional_usd, s.suggested_limit, order["id"])
            except Exception:
                log.exception("crypto order placement failed for %s", s.symbol)

    def weekly_review():
        if not cfg.anthropic_api_key:
            log.info("no anthropic key, skipping weekly review")
            return
        try:
            commentary = llm_commentary.generate_weekly(cfg.anthropic_api_key, journal, days=7)
        except Exception:
            log.exception("weekly commentary failed")
            return
        if not commentary:
            return
        # Replace today's markdown with the weekly review (Sunday slot)
        existing = journal.get_today_markdown() or ""
        md = f"# Weekly review — {datetime.now(ET).date()}\n\n{commentary}"
        if existing:
            md += "\n\n---\n\n" + existing
        journal.set_today_markdown(md)

    jobs = {
        "market_open_check": _safe("market_open_check", market_open_check),
        "pre_session_signals": _safe("pre_session_signals", pre_session_signals),
        "trading_session": _safe("trading_session", trading_session),
        "position_monitor": _safe("position_monitor", position_monitor),
        "pre_close_exit": _safe("pre_close_exit", pre_close_exit),
        "eod_journal": _safe("eod_journal", eod_journal),
        "weekly_review": _safe("weekly_review", weekly_review),
        "crypto_scan": _safe("crypto_scan", crypto_scan),
        "crypto_position_monitor": _safe("crypto_position_monitor", crypto_position_monitor),
    }

    scheduler.add_job(jobs["market_open_check"], CronTrigger(day_of_week="mon-fri", hour=9, minute=30, timezone=ET))
    scheduler.add_job(jobs["pre_session_signals"], CronTrigger(day_of_week="mon-fri", hour=9, minute=35, timezone=ET))
    scheduler.add_job(jobs["trading_session"], CronTrigger(day_of_week="mon-fri", hour=10, minute=0, timezone=ET))
    scheduler.add_job(jobs["position_monitor"], CronTrigger(day_of_week="mon-fri", hour="10-15", minute="*/30", timezone=ET))
    scheduler.add_job(jobs["pre_close_exit"], CronTrigger(day_of_week="mon-fri", hour=15, minute=55, timezone=ET))
    scheduler.add_job(jobs["eod_journal"], CronTrigger(day_of_week="mon-fri", hour=16, minute=15, timezone=ET))
    scheduler.add_job(jobs["weekly_review"], CronTrigger(day_of_week="sun", hour=18, minute=0, timezone=ET))
    # Crypto: 24/7. crypto_scan does the full cycle (reconcile + exits + entries) every 30m;
    # crypto_position_monitor does exit-only checks every 5m to tighten stop-loss reaction
    # on crypto's volatility. The 5m job self-skips when no positions are open.
    if cfg.strategy_crypto_enabled:
        scheduler.add_job(jobs["crypto_scan"], CronTrigger(minute="*/30", timezone=ET))
        scheduler.add_job(jobs["crypto_position_monitor"], CronTrigger(minute="*/5", timezone=ET))

    return scheduler, jobs
