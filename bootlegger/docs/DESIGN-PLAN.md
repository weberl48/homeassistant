# BOOTLEGGER — Design & Build Plan

### A personal Sleeper autopilot that competes with FantasyPros (Tier 1 + Tier 2)

**Codename rationale:** it runs the legitimate storefront (analysis, rankings, notifications) with a discreet back room (the actuation layer). Fits the Speakeasy Cinema household aesthetic.

**Status:** Approved 2026-08-24 ("build this"). Open decisions resolved to the recommendations: `approve_required` default, Expo/React Native app, Python-only projection legs.

---

## 1. Requirements

### Functional (parity target = FantasyPros MVP for one league)

| Capability | Target |
|---|---|
| Live draft assistant | Real-time board synced to your Sleeper draft (no browser extension), tiers + VBD + "survives to my next pick" odds, pick suggestions |
| Weekly start/sit | Optimal lineup vs. actual lineup diff, with rationale, pushed to phone |
| Waiver / FAAB | Roster-aware FA rankings + bid sizing calibrated to *your league's* historical bids |
| Trade analyzer | ROS value + market cross-check; advisory only, never automated |
| Injury response | Status-change alerts on rostered players with a recommended action |
| **Actuation (Tier 2)** | Executes **lineup swaps only**, approval-gated by default, fully audited, self-verifying |

### Non-functional

- **Reliability during the Sunday window is the top NFR.** The system must never *silently* fail: every failure degrades to a notification, never to nothing.
- **Blast-radius containment:** the only automated write in the entire codebase is a reversible lineup swap. Waiver submissions, FAAB bids, and trades have *no actuation code path* — not disabled, nonexistent.
- Single user, single league, runs on the Pi 5 next to Home Assistant, reachable via Tailscale.
- Human-paced, own-IP, own-account. This is you, from your house, doing your own lineup — just with robot hands. Accepted risk (per prior ToS analysis): worst case is account action; containment + audit trail + instant fallback to notify-only is the mitigation.

### Constraints

- Sleeper public API is read-only (free, no auth, ~1000 req/min soft limit); all writes go through the web UI via Playwright.
- Draft in ~2 weeks → the draft board is the Phase 1 deadline; everything else can land in-season.
- Solo build, nights/weekends: ~40–60 hrs total budget.

---

## 2. Architecture

```
┌──────────────────────────  Pi 5 (Docker Compose)  ─────────────────────────┐
│                                                                            │
│  [ingest]        nightly ETL + in-season pollers ──────► [SQLite (WAL)]    │
│   • Sleeper public API (players 24h cache, league,          ▲              │
│     rosters, matchups, transactions, trending,              │              │
│     draft picks @2s during draft)                           │              │
│   • nflreadpy: injuries, depth charts, snaps,               │              │
│     cached FantasyPros ECR                                  │              │
│   • FFC ADP API • FantasyCalc values (sleeperId join)       │              │
│                                                             │              │
│  [brain]         decision engines (pure functions over DB) ─┘              │
│   • draft engine (VOLS/VBD + GMM tiers + survival)                         │
│   • lineup optimizer (optimal vs actual diff)                              │
│   • waiver/FAAB engine (league-history percentiles)                        │
│   • trade analyzer (ROS VBD + FantasyCalc cross-check)                     │
│   • rationale writer (Claude API / MCP — READ-ONLY tools,                  │
│     proposes text, can never trigger anything)                             │
│                                                                            │
│  [push]          FCM via Expo push — outbound-only from the Pi             │
│   payload: rec + rationale + deep link + shade action buttons:             │
│   [Approve & Execute] [Snooze 30m]   (tap-through opens the app)           │
│   channels: normal · game-time-emergency (DND-bypass, alarm sound)         │
│   escalation: T-90 and T-20 before affected kickoff if diff persists       │
│                                                                            │
│  [hands]         Tier 2 actuation worker (separate container)              │
│   Playwright + Chromium (ARM64, headless), storageState secret,            │
│   LINEUP SWAPS ONLY, consumes signed jobs from approval queue,             │
│   pre-verify → act → post-verify via public API, screenshots to audit/     │
│                                                                            │
│  [api]           FastAPI backend (same brain) serving the app over         │
│                  Tailscale + a bare mobile web fallback page               │
└────────────────────────────────────────────────────────────────────────────┘
        ▲
        │ approvals & app traffic ride always-on Tailscale VPN on the phone
        │
   [Android app]   Expo / React Native (TypeScript), sideloaded APK (EAS)
    screens: Draft Board · This Week · Waivers · Trades · Audit · Rules
    push arrives via FCM (cloud); OTA JS updates via EAS Update mid-season
        │ off-Pi safety net: healthchecks.io dead-man pings per job +
        │ a GitHub Actions Sunday-9am "is-my-lineup-set" redundant check
```

**Languages:** Python 3.12 on the Pi (nflreadpy, scikit-learn, Playwright) + TypeScript/Expo for the app. One repo: `/server` (compose services `api`, `ingest`, `hands`) and `/mobile` (Expo app).

### The Android app (mobile-first, replaces the web dashboard)

- **Framework:** Expo / React Native in TypeScript; EAS Build produces a sideloadable APK (no Play Store, no review); EAS Update ships JS fixes over-the-air mid-season without reinstalling.
- **Push:** Expo push service over FCM (one-time Firebase credential setup). The Pi only ever connects *outbound* — no inbound ports, no relays. Notification categories give shade-level action buttons; **[Approve & Execute]** fires the actuation job without even opening the app.
- **Notification channels:** `recommendations` (normal priority) and `game-time-emergency` — high importance, alarm sound, DND-bypass via a one-time Android settings grant.
- **Return leg:** approvals and all app↔API traffic ride always-on Tailscale VPN on the phone. If Tailscale is down, the push still arrives (cloud path); the app queues the approval, retries, and always shows an "Open Sleeper" fallback.
- **Screens:** Draft Board (live, 2s polling) · This Week (lineup diff card + approve) · Waivers (targets + FAAB) · Trades (analyzer) · Audit (screenshot trail) · Rules (don't-act toggles).
- **Fallback insurance:** the API keeps serving one bare mobile web page mirroring the Draft Board — so draft day never depends on the APK being finished.
- **Deferred to offseason:** home-screen widget (needs native module), in-app Claude chat, iOS build if ever wanted.

---

## 3. Data model (SQLite, WAL mode)

```
players(sleeper_id PK, name, pos, team, bye, status, injury_status, updated_at)
projections(player_id, week, source, pts, floor, ceiling, PRIMARY KEY(player_id, week, source))
consensus(player_id, week, pts_mean, pts_robust, stdev, tier, vbd, PRIMARY KEY(player_id, week))
adp(player_id, source, adp, stdev, updated_at)
player_values(player_id, redraft_value, trend_30d, updated_at)    -- FantasyCalc ("values" is a SQL keyword)
league(league_id PK, settings_json, scoring_json)
rosters(roster_id, owner, players_json, starters_json, updated_at)
matchups(week, roster_id, opp_roster_id, proj_for, proj_against)
transactions(txn_id PK, week, type, adds_json, drops_json, faab, status, ts)
recommendations(rec_id PK, kind, week, payload_json, rationale, state, created_at)
    -- state: proposed → notified → approved|snoozed|ignored → executed → verified|failed
actions_log(action_id PK, rec_id FK, step, screenshot_path, api_state_before, api_state_after, ts)
rules(rule_id PK, name, threshold, enabled)                       -- the don't-act policy
```

Seed FAAB history by walking `previous_league_id` back through prior seasons of the league and ingesting historical transactions — the bid-percentile model works from week 1.

---

## 4. Decision engines (the math, pinned)

- **Consensus projections:** robust average (median-trimmed) across sources; simple beats clever per the forecasting literature. Sources: nflreadpy-cached FP ECR (converted to points via value curve), Sleeper projections when available, plus one scraped weekly source if a third leg proves necessary.
- **Tiers:** 1-D Gaussian mixture per position (scikit-learn), components chosen by BIC — the Boris Chen method.
- **VBD:** VOLS baselines for a 12-team full-PPR (QB12, RB≈30–32, WR≈40 incl. flex share, TE12); recompute weekly for start/sit context.
- **Draft suggestion score:** `VBD_now − E[best VBD available at next pick]`, where availability uses survival `P = 1 − Φ((my_next_pick − ADP)/σ)` with σ from FFC ranges (fallback 0.15×ADP). Roster-need multiplier caps at unfilled starter slots.
- **Lineup optimizer:** exhaustive over slot assignments (trivial at this scale), FLEX resolved last; **diff against actual starters read from the API**; notify only when materiality > 1.5 proj pts or an injury flag is involved.
- **Waiver/FAAB:** FA score = ROS consensus value − worst droppable roster value at the position; bid = league-history percentile for that value tier (default P70), +$1 over round numbers; hard-confirm rule above 25% of remaining budget.
- **Trade analyzer:** side sums of ROS VBD + FantasyCalc redraft values as market sanity check + positional-need overlay. Output is analysis text only.

---

## 5. The Tier 2 actuation module ("hands") — reliability design

1. **Scope lock (compile-time, not config):** the worker contains selectors and flows for exactly one operation — tap-to-swap lineup edits on the Sleeper web team page. No other flow exists in the code.
2. **Auth:** one-time interactive login on desktop → export Playwright `storageState.json` → mounted into the container as a read-only secret. A session-expiry probe runs before every job; on expiry: abort, screenshot, notify "re-auth needed," fall back to Tier 1.
3. **Job contract:** `hands` only consumes jobs from the approval queue: `{rec_id, week, swaps:[{out_id,in_id,slot}], lineup_hash_expected, expires_at}`. Jobs are created *only* by an Approve event (or auto-mode policy). Expired jobs are dropped, never retried.
4. **Execute-verify loop:**
   - **Pre-verify:** read `/league/{id}/rosters` from the public API; if actual lineup ≠ `lineup_hash_expected`, abort — the world changed since the recommendation.
   - **Act:** navigate → locate by accessibility role/text (never CSS classes), randomized 0.8–2.5s pacing between interactions, screenshot every step to `audit/`.
   - **Post-verify:** re-read the API until the lineup matches the target (timeout 60s). Only then mark `verified` and send the confirmation notification. Post-verify failure ⇒ `failed` + urgent notification with screenshots. Success claims require API evidence, never Playwright's word for it.
   - **Idempotent by construction:** re-running any job re-does pre-verify first; an already-applied lineup no-ops.
5. **Two modes, one toggle:** `approve_required` (default) · `auto_execute` (opt-in later; only when no don't-act rule fires, projection edge > 2.0 pts, > 2h before the affected kickoff, and the job is a pure bench↔starter swap; always sends the "I did X — tap to undo" notification). Earn trust in approve mode for 3+ weeks first.
6. **Don't-act rules:** Questionable/Doubtful inside 3h of kickoff · weather flag on the game · source disagreement > 25% on either player · any drop involved · any FAAB involved (structurally impossible anyway) · rules table row disabled.
7. **Canary:** a Wednesday-morning dry-run job navigates the full flow up to (not including) the final tap and diffs the selector map — so UI redeploys break loudly on Wednesday, not silently on Sunday.
8. **Rehearsal environment:** before `hands` ever touches the real league, create a free private 2-team test league and green the whole flow there.
9. **Pi 5 specifics:** Chromium ARM64 with `--no-sandbox --disable-dev-shm-usage --disable-gpu`, memory-capped container, single job at a time.

---

## 6. Reliability & ops

- **Degradation ladder:** `hands` fails → Tier 1 notification with deep link → escalation at T-90/T-20 → off-Pi GitHub Actions check at Sunday 9am ET reads the public API and emails if any starter is OUT/BYE (works even if the Pi is dead).
- **Dead-man switch:** every scheduled job pings healthchecks.io; missed ping ⇒ out-of-band alert.
- **Backups:** nightly SQLite snapshot + `audit/` rsync to the NAS share.
- **Scheduling:** systemd timers on the Pi. Home Assistant is out of the critical path entirely — optional decoration only (e.g., WLED red-alert on an unresolved lineup diff).
- **Observability:** structured logs + a `/health` endpoint; every recommendation's full lifecycle is queryable in `recommendations` + `actions_log`.

---

## 7. Parity scorecard vs FantasyPros

| Area | Bootlegger | Verdict |
|---|---|---|
| Live draft sync | API polling, no extension, works from any device | **Better** |
| Custom-scoring exactness | Your league's literal settings | **Better** |
| FAAB advice | Modeled on your league's actual bid history | **Better** |
| Automation & notifications | Native Android push w/ shade approvals, DND-bypass emergencies, self-verifying actuation | **Better** (FP has nothing) |
| Rationale | LLM paragraph per recommendation | **Better** |
| Start/sit & trade math | Same commodity algorithms | Parity |
| Expert breadth | FP ECR (via nflverse cache) is *one input*, not 100 experts | Worse — mitigated |
| Projection polish, UX breadth | Solo build | Worse — irrelevant for one user |

---

## 8. Build plan (draft ≈ Sep 1–5)

- **Phase 0 — draft-ready MVP:** repo + compose + SQLite schema · Sleeper client + players/ADP/FantasyCalc/ECR ETL · consensus + GMM tiers + VOLS · app shell + Draft Board screen (2s pick polling, best-available, survival odds) · the web fallback page · rehearse against a Sleeper mock. *Done when: in a live mock, the board crosses off every pick within 3s and top-available matches gut within one tier ~90% of the time.*
- **Phase 1 — pre-draft week:** Expo/FCM push pipeline with shade action buttons + DND-bypass emergency channel · lineup optimizer + diff in dry-run · This Week screen. *Done when: a forced suboptimal lineup produces a phone notification with a working [Approve] button inside 60s of the poll.*
- **Draft day:** board on the laptop/phone, human picks, zero actuation.
- **Phase 2 — season weeks 1–2:** injury watcher · T-90/T-20 escalation · verification loop on confirmations · GitHub Actions off-Pi check · healthchecks + backups.
- **Phase 3 — weeks 2–4:** waiver/FAAB engine (seeded from `previous_league_id` history) · LLM rationale via forked read-only MCP · rules table UI.
- **Phase 4 — weeks 3–5: the hands.** Test-league rehearsal → selector map + canary → `approve_required` live on the real league → 3 clean weeks → optionally flip `auto_execute`. *Done when: 3 consecutive real approved swaps go proposed→executed→API-verified with complete screenshot trails and zero manual fixes.*

---

## 9. Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| Sleeper UI redeploy breaks selectors | High (eventually) | Role/text selectors + Wednesday canary + Tier 1 fallback |
| Session expiry before Sunday | Medium | Pre-job probe, re-auth notification, fallback |
| Account action (ToS) | Low-probability / high-impact — accepted | Lineup-only scope, human pacing, own IP, approve-gated, instant retreat to Tier 1 |
| Pi down on Sunday | Low | Off-Pi GitHub Actions check + dead-man alert |
| Projection source breaks | Medium | Multi-source consensus, stale-data flag, never suppress alerts |
| Auto-mode acts on bad data | Low | Don't-act rules + 2h undo window + verify loop |

---

## 10. Decisions (resolved 2026-08-24)

1. **Actuation default:** `approve_required`, with `auto_execute` earned after 3 clean weeks.
2. **App framework:** Expo/React Native in TypeScript, sideloaded EAS APK.
3. **Third projection leg:** Python-only (ECR + Sleeper) until a week proves more is needed.
