"""Risk gates. Pure functions over (intent, state) -> Decision.

All sizing math uses managed_equity = min(actual_equity, ceiling), never actual_equity.
That's how the bot stays bounded to the experiment's $200 even if the account grows.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum


class Strategy(str, Enum):
    MEAN_REVERSION = "mean_reversion"
    POLITICAL_COPY = "political_copy"
    CRYPTO_MEAN_REVERSION = "crypto_mean_reversion"


# Bucket caps stated in absolute dollars on a $200-ceiling account.
# When the ceiling is raised, scale these proportionally in raise_ceiling logic — not here.
# Strategies B (political) and C (crypto) share the same $45 slot — if both are ever
# enabled simultaneously the caller is responsible for halving them or the global
# DEPLOYMENT_FRACTION_CAP will bind.
BUCKET_CAP_USD: dict[Strategy, float] = {
    Strategy.MEAN_REVERSION: 135.0,
    Strategy.POLITICAL_COPY: 45.0,
    Strategy.CRYPTO_MEAN_REVERSION: 45.0,
}
PER_ORDER_CAP_USD: dict[Strategy, float] = {
    Strategy.MEAN_REVERSION: 45.0,
    Strategy.POLITICAL_COPY: 22.50,
    Strategy.CRYPTO_MEAN_REVERSION: 22.50,
}
MAX_CONCURRENT_POSITIONS: dict[Strategy, int] = {
    Strategy.MEAN_REVERSION: 3,
    Strategy.POLITICAL_COPY: 2,
    Strategy.CRYPTO_MEAN_REVERSION: 2,
}

# Global fail-safe: keep at least 10% cash. With bucket caps summing to $180 on a $200
# ceiling this is rarely the binding constraint, but it catches misconfiguration.
DEPLOYMENT_FRACTION_CAP = 0.90

# Limit-price gate: never pay more than this fraction above ask. Crypto needs more
# slack because off-hours spreads on BTC/ETH can widen meaningfully vs RTH equity spreads.
LIMIT_PRICE_MAX_PREMIUM: dict[Strategy, float] = {
    Strategy.MEAN_REVERSION: 0.002,         # 0.2% — tight ETF spreads
    Strategy.POLITICAL_COPY: 0.002,
    Strategy.CRYPTO_MEAN_REVERSION: 0.010,  # 1.0% — wider crypto spreads
}

# Circuit-breaker thresholds.
DAILY_LOSS_HALT_PCT = 0.03   # halt new entries if down >3% on the day
DRAWDOWN_HALT_PCT = 0.08     # halt indefinitely if down >8% from all-time high
CONSECUTIVE_LOSS_HALT = 3    # halt 24h after 3 losers in a row

# Ceiling-alert hysteresis dead-band (5% below ceiling — wide enough that boundary
# chop like $195->$205->$198->$203 fires exactly one alert, not two).
CEILING_RESET_FRACTION = 0.95

# PDT rule: more than 3 day-trades in a rolling 5-business-day window flips the account
# into Pattern Day Trader status, which requires $25k equity. Refuse the 4th.
PDT_MAX_DAY_TRADES = 3
PDT_WINDOW_DAYS = 5


@dataclass(frozen=True)
class Position:
    symbol: str
    strategy: Strategy
    qty: float
    cost_basis: float  # total dollars deployed at entry
    opened_at: datetime


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    strategy: Strategy
    side: str  # "buy" or "sell"
    notional_usd: float
    limit_price: float
    ask_price: float


@dataclass(frozen=True)
class AccountState:
    actual_equity: float
    cash: float
    sod_equity: float           # start-of-day equity, for the daily-loss circuit
    all_time_high_equity: float
    open_positions: list[Position]
    day_trade_dates: list[date]  # dates of round-trip closes in last 5 business days
    consecutive_losses: int
    last_loss_at: datetime | None
    drawdown_halt_active: bool   # set true once tripped; manual reset via flag file
    symbol_allowlist: frozenset[str]


@dataclass(frozen=True)
class Decision:
    allow: bool
    reason: str


def managed_equity(actual: float, ceiling: float) -> float:
    return min(actual, ceiling)


def evaluate_order(intent: OrderIntent, state: AccountState, ceiling_usd: float) -> Decision:
    """Run all per-order gates. Returns the first failure, or allow."""
    # Hard mode failures first (drawdown halt, allowlist, market-order ban).
    if state.drawdown_halt_active:
        return Decision(False, "drawdown circuit breaker active (manual reset required)")

    if intent.symbol not in state.symbol_allowlist:
        return Decision(False, f"symbol {intent.symbol} not in allowlist")

    max_premium = LIMIT_PRICE_MAX_PREMIUM[intent.strategy]
    if intent.limit_price > intent.ask_price * (1 + max_premium):
        return Decision(
            False,
            f"limit {intent.limit_price:.4f} > {max_premium*100:.2f}% above ask {intent.ask_price:.4f}",
        )

    # Daily loss gate (existing exits still allowed; only blocks BUY).
    if intent.side == "buy":
        daily_loss_pct = (state.sod_equity - state.actual_equity) / state.sod_equity
        if daily_loss_pct > DAILY_LOSS_HALT_PCT:
            return Decision(
                False,
                f"daily loss {daily_loss_pct*100:.2f}% exceeds {DAILY_LOSS_HALT_PCT*100:.0f}% halt",
            )

    # Consecutive-loss gate (24h cooloff).
    if (
        intent.side == "buy"
        and state.consecutive_losses >= CONSECUTIVE_LOSS_HALT
        and state.last_loss_at is not None
        and datetime.utcnow() - state.last_loss_at < timedelta(hours=24)
    ):
        return Decision(
            False,
            f"{state.consecutive_losses} consecutive losses; 24h cooloff",
        )

    # Per-order cap.
    if intent.notional_usd > PER_ORDER_CAP_USD[intent.strategy]:
        return Decision(
            False,
            f"order ${intent.notional_usd:.2f} exceeds per-order cap "
            f"${PER_ORDER_CAP_USD[intent.strategy]:.2f} for {intent.strategy.value}",
        )

    # Bucket cap (per-strategy exposure).
    bucket_used = sum(p.cost_basis for p in state.open_positions if p.strategy == intent.strategy)
    if intent.side == "buy" and bucket_used + intent.notional_usd > BUCKET_CAP_USD[intent.strategy]:
        return Decision(
            False,
            f"{intent.strategy.value} bucket would be ${bucket_used + intent.notional_usd:.2f}, "
            f"cap ${BUCKET_CAP_USD[intent.strategy]:.2f}",
        )

    # Concurrent positions cap.
    if intent.side == "buy":
        open_in_strategy = sum(1 for p in state.open_positions if p.strategy == intent.strategy)
        if open_in_strategy >= MAX_CONCURRENT_POSITIONS[intent.strategy]:
            return Decision(
                False,
                f"{intent.strategy.value} already at max {MAX_CONCURRENT_POSITIONS[intent.strategy]} positions",
            )

    # Global 90% deployment fail-safe (against managed_equity, not actual).
    if intent.side == "buy":
        m_eq = managed_equity(state.actual_equity, ceiling_usd)
        total_invested = sum(p.cost_basis for p in state.open_positions)
        if total_invested + intent.notional_usd > m_eq * DEPLOYMENT_FRACTION_CAP:
            return Decision(
                False,
                f"deployment ${total_invested + intent.notional_usd:.2f} would exceed "
                f"{DEPLOYMENT_FRACTION_CAP*100:.0f}% of managed equity ${m_eq:.2f}",
            )

    # PDT: count day-trades in rolling 5-business-day window. A new BUY that opens a position
    # we'd close same-day doesn't count yet — but if we've already used our 3, we refuse to
    # take an entry that COULD become the 4th if exited intraday. Crypto is exempt — the PDT
    # rule applies only to securities, not crypto.
    if intent.side == "buy" and intent.strategy != Strategy.CRYPTO_MEAN_REVERSION:
        cutoff = date.today() - timedelta(days=PDT_WINDOW_DAYS)
        recent = [d for d in state.day_trade_dates if d > cutoff]
        if len(recent) >= PDT_MAX_DAY_TRADES:
            return Decision(False, f"PDT: {len(recent)} day-trades in last {PDT_WINDOW_DAYS}d, would risk 4th")

    return Decision(True, "ok")


@dataclass(frozen=True)
class CeilingAlertState:
    has_alerted_above_ceiling: bool
    last_alert_at: datetime | None


def evaluate_ceiling_alert(
    actual_equity: float,
    ceiling_usd: float,
    state: CeilingAlertState,
    now: datetime,
    weekly_reminder_hours: int = 168,
) -> tuple[bool, CeilingAlertState]:
    """Returns (should_fire_now, new_state).

    Hysteresis:
      - Arm + fire when actual > ceiling AND flag is False.
      - Reset flag when actual < ceiling * 0.95 (5% dead-band).
      - Weekly reminder while flag is True and last alert was >168h ago.
    """
    if actual_equity < ceiling_usd * CEILING_RESET_FRACTION:
        return False, CeilingAlertState(has_alerted_above_ceiling=False, last_alert_at=state.last_alert_at)

    above_ceiling = actual_equity > ceiling_usd

    if above_ceiling and not state.has_alerted_above_ceiling:
        return True, CeilingAlertState(has_alerted_above_ceiling=True, last_alert_at=now)

    if (
        state.has_alerted_above_ceiling
        and state.last_alert_at is not None
        and now - state.last_alert_at >= timedelta(hours=weekly_reminder_hours)
    ):
        return True, CeilingAlertState(has_alerted_above_ceiling=True, last_alert_at=now)

    return False, state


def evaluate_drawdown_halt(actual_equity: float, all_time_high: float) -> bool:
    """Returns True if drawdown from ATH exceeds 8% — caller must persist halt + alert."""
    if all_time_high <= 0:
        return False
    return (all_time_high - actual_equity) / all_time_high > DRAWDOWN_HALT_PCT
