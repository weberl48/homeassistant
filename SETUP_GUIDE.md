# Alpaca Algo Trading Bot — Setup Guide

Build a deterministic mean-reversion trading bot that runs as a Docker container on a Raspberry Pi alongside Home Assistant, trades stocks (M-F) and crypto (24/7) on Alpaca paper, displays its state on an HA dashboard, and alerts your phone on key events. End-to-end: $200 live capital ceiling, ~$0.50/month in LLM costs, fully reversible until you flip a sentinel file.

---

## What you're building

```
┌─────────────────────────────────────────────────┐
│  Layer 3: HA integration                         │
│   - REST sensor polls bot every 60s              │
│   - Dashboard card: equity, P&L, signals, positions
│   - Automation: notify phone on entries/halts    │
└─────────────────────────────────────────────────┘
                       ▲ HTTP
┌─────────────────────────────────────────────────┐
│  Layer 2: LLM commentary (1 call/day, optional)  │
│   - Claude Haiku writes EOD/weekly journal entries
│   - NEVER in the trade decision loop             │
└─────────────────────────────────────────────────┘
                       ▲ reads journal DB
┌─────────────────────────────────────────────────┐
│  Layer 1: Deterministic Python (no LLM)          │
│   - APScheduler cron: pre-market, session, EOD   │
│   - Strategies: ETF mean-rev (M-F) + crypto (24/7)
│   - Risk gates: per-order + circuit breaker + ceiling
│   - SQLite journal for trades, signals, heartbeat │
└─────────────────────────────────────────────────┘
                       ▲ alpaca-py SDK
                  Alpaca paper/live API
```

**Capital split** (on a $200 ceiling):
- $135 (75%) — ETF mean-reversion (SPY/QQQ/IWM), max $45 per position, 3 concurrent
- $45 (25%) — Crypto mean-reversion (BTC/USD, ETH/USD), max $22.50 per position, 2 concurrent
- $20 (10%) — cash reserve, enforced by global deployment gate

---

## Prerequisites

| What | Why |
|------|-----|
| Raspberry Pi 4 or 5 running Home Assistant OS | The bot runs as a Docker container on the Pi alongside HA |
| Alpaca account (paper at minimum, live optional) | Free, no funding needed for paper |
| Anthropic API key (optional) | $0.50/mo for daily Haiku commentary in the journal; bot works without it |
| HA Companion app on your phone | For push notifications |
| Frenck "Advanced SSH & Web Terminal" HA addon | Gives Docker socket access; the built-in SSH addon won't work |
| ~$200 in live Alpaca account (optional) | Only needed if/when you flip to live mode after paper validation |

---

## Phase 0 — Accounts + API keys

### Alpaca

1. Sign up at https://alpaca.markets — pick "Individual brokerage" if you ever want to flip to live; "Paper only" works for paper-forever.
2. Go to https://app.alpaca.markets/paper/dashboard/overview
3. Click "Generate API Keys" (paper). Save the **Key ID** and **Secret Key** — you can't view the secret again, only regenerate.
4. Paper accounts come pre-funded with $100k of fake money. Your live account stays at $0 until you wire money — do this only after paper validation (Phase 6).

### Anthropic (optional)

1. https://console.anthropic.com → API Keys → Create Key
2. Add $5–10 in billing credit. Daily commentary uses Claude Haiku 4.5 at ~$0.02/day.
3. If you skip this, the bot writes a structured journal stub without prose commentary.

---

## Phase 1 — Pi setup

### Install the SSH addon (the right one)

1. HA UI → Settings → Add-ons → Add-on Store → search "**Advanced SSH & Web Terminal**" (by Frenck, *not* the official "Terminal & SSH" — that one can't access the Docker socket).
2. Install. Before starting, open Configuration:
   - Set a username + password OR (better) paste an SSH public key in `authorized_keys`
   - Save
3. Open the addon page → toggle **Protection mode OFF**. This is what grants Docker socket access. (Yes, HA will warn you; that's the trade-off for running custom containers on HAOS.)
4. Start the addon.

### Find your Pi's IP

HA UI → Settings → System → Network. Note both the wired and wireless IPs. **Use the wired IP** (mDNS does not resolve reliably to HAOS). Throughout this guide, replace `<PI_IP>` with that address.

### Set up SSH access from your dev machine

```bash
ssh -p 22222 root@<PI_IP>
# or with cipher requirement if the addon enforces it:
ssh -p 22222 -c aes256-gcm@openssh.com root@<PI_IP>
```

Add an entry to `~/.ssh/config` to alias it:

```
Host alpaca-pi
  HostName <PI_IP>
  User root
  Port 22222
  Ciphers aes256-gcm@openssh.com
  IdentityFile ~/.ssh/id_ed25519
```

Verify Docker socket access:

```bash
ssh alpaca-pi "docker ps"
```

You should see the HA Core containers listed. If you get "permission denied", Protection mode is still on — go back and toggle it.

---

## Phase 2 — Source code

Get the source onto your dev machine. The repo layout:

```
alpaca-bot/
├── Dockerfile
├── pyproject.toml
├── .env.example
├── README.md
└── src/
    ├── main.py                 # FastAPI app + APScheduler bootstrap
    ├── config.py               # env var parsing, mode/sentinel checks
    ├── alpaca_client.py        # thin wrapper over alpaca-py (stocks + crypto)
    ├── risk.py                 # per-order + circuit-breaker gates
    ├── journal.py              # SQLite writes + markdown rendering
    ├── llm_commentary.py       # daily/weekly Haiku calls
    ├── api.py                  # /status, /journal/today, /healthz, /config
    ├── scheduler.py            # APScheduler job definitions
    └── strategies/
        ├── mean_reversion.py            # SPY/QQQ/IWM, daily bars, RSI(2)<10
        └── crypto_mean_reversion.py     # BTC/USD + ETH/USD, hourly bars, RSI(2)<15
```

If you're starting from scratch, see `pyproject.toml` for the dep list (`alpaca-py`, `fastapi`, `uvicorn`, `apscheduler`, `anthropic`, `numpy`). The Dockerfile is a plain `python:3.12-slim` base + `uv pip install`.

### Customize for your account

Open `src/risk.py` and confirm these caps match what you want on a $200 ceiling:

```python
BUCKET_CAP_USD = {
    Strategy.MEAN_REVERSION: 135.0,
    Strategy.CRYPTO_MEAN_REVERSION: 45.0,
}
PER_ORDER_CAP_USD = {
    Strategy.MEAN_REVERSION: 45.0,           # $45 × 3 ETFs = $135 bucket cap
    Strategy.CRYPTO_MEAN_REVERSION: 22.50,   # $22.50 × 2 coins = $45 bucket cap
}
DEPLOYMENT_FRACTION_CAP = 0.90               # always keep ≥10% cash
```

If you're working with a different ceiling (e.g. $500), scale these proportionally before the first run.

---

## Phase 3 — Deploy the container

### Decide where data lives

The container writes its SQLite DB and logs to a host directory. Two reasonable choices:

- **Main Pi disk** (default): `/root/alpaca-bot-data/`
- **External USB drive** (if you have one mounted): `/mnt/data/supervisor/media/<your-label>/alpaca/`

The guide uses `<DATA_HOST>` as the placeholder.

### Push the source to the Pi

From your dev machine:

```bash
# Make build context dir on the Pi
ssh alpaca-pi "mkdir -p /root/alpaca-bot"

# Copy everything (excluding .env which we'll handle separately)
scp -r Dockerfile pyproject.toml src/ alpaca-pi:/root/alpaca-bot/
```

### Create the data dirs + paper-only sentinel

```bash
ssh alpaca-pi "mkdir -p <DATA_HOST>/{data,logs} && touch <DATA_HOST>/data/.paper-only"
```

The `.paper-only` file is a safety sentinel: even if you accidentally set `ALPACA_MODE=live`, the container refuses to boot until you also delete this file. Two-step intentional friction.

### Create `.env` on the Pi

```bash
ssh alpaca-pi "cat > <DATA_HOST>/.env" <<'EOF'
ALPACA_MODE=paper
ALPACA_API_KEY_ID=<your_paper_key_id>
ALPACA_API_SECRET_KEY=<your_paper_secret>

ANTHROPIC_API_KEY=<your_anthropic_key_or_empty>

MANAGED_EQUITY_CEILING_USD=200
STRATEGY_MEAN_REVERSION_ENABLED=true
STRATEGY_POLITICAL_ENABLED=false
STRATEGY_CRYPTO_ENABLED=false

DATA_DIR=/data
LOG_DIR=/logs
EOF
```

Leave `STRATEGY_CRYPTO_ENABLED=false` for now — enable it in Phase 7 after the ETF side is validated.

### Build the image

```bash
ssh alpaca-pi "cd /root/alpaca-bot && docker build -t alpaca-bot:latest ."
```

First build takes 2-5 min on a Pi 5 (slower on Pi 4) — `uv pip install` is the longest step.

### Run the container

```bash
ssh alpaca-pi "docker run -d --name alpaca-bot --restart unless-stopped \
  -p 9700:8000 \
  -v <DATA_HOST>/data:/data \
  -v <DATA_HOST>/logs:/logs \
  --env-file <DATA_HOST>/.env \
  alpaca-bot:latest"
```

### Verify

```bash
ssh alpaca-pi "curl -s http://localhost:9700/healthz"
```

You should see something like:

```json
{
  "ok": true,
  "mode": "paper",
  "account_number": "PA3IHEH4Q6YV",
  "status": "ACTIVE",
  "actual_equity": 99999.99,
  "managed_equity": 200.0,
  "ceiling_usd": 200.0,
  "now": "2026-05-16T15:05:00.391390Z"
}
```

The `PA` prefix on the account number means paper. `managed_equity: 200.0` means the bot is treating the account as a $200 experiment regardless of the $100k of paper funds.

Check the scheduler is alive:

```bash
ssh alpaca-pi "docker logs --tail 20 alpaca-bot"
```

Look for `scheduler started with 7 jobs` (or 8 if you enable crypto).

---

## Phase 4 — HA integration

### Enable packages (if not already)

In `/config/configuration.yaml`, make sure you have:

```yaml
homeassistant:
  packages: !include_dir_named packages
```

And the dashboards section can pick up YAML-mode dashboards:

```yaml
lovelace:
  mode: storage   # keeps your existing UI-edited dashboards
  dashboards:
    dashboard-alpaca:
      mode: yaml
      title: Alpaca Bot
      icon: mdi:chart-line
      show_in_sidebar: true
      filename: dashboards/alpaca.yaml
```

### Drop in the package

Copy this repo's `_alpaca_bot.yaml` to `/config/packages/alpaca_bot.yaml`. **Edit the IP** — find every occurrence of `192.168.1.160` and replace with your `<PI_IP>`.

Key things the package creates:
- `sensor.alpaca_status_raw` — REST sensor polling `http://<PI_IP>:9700/status` every 60s
- Derived template sensors: `sensor.alpaca_equity`, `sensor.alpaca_pnl_today`, `sensor.alpaca_open_positions`, etc.
- `binary_sensor.alpaca_market_open` and `binary_sensor.alpaca_crypto_enabled`
- 5 automations: drawdown halt, daily-loss halt, consecutive-loss cooloff, heartbeat-stale, position-change

### Drop in the dashboard

Copy this repo's `_dashboard_alpaca.yaml` to `/config/dashboards/alpaca.yaml`.

### Restart HA Core

Adding `template:`, `rest:`, `binary_sensor:`, and `automation:` blocks via a new package requires a full HA Core restart. From SSH:

```bash
ssh alpaca-pi "curl -s -X POST -H 'Authorization: Bearer \$SUPERVISOR_TOKEN' \
  http://supervisor/core/restart"
```

Or HA UI → Developer Tools → YAML → "Restart" button.

After restart, the sidebar should have a new **Alpaca Bot** entry. Click it — you'll see the hero card with equity + mode badge, the Today P&L row, the Signal scan table (empty until first scan), and the Open positions table (empty until first fill).

---

## Phase 5 — Phone notifications

### Pair your phone with HA Companion

1. Install the HA Companion app (iOS or Android)
2. Sign in to your HA instance
3. Allow notifications when prompted

### Find your notify entity

In HA → Developer Tools → Services → search "notify" — you'll see one called `notify.<your_device>` (e.g. `notify.sm_s926u` for a Samsung Galaxy S26, `notify.mobile_app_iphone` for iPhone).

### Update the package

In `/config/packages/alpaca_bot.yaml`, find every occurrence of `notify.sm_s926u` and replace with your entity ID. There are 5 automations to update.

After editing:

```bash
ssh alpaca-pi "curl -s -X POST -H 'Authorization: Bearer \$SUPERVISOR_TOKEN' \
  http://supervisor/core/api/services/automation/reload"
```

### Test it

Fire one of the automations directly to confirm delivery:

```bash
ssh alpaca-pi "curl -s -X POST -H 'Authorization: Bearer \$SUPERVISOR_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{\"entity_id\":\"automation.alpaca_drawdown_circuit_breaker_tripped\",\"skip_condition\":true}' \
  http://supervisor/core/api/services/automation/trigger"
```

You should get a phone notification within a few seconds titled "🛑 Alpaca: drawdown halt". If not, double-check the `notify.<your_device>` entity name and confirm the Companion app has notification permissions.

---

## Phase 6 — Validation + (optional) live flip

### Paper validation period

Let the bot run on paper for **2-4 weeks minimum** before considering live mode. During this time:

- The ETF strategy fires `pre_session_signals` at 09:35 ET on weekdays. Most days no entries fire (RSI(2)<10 is a rare condition by design). Don't worry if a week goes by with no trades.
- The `eod_journal` job at 16:15 ET writes a markdown summary including Haiku commentary (if `ANTHROPIC_API_KEY` is set). View today's journal at `http://<PI_IP>:9700/journal/today`.
- Heartbeat alerts will fire if the container dies — install Watchtower or just rely on Docker's `restart unless-stopped` policy.

### Trigger jobs manually (paper only)

While in paper mode, you can manually fire any scheduled job for testing:

```bash
ssh alpaca-pi "curl -s -X POST http://localhost:9700/trigger/pre_session_signals"
ssh alpaca-pi "curl -s -X POST http://localhost:9700/trigger/eod_journal"
```

Available job names: `market_open_check`, `pre_session_signals`, `trading_session`, `position_monitor`, `pre_close_exit`, `eod_journal`, `weekly_review`, `crypto_scan`.

This endpoint is **disabled in live mode** to prevent fat-finger trades.

### Live-flip checklist

Only proceed if:
- ☐ Paper P&L over the validation window is roughly ≥ -2% (i.e., the strategy didn't blow up)
- ☐ You've read the most recent weekly Haiku review and the patterns make sense
- ☐ You've taken a backup of the SQLite DB: `ssh alpaca-pi "cp <DATA_HOST>/data/db.sqlite <DATA_HOST>/data/db.sqlite.bak.preflip"`
- ☐ Your live Alpaca account is funded with the amount you're willing to lose (i.e., $200)
- ☐ You've reviewed `risk.py` one more time and you're comfortable with the caps

To flip:

```bash
# 1. Generate live API keys at https://app.alpaca.markets/brokerage/dashboard/overview
# 2. Edit the .env on the Pi:
ssh alpaca-pi "sed -i 's/ALPACA_MODE=paper/ALPACA_MODE=live/' <DATA_HOST>/.env"
ssh alpaca-pi "sed -i 's/ALPACA_API_KEY_ID=.*/ALPACA_API_KEY_ID=<your_live_key>/' <DATA_HOST>/.env"
ssh alpaca-pi "sed -i 's/ALPACA_API_SECRET_KEY=.*/ALPACA_API_SECRET_KEY=<your_live_secret>/' <DATA_HOST>/.env"

# 3. Delete the sentinel (second friction step)
ssh alpaca-pi "rm <DATA_HOST>/data/.paper-only"

# 4. Restart the container
ssh alpaca-pi "docker restart alpaca-bot"

# 5. Verify
ssh alpaca-pi "curl -s http://localhost:9700/healthz"
# Should show "mode": "live" and your real account number (no PA prefix)
```

If the container refuses to start with `Refusing to start: ALPACA_MODE=live but ... .paper-only still exists`, you forgot step 3. That's the intended behavior.

---

## Phase 7 — Crypto strategy (optional, 24/7)

Once the ETF side has been stable in paper for 2-4 weeks, you can enable the crypto module. It uses the same statistical edge (RSI(2) oversold + price stretched below EMA) with crypto-appropriate parameters:

| Parameter | ETF (Strategy A) | Crypto (Strategy C) |
|-----------|------------------|---------------------|
| Bar timeframe | 1 day | 1 hour |
| RSI(2) entry threshold | < 10 | < 15 |
| Stretch (× ATR below EMA) | 1.5× | 1.0× |
| Stop loss | -3% | -5% |
| Max hold | 5 trading days | 24 hours |
| Per-position size | $45 | $22.50 |
| Max concurrent | 3 | 2 |
| Schedule | M-F session-aligned | every 30 min, 24/7 |

### Enable it

```bash
# Flip the env flag
ssh alpaca-pi "sed -i 's/STRATEGY_CRYPTO_ENABLED=false/STRATEGY_CRYPTO_ENABLED=true/' <DATA_HOST>/.env"

# Restart container
ssh alpaca-pi "docker restart alpaca-bot"

# Verify the crypto_scan job is registered (should now show 8 jobs, not 7)
ssh alpaca-pi "docker logs --tail 20 alpaca-bot | grep 'scheduler started'"

# Manually trigger to verify it works
ssh alpaca-pi "curl -s -X POST http://localhost:9700/trigger/crypto_scan"
ssh alpaca-pi "docker logs --tail 10 alpaca-bot | grep crypto"
```

You should see lines like:

```
crypto signal BTC/USD entry fired=False | rsi(2)=42.3>=15.0
crypto signal ETH/USD entry fired=False | rsi(2)=51.8>=15.0
```

The HA dashboard's "Signal scan" card will pick up crypto rows on the next 60-second poll. The "Bot health" entities row will show "Crypto strategy enabled: on".

### What changes in HA

- Heartbeat-stale alert now fires when *either* market is open OR crypto is enabled (the bot should be heartbeating 24/7 once crypto is on)
- Position-change alerts work for crypto too (the existing automation watches the open-positions count)
- The dashboard's Signal scan table gets a Strategy column with both ETF and Crypto rows

---

## Operational reference

### Useful endpoints (paper + live)

| URL | What |
|-----|------|
| `http://<PI_IP>:9700/healthz` | Quick alive check + account summary |
| `http://<PI_IP>:9700/status` | Full state JSON — what the HA REST sensor polls |
| `http://<PI_IP>:9700/journal/today` | Today's markdown journal entry |
| `http://<PI_IP>:9700/signals/today` | All signal rows for today |
| `http://<PI_IP>:9700/config` | Current risk caps + enabled strategies |
| `http://<PI_IP>:9700/trigger/<job>` | Fire a job manually (paper only) |

### Logs

```bash
ssh alpaca-pi "docker logs -f alpaca-bot"           # tail forever
ssh alpaca-pi "docker logs --tail 100 alpaca-bot"   # last 100 lines
```

The bot also writes to `<DATA_HOST>/logs/bot.log` (if you configured rotating file logging).

### Debugging signal rejections

If you expect a signal to fire and it doesn't:

1. Hit `/signals/today` — every scan writes a row even when the signal doesn't fire, with a `reason` explaining why
2. Check `/status` for the `circuit_breaker` field — it might be in `halted_drawdown` (manual reset needed) or `halted_daily`/`halted_consecutive_losses`
3. Check `/status` for `consecutive_losses` — at 3, the bot is in 24h cooloff
4. Check open positions: bucket caps reject if you're already at max exposure

### Resetting after a drawdown halt

If the drawdown circuit breaker tripped (down >8% from ATH) and you want to resume after reviewing:

```bash
ssh alpaca-pi "sqlite3 <DATA_HOST>/data/db.sqlite \
  'UPDATE circuit_breaker_state SET drawdown_halt_active=0 WHERE id=1'"
```

### Bot upgrade flow

When you change source code:

```bash
# Push the changed files
scp src/changed_file.py alpaca-pi:/root/alpaca-bot/src/

# Rebuild + restart
ssh alpaca-pi "cd /root/alpaca-bot && docker build -t alpaca-bot:latest . && \
  docker rm -f alpaca-bot && \
  docker run -d --name alpaca-bot --restart unless-stopped \
    -p 9700:8000 \
    -v <DATA_HOST>/data:/data -v <DATA_HOST>/logs:/logs \
    --env-file <DATA_HOST>/.env \
    alpaca-bot:latest"
```

The SQLite DB persists across container rebuilds (it's on the host volume).

### Costs (monthly, paper or live)

| Item | Cost |
|------|------|
| Alpaca commissions | $0 (commission-free for stocks/ETFs and crypto) |
| Anthropic Haiku 4.5 (1 daily call ~5k tokens + 1 weekly review ~20k) | < $0.50 |
| Pi power + hosting | $0 (existing) |
| **Total** | **~$0.50/mo** |

---

## File checklist

Things you should have on the Pi when done:

```
/root/alpaca-bot/                          # build context (source code)
├── Dockerfile
├── pyproject.toml
└── src/...

<DATA_HOST>/                               # runtime mounts
├── .env                                   # your API keys + config
├── data/
│   ├── db.sqlite                          # trades, signals, heartbeat
│   └── .paper-only                        # safety sentinel (delete only for live flip)
└── logs/
    └── bot.log                            # optional file logging

/config/packages/alpaca_bot.yaml           # HA REST sensor + templates + automations
/config/dashboards/alpaca.yaml             # HA dashboard layout
/config/configuration.yaml                 # has lovelace.dashboards.dashboard-alpaca entry
```

Things in the container:

```
docker ps                                  # alpaca-bot should be "Up X hours"
docker port alpaca-bot                     # 9700/tcp -> 0.0.0.0:8000
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `/healthz` returns "alpaca unreachable" | Wrong API keys or Alpaca outage | Double-check `.env`, check https://status.alpaca.markets |
| Container exits immediately | `ALPACA_MODE=live` but `.paper-only` exists, or missing env vars | `docker logs alpaca-bot` to see the exit message |
| Dashboard shows "Entity not found" | HA Core wasn't restarted after adding the package | Full restart, not just `automation.reload` |
| Phone alerts never arrive | Wrong notify entity ID, or HA Companion notifications disabled | Test with the manual trigger command above |
| `daily_bars` returns only 1 bar | Alpaca API changed default behavior | Confirm `alpaca_client.daily_bars()` passes explicit `start` and `end` dates |
| Crypto signal logs but no order placed | Risk gate rejected | Check `/signals/today` for the `entry_rejected` row with the reason |
| Heartbeat-stale alert fires constantly | Container restarting in a loop, OR alert trigger threshold too tight | `docker logs` to find the crash; verify `binary_sensor.alpaca_crypto_enabled` matches your actual config |

---

## Architecture notes (for the curious)

- **Why no LLM in the trade loop?** Every public experiment (Reddit r/algotrading threads, Jake Nesler's Prophet Trader, the MindStudio Alpaca guide) converges on the same finding: LLMs don't generate alpha at this scale. Use them only for code, journaling, and sanity-checks. The deterministic Python core is what actually buys and sells.
- **Why three layers of risk gates?** Per-order validation catches sizing mistakes. Circuit breakers (daily loss, drawdown, consecutive losses) catch strategy breakdown. Mode + capital ceiling catch operator mistakes. Each layer is independent; any one of them can save you from the other two failing.
- **Why a $200 ceiling even in live mode?** `managed_equity = min(actual_equity, ceiling)` means even if the account grows, the bot continues to size as though it's still $200. This bounds the experiment. To compound up, you explicitly raise `MANAGED_EQUITY_CEILING_USD` in the env.
- **Why limit orders only?** Market orders on a fractional notional get filled at whatever the next-tick price is. Limit orders fail safely if the spread moves against you, which is the better failure mode.
- **Why two timeframes for the same strategy?** The mean-reversion edge (price stretched too far from trend → snapback) exists on both daily ETF bars and hourly crypto bars. Same math, different cadence, independent of each other.

---

**That's the whole setup.** Once `/healthz` returns OK and the HA dashboard shows live equity, you're in paper-trading territory. Let it run for a few weeks, read the journal entries, and only then think about Phase 6.
