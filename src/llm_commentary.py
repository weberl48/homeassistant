"""Claude Haiku 4.5 commentary on journal data. Pure prose generator.

NEVER in the trade decision loop — caller passes in already-computed structured data,
LLM returns markdown text. If it fails, caller keeps the structured stub.
"""
from __future__ import annotations

import logging
from datetime import date

from anthropic import Anthropic

from .alpaca_client import Account
from .journal import Journal

log = logging.getLogger("llm_commentary")

MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS_DAILY = 600
MAX_TOKENS_WEEKLY = 1200

SYSTEM_PROMPT = """You write commentary for an autonomous Alpaca paper trading bot.

The bot:
- Manages a $200 account (per-trade capped at $45 mean-reversion / $22.50 political-copy).
- Mean-reversion strategy on SPY/QQQ/IWM: entry when RSI(2)<10 AND close < EMA(20) - 1.5*ATR(14). Exit on EMA touch, -3% stop, or 5 trading days.
- Political copy-trading on Quiver disclosures: currently disabled.
- Risk gates: bucket caps, daily-loss halt at -3% intraday, drawdown halt at -8% from ATH, 24h cooloff after 3 consecutive losers.

Your job: write 2-3 short markdown paragraphs analyzing the data the user provides. Be terse, factual, and pattern-focused. Match the tone of a quant analyst writing a desk note: short sentences, specifics over generalizations, no marketing language, no exclamation points. Do NOT recommend trades or strategy changes — the bot is deterministic, you are commenting only.

If a signal didn't fire, say why concretely (RSI was X, not <10). If positions are open, note their P/L progress. If nothing happened, say so plainly without padding."""


def _format_daily_input(journal: Journal, acct: Account, managed_eq: float) -> str:
    sigs = journal.signals_today()
    open_trades = journal.list_open_trades()
    cb = journal.get_circuit_breaker()
    closed = journal.closed_trades_since(1)

    lines = [
        f"# Day in review — {date.today().isoformat()}",
        "",
        "## Account",
        f"- Actual equity: ${acct.equity:,.2f}",
        f"- Managed equity (ceiling-clamped): ${managed_eq:.2f}",
        f"- Cash: ${acct.cash:,.2f}",
        f"- ATH equity: ${cb.get('all_time_high') or 0:,.2f}",
        f"- Consecutive losses: {cb.get('consecutive_losses') or 0}",
        "",
        f"## Signals computed today ({len(sigs)})",
    ]
    if not sigs:
        lines.append("(none — non-trading day or scheduler did not fire)")
    else:
        for s in sigs:
            mark = "[FIRED]" if s["fired"] else "[no]"
            lines.append(f"- {s['strategy']}/{s['symbol']} {s['signal_type']} {mark} — {s['reason']}")

    lines.append("")
    lines.append(f"## Trades closed today ({len(closed)})")
    if not closed:
        lines.append("(none)")
    else:
        for t in closed:
            pnl = t.get("realized_pnl")
            pnl_str = f"${pnl:+.2f}" if pnl is not None else "?"
            lines.append(f"- {t['symbol']} ({t['strategy']}) {pnl_str} — {t.get('exit_reason') or '?'}")

    lines.append("")
    lines.append(f"## Open positions ({len(open_trades)})")
    if not open_trades:
        lines.append("(none)")
    else:
        for ot in open_trades:
            avg = ot.filled_avg_price
            lines.append(
                f"- {ot.symbol} ({ot.strategy}) opened {ot.opened_at.date().isoformat()} "
                f"@ ${avg:.2f}" if avg else f"- {ot.symbol} ({ot.strategy}) opened {ot.opened_at.date().isoformat()}, fill pending"
            )

    return "\n".join(lines)


def _format_weekly_input(journal: Journal, days: int) -> str:
    entries = journal.recent_markdowns(days)
    closed = journal.closed_trades_since(days)
    if not entries and not closed:
        return ""

    lines = [f"# Weekly review — {days} trading days back", ""]
    if closed:
        wins = [t for t in closed if (t.get("realized_pnl") or 0) > 0]
        losses = [t for t in closed if (t.get("realized_pnl") or 0) < 0]
        total_pnl = sum(t.get("realized_pnl") or 0 for t in closed)
        lines.extend([
            "## Aggregate stats",
            f"- Trades closed: {len(closed)} ({len(wins)} winners, {len(losses)} losers)",
            f"- Realized P&L: ${total_pnl:+.2f}",
            f"- Avg win: ${sum(t['realized_pnl'] for t in wins)/len(wins):+.2f}" if wins else "- Avg win: n/a",
            f"- Avg loss: ${sum(t['realized_pnl'] for t in losses)/len(losses):+.2f}" if losses else "- Avg loss: n/a",
            "",
        ])
    lines.append("## Daily entries this week")
    for e in entries:
        lines.append(f"\n### {e['entry_date']}\n{e['markdown']}")
    return "\n".join(lines)


def generate_daily(api_key: str, journal: Journal, acct: Account, managed_eq: float) -> str:
    user_content = _format_daily_input(journal, acct, managed_eq)
    client = Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS_DAILY,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    text = "".join(b.text for b in msg.content if hasattr(b, "text"))
    log.info("daily commentary: in=%d out=%d tokens", msg.usage.input_tokens, msg.usage.output_tokens)
    return text


def generate_weekly(api_key: str, journal: Journal, days: int = 7) -> str | None:
    user_content = _format_weekly_input(journal, days)
    if not user_content:
        log.info("weekly: no data for last %d days, skipping", days)
        return None
    client = Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS_WEEKLY,
        system=SYSTEM_PROMPT + "\n\nThis is a WEEKLY review. Synthesize patterns across the daily entries — hit rate, what kept signals from firing, drawdown behavior, anything systematic. One page max.",
        messages=[{"role": "user", "content": user_content}],
    )
    text = "".join(b.text for b in msg.content if hasattr(b, "text"))
    log.info("weekly commentary: in=%d out=%d tokens", msg.usage.input_tokens, msg.usage.output_tokens)
    return text
