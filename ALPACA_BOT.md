# alpaca-bot

Deterministic Python trading bot for a small Alpaca account ($200 ceiling by default), deployed as a Docker container on a Raspberry Pi running Home Assistant OS. Full setup walkthrough in [`SETUP_GUIDE.md`](./SETUP_GUIDE.md).

## What it does

- **Strategy A — ETF mean-reversion** ($135 bucket, 75% of deployable): RSI(2) on SPY/QQQ/IWM with an ATR-from-EMA stretch filter. Daily bars, hold up to 5 trading days, exit on EMA touch / -3% stop. Runs M-F 09:35–16:15 ET.
- **Strategy C — Crypto mean-reversion** ($45 bucket, 25%, off by default): same statistical edge retuned for hourly bars on BTC/USD + ETH/USD — RSI(2) < 15, EMA-1.0×ATR stretch, -5% stop, 24h max hold. Runs 24/7 every 30 min.
- **LLM commentary** (Claude Haiku, 1 call/day, optional): writes journal commentary. Never makes trade decisions.
- **HA integration**: REST sensor + dashboard card + phone alerts on entries, exits, circuit-breaker trips, and stale heartbeats.

Three layers of risk gates (per-order validation → daily/drawdown/consecutive-loss circuit breaker → mode + capital ceiling) bracket the experiment within a known capital range even in live mode.

## Quick start (local dev)

```
uv sync
cp .env.example .env  # fill in ALPACA_API_KEY_ID + ALPACA_API_SECRET_KEY
uv run uvicorn src.main:app --reload
```

Visit http://localhost:8000/healthz to confirm the Alpaca connection works.

## Deploy to a Raspberry Pi running HAOS

See [`SETUP_GUIDE.md`](./SETUP_GUIDE.md) for the full walkthrough — covers the HA SSH addon setup, source push via `scp`, Docker build, runtime mounts, HA dashboard wiring, phone notifications, and the paper → live flip checklist.

TL;DR:

```
# on the Pi (via HA SSH addon with Protection mode OFF)
mkdir -p <DATA_HOST>/{data,logs}
touch <DATA_HOST>/data/.paper-only

# scp the source over, then
docker build -t alpaca-bot:latest .
docker run -d --name alpaca-bot --restart unless-stopped \
  -p 9700:8000 \
  -v <DATA_HOST>/data:/data -v <DATA_HOST>/logs:/logs \
  --env-file <DATA_HOST>/.env \
  alpaca-bot:latest

# verify
curl http://<PI_IP>:9700/healthz
```

## Live-mode safety

Flipping from paper to live requires **two intentional actions** — either alone is not enough:

1. Edit `.env`: `ALPACA_MODE=live` and replace API keys with live ones
2. Delete the sentinel: `rm <DATA_HOST>/data/.paper-only`
3. Restart the container

If either is missing, the container refuses to start in live mode.

A `MANAGED_EQUITY_CEILING_USD` env var caps the equity figure all sizing math sees — even if the account grows beyond the ceiling, the bot continues to size as though it were still at the cap. Excess sits as untouched cash.

## Architecture

```
HA dashboard ──HTTP──> /status (FastAPI)
                          │
              ┌───────────┴───────────┐
              │   APScheduler jobs    │
              │  (M-F sessions +      │
              │   24/7 crypto scan)   │
              └───────────┬───────────┘
                          ▼
         ┌─────────┬──────────┬──────────┐
         │ risk.py │ alpaca-py│ journal  │
         │ (gates) │ (orders) │ (SQLite) │
         └─────────┴──────────┴──────────┘
                          │
                          ▼
                 Alpaca paper/live API
```

## License

MIT (see source files).
