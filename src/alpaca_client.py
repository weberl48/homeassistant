"""Thin wrapper over alpaca-py. Routes to paper or live based on Config."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.requests import (
    CryptoBarsRequest,
    CryptoLatestQuoteRequest,
    StockBarsRequest,
    StockLatestQuoteRequest,
)
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest

from . import config


@dataclass(frozen=True)
class Account:
    equity: float
    cash: float
    buying_power: float
    portfolio_value: float
    pattern_day_trader: bool
    account_number: str
    status: str


@dataclass(frozen=True)
class Bar:
    t: datetime
    o: float
    h: float
    l: float
    c: float
    v: float


class AlpacaClient:
    def __init__(self, cfg: config.Config) -> None:
        self.cfg = cfg
        self.trading = TradingClient(cfg.alpaca_key_id, cfg.alpaca_secret, paper=not cfg.is_live)
        self.data = StockHistoricalDataClient(cfg.alpaca_key_id, cfg.alpaca_secret)
        # Crypto data is free / unauthenticated on Alpaca's basic data tier.
        # Passing keys is harmless and lets the rate-limit budget attach to our account.
        self.crypto_data = CryptoHistoricalDataClient(cfg.alpaca_key_id, cfg.alpaca_secret)

    def account(self) -> Account:
        a = self.trading.get_account()
        return Account(
            equity=float(a.equity),
            cash=float(a.cash),
            buying_power=float(a.buying_power),
            portfolio_value=float(a.portfolio_value),
            pattern_day_trader=bool(a.pattern_day_trader),
            account_number=a.account_number,
            status=str(a.status),
        )

    def clock(self) -> dict:
        c = self.trading.get_clock()
        return {
            "is_open": c.is_open,
            "next_open": c.next_open.isoformat(),
            "next_close": c.next_close.isoformat(),
            "timestamp": c.timestamp.isoformat(),
        }

    def positions(self) -> list[dict]:
        out = []
        for p in self.trading.get_all_positions():
            sym = p.symbol
            # Alpaca's positions endpoint returns crypto without the slash ("BTCUSD"),
            # but orders + storage use slash format ("BTC/USD"). Normalize so downstream
            # matching by symbol works consistently.
            if "/" not in sym and len(sym) >= 6 and sym.endswith("USD"):
                base = sym[:-3]
                # Heuristic: real crypto tickers are 3-5 chars; this catches BTC, ETH, LTC, SOL etc.
                # without false-positive matching on e.g. some hypothetical equity ending in USD.
                if 2 <= len(base) <= 5 and base.isalpha():
                    sym = f"{base}/USD"
            out.append({
                "symbol": sym,
                "qty": float(p.qty),
                "avg_entry_price": float(p.avg_entry_price),
                "current_price": float(p.current_price) if p.current_price else None,
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
            })
        return out

    def daily_bars(self, symbol: str, limit: int = 60) -> list[Bar]:
        # Explicit start date — without it Alpaca returns only the most recent bar.
        # 120 calendar days = comfortably more than 60 trading days even with holidays.
        # end=yesterday so we only see closed bars (today's bar would be partial during market hours).
        end = datetime.now(timezone.utc) - timedelta(days=1)
        start = end - timedelta(days=120)
        req = StockBarsRequest(
            symbol_or_symbols=symbol, timeframe=TimeFrame.Day,
            start=start, end=end, limit=limit,
        )
        resp = self.data.get_stock_bars(req)
        return [Bar(t=b.timestamp, o=float(b.open), h=float(b.high), l=float(b.low), c=float(b.close), v=float(b.volume)) for b in resp[symbol]]

    def latest_ask(self, symbol: str) -> float:
        req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
        q = self.data.get_stock_latest_quote(req)[symbol]
        return float(q.ask_price)

    def place_limit(self, symbol: str, notional_usd: float, side: str, limit_price: float) -> dict:
        order = LimitOrderRequest(
            symbol=symbol,
            notional=round(notional_usd, 2),  # fractional via notional, not qty
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            limit_price=round(limit_price, 2),
        )
        o = self.trading.submit_order(order)
        status = o.status.value if hasattr(o.status, "value") else str(o.status)
        return {"id": str(o.id), "symbol": o.symbol, "status": status}

    # ---- Crypto ----
    # Symbol format: "BTC/USD", "ETH/USD" (slash required for orders + data requests).

    def crypto_hourly_bars(self, symbol: str, hours: int = 240) -> list[Bar]:
        # 24h buffer covers any provider-side latency on the very latest bar.
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=hours + 24)
        req = CryptoBarsRequest(
            symbol_or_symbols=symbol, timeframe=TimeFrame.Hour,
            start=start, end=end,
        )
        resp = self.crypto_data.get_crypto_bars(req)
        return [Bar(t=b.timestamp, o=float(b.open), h=float(b.high), l=float(b.low),
                    c=float(b.close), v=float(b.volume)) for b in resp[symbol]]

    def latest_crypto_ask(self, symbol: str) -> float:
        req = CryptoLatestQuoteRequest(symbol_or_symbols=symbol)
        q = self.crypto_data.get_crypto_latest_quote(req)[symbol]
        return float(q.ask_price)

    def place_crypto_limit(self, symbol: str, notional_usd: float, side: str, limit_price: float) -> dict:
        # Crypto requires GTC (Good-Til-Cancelled) — DAY tif is not accepted.
        order = LimitOrderRequest(
            symbol=symbol,
            notional=round(notional_usd, 2),
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.GTC,
            limit_price=round(limit_price, 2),
        )
        o = self.trading.submit_order(order)
        status = o.status.value if hasattr(o.status, "value") else str(o.status)
        return {"id": str(o.id), "symbol": o.symbol, "status": status}

    def place_crypto_sell_qty(self, symbol: str, qty: float, limit_price: float) -> dict:
        # Crypto SELLs must specify qty (the coin amount), not notional, to fully exit.
        order = LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.GTC,
            limit_price=round(limit_price, 2),
        )
        o = self.trading.submit_order(order)
        status = o.status.value if hasattr(o.status, "value") else str(o.status)
        return {"id": str(o.id), "symbol": o.symbol, "status": status}
