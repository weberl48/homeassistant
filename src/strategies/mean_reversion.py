"""RSI(2) + ATR-from-EMA mean-reversion on liquid ETFs.

Pure functions over OHLC arrays + an Alpaca client passed in by the scheduler.
No I/O state — the caller persists signals + trades.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from ..alpaca_client import AlpacaClient
    from ..journal import OpenTrade


WATCHLIST: list[str] = ["SPY", "QQQ", "IWM"]
POSITION_SIZE_USD: float = 45.0       # matches risk.PER_ORDER_CAP_USD[MEAN_REVERSION]
ENTRY_RSI_THRESHOLD: float = 10.0
ENTRY_STRETCH_ATR_MULT: float = 1.5
EXIT_STOP_LOSS_PCT: float = 0.03
EXIT_MAX_HOLD_DAYS: int = 5
RSI_PERIOD: int = 2
EMA_PERIOD: int = 20
ATR_PERIOD: int = 14
LIMIT_PREMIUM_OVER_ASK: float = 0.001  # within risk.LIMIT_PRICE_MAX_PREMIUM (0.002)


@dataclass(frozen=True)
class EntrySignal:
    symbol: str
    fired: bool
    reason: str
    rsi: float
    ema: float
    atr: float
    last_close: float
    notional_usd: float
    suggested_limit: float


@dataclass(frozen=True)
class ExitSignal:
    symbol: str
    fired: bool
    reason: str
    last_close: float


def _rsi(closes: np.ndarray, period: int) -> float:
    deltas = np.diff(closes[-period - 1:])
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = gains.mean()
    avg_loss = losses.mean()
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return float(100 - 100 / (1 + rs))


def _ema(closes: np.ndarray, period: int) -> float:
    alpha = 2.0 / (period + 1)
    e = float(closes[0])
    for c in closes[1:]:
        e = alpha * float(c) + (1 - alpha) * e
    return e


def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int) -> float:
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    if len(trs) < period:
        return float(np.mean(trs)) if trs else 0.0
    return float(np.mean(trs[-period:]))


def _bars_to_arrays(bars) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    closes = np.array([b.c for b in bars], dtype=np.float64)
    highs = np.array([b.h for b in bars], dtype=np.float64)
    lows = np.array([b.l for b in bars], dtype=np.float64)
    return highs, lows, closes


def compute_entries(client: "AlpacaClient", excluded_symbols: set[str]) -> list[EntrySignal]:
    """One signal per watchlist symbol. Excluded = symbols we already hold."""
    signals = []
    for symbol in WATCHLIST:
        if symbol in excluded_symbols:
            continue
        bars = client.daily_bars(symbol, limit=60)
        if len(bars) < EMA_PERIOD + 1:
            signals.append(EntrySignal(symbol, False, f"insufficient bars ({len(bars)})", 0, 0, 0, 0, 0, 0))
            continue
        highs, lows, closes = _bars_to_arrays(bars)
        rsi = _rsi(closes, RSI_PERIOD)
        ema = _ema(closes, EMA_PERIOD)
        atr = _atr(highs, lows, closes, ATR_PERIOD)
        last = float(closes[-1])
        stretch_threshold = ema - ENTRY_STRETCH_ATR_MULT * atr

        cond_rsi = rsi < ENTRY_RSI_THRESHOLD
        cond_stretch = last < stretch_threshold

        if cond_rsi and cond_stretch:
            ask = client.latest_ask(symbol)
            limit = round(ask * (1 + LIMIT_PREMIUM_OVER_ASK), 2)
            signals.append(EntrySignal(
                symbol=symbol, fired=True,
                reason=f"rsi(2)={rsi:.1f}<{ENTRY_RSI_THRESHOLD} AND close={last:.2f}<ema-{ENTRY_STRETCH_ATR_MULT}*atr={stretch_threshold:.2f}",
                rsi=rsi, ema=ema, atr=atr, last_close=last,
                notional_usd=POSITION_SIZE_USD, suggested_limit=limit,
            ))
        else:
            why_not = []
            if not cond_rsi:
                why_not.append(f"rsi(2)={rsi:.1f}>={ENTRY_RSI_THRESHOLD}")
            if not cond_stretch:
                why_not.append(f"close={last:.2f}>=stretch={stretch_threshold:.2f}")
            signals.append(EntrySignal(
                symbol=symbol, fired=False, reason="; ".join(why_not),
                rsi=rsi, ema=ema, atr=atr, last_close=last,
                notional_usd=0, suggested_limit=0,
            ))
    return signals


def compute_exits(client: "AlpacaClient", open_trades: list["OpenTrade"]) -> list[ExitSignal]:
    """For each mean-rev open position: exit on EMA touch / -3% / >5 days held."""
    exits = []
    for ot in open_trades:
        if ot.strategy != "mean_reversion":
            continue
        if ot.filled_avg_price is None:
            continue
        bars = client.daily_bars(ot.symbol, limit=30)
        if not bars:
            continue
        closes = np.array([b.c for b in bars], dtype=np.float64)
        last = float(closes[-1])
        ema = _ema(closes, EMA_PERIOD) if len(closes) >= EMA_PERIOD else last
        pnl_pct = (last - ot.filled_avg_price) / ot.filled_avg_price
        days_held = (date.today() - ot.opened_at.date()).days

        if last >= ema:
            exits.append(ExitSignal(ot.symbol, True, f"ema_touch: close={last:.2f}>=ema={ema:.2f}", last))
        elif pnl_pct <= -EXIT_STOP_LOSS_PCT:
            exits.append(ExitSignal(ot.symbol, True, f"stop_loss: pnl={pnl_pct*100:.2f}%", last))
        elif days_held >= EXIT_MAX_HOLD_DAYS:
            exits.append(ExitSignal(ot.symbol, True, f"max_hold: held {days_held} trading days", last))
        else:
            exits.append(ExitSignal(
                ot.symbol, False,
                f"hold: pnl={pnl_pct*100:.2f}% ema_dist={(ema-last)/last*100:.2f}% days={days_held}",
                last,
            ))
    return exits
