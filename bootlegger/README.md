# BOOTLEGGER

A personal Sleeper autopilot: the legitimate storefront (draft board, start/sit,
waivers, trades, injury alerts) with a discreet back room (an approval-gated,
self-verifying lineup-swap actuator). Single user, single league, built to run
on the household Pi 5 behind Tailscale. Full design: [docs/DESIGN-PLAN.md](docs/DESIGN-PLAN.md).

The only automated write in this codebase is a reversible lineup swap.
Waivers, FAAB, and trades have no actuation code path — not disabled,
nonexistent — and the hands worker rejects any job that smuggles one in.

## Run it locally (demo mode, no network needed)

```bash
cd bootlegger/server
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest                       # 25 tests, whole loop covered
.venv/bin/python -m uvicorn app.api:app --host 0.0.0.0 --port 8484
```

(`--host 0.0.0.0` makes it reachable from the rest of the LAN/Tailscale —
e.g. http://192.168.1.160:8484 from the couch; leave it off to keep the
server local to the box.)

Open http://localhost:8484 — demo mode seeds a labeled synthetic 12-team
full-PPR league and starts a simulated live snake draft:

- **The Board** — tiers (GMM/BIC), VBD, ADP, "survives to my next pick" odds,
  pick suggestions with reasons; picks cross off within a 2s poll; you are
  slot 7, and the sim leaves you on the clock so you can watch The Call work.
- **This Week** — the seeded lineup is deliberately wrong (one starter ruled
  Out, one value miss). Tap **Approve & Execute** and watch the rec go
  proposed → notified → approved → executed → **verified**: the in-process
  hands worker (dry-run) applies the swap and post-verifies it against the
  API mirror, writing the audit trail you can read in **The Ledger**.
- **Waivers** — FA scores and FAAB bids sized from seeded league history.

Or with Docker: `cp .env.example .env && docker compose up api`.

`POST /api/draft/reset` (or the button under My Shelf) re-runs the mock.

## Live mode (your league)

1. `cp .env.example .env`, set `BOOTLEGGER_MODE=live`, `SLEEPER_LEAGUE_ID`,
   `SLEEPER_USER_ID`, `BOOTLEGGER_MY_ROSTER_ID`.
2. Nightly ETL: `python -m app.ingest nightly` (players, FFC ADP, FantasyCalc
   values, consensus). Wire it with `ops/systemd/bootlegger-nightly.timer` or cron.
3. Draft day: `python -m app.ingest draft-poll` polls picks at 2s; the same
   board renders against your real draft.
4. Compose services for the season: `docker compose --profile live up`.

### The hands (Phase 4 — do not skip the rehearsal)

`hands/` executes approved lineup swaps only, with pre-verify → act →
post-verify against the public API and a screenshot audit trail. It ships
**dry-run by default** and refuses to drive a browser until:

1. You create a private 2-team test league and rehearse there (design doc §5.8).
2. You record real accessibility-role locators in `hands/selector_map.json`
   and set `"calibrated": true`.
3. You export a Playwright `storageState.json` from a desktop login and mount
   it (compose secret; never committed).
4. `HANDS_DRY_RUN=0` — and `approve_required` stays on until it has earned
   three clean weeks.

The Wednesday canary (`hands.browser.canary`) walks the flow up to — never
including — the final tap and reports which locators still resolve.

## Reliability posture

Every failure degrades to a notification, never to silence: push (Expo/FCM,
outbound-only) with `recommendations` and `game-time-emergency` channels;
healthchecks.io dead-man pings (`HEALTHCHECKS_URL`); an off-Pi Sunday-9am
lineup check you can arm by copying `ops/github/lineup-check.yml` into
`.github/workflows/`; nightly backups via `ops/systemd/bootlegger-backup.service`.
Home Assistant stays out of the critical path.

## The app

`mobile/` is the Expo/React Native (TypeScript) client: Draft Board + This Week,
push registration with shade action buttons ([Approve & Execute] fires the job
without opening the app), DND-bypass emergency channel.

```bash
cd bootlegger/mobile
npm install            # or: npx expo install --fix to reconcile SDK versions
npx expo start         # dev; set extra.apiBase in app.json to the Pi's Tailscale IP
npm run build:apk      # sideloadable APK via EAS
```

The web board at `/` is the permanent fallback so draft day never depends on
the APK being finished.

## Layout

```
server/app        FastAPI + SQLite (WAL) + Sleeper/FFC/FantasyCalc clients
server/app/engines  consensus · GMM tiers · VBD · draft (survival, E[best]) ·
                    lineup (Hungarian) · waivers (bid percentiles) · trades
server/app/web    the board (no build step, self-hosted fonts)
server/hands      the back room: lineup-swap-only worker + browser flow + canary
server/tests      engines + the full proposed→verified loop
mobile/           Expo client
ops/              systemd timers · GitHub Actions off-Pi check
```

Demo player data is a labeled synthetic fixture (2025-season knowledge);
live mode replaces all of it from the real APIs.
