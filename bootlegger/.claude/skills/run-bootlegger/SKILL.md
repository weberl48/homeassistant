---
name: run-bootlegger
description: Build, launch, drive, screenshot, and smoke-test the Bootlegger server and its web UI (the draft board / war room) in demo mode. Use when asked to run, start, serve, launch, preview, screenshot, or smoke-test Bootlegger, or to check that a web-UI change actually renders.
---

# Run Bootlegger

A FastAPI server (`server/app/api.py`) that also serves the whole web UI as
static assets from `server/app/web/`. There is no separate frontend build — edit
`index.html` / `styles.css` / `app.js` and reload the page.

In **demo mode** (the default; no network, no credentials) it seeds a synthetic
12-team full-PPR league, runs a simulated snake draft in-process, and seeds one
deliberately-wrong week-1 lineup so the approve → execute → verify loop can be
driven end to end.

The agent path is **`driver.py`**, committed next to this file. It drives the
running app in a real browser: clicks the six tabs, screenshots each, and runs
the full approval flow. `chromium-cli` does not exist on this Windows box, which
is why the driver is a committed Playwright script rather than an inline heredoc.

All paths below are relative to `bootlegger/`. All commands are PowerShell.

## Prerequisites

The server venv (app deps only — deliberately no test tooling):

```powershell
cd server
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Verified on Python 3.14.0: all 34 packages resolved from `cp314` wheels, nothing
built from source (numpy 2.5.2, scipy 1.18.1, scikit-learn 1.9.0).

Playwright for the driver — installed against the **system** Python, not the
server venv, so browser tooling never ships in the app's dependency set:

```powershell
C:\Python314\python.exe -m pip install playwright
C:\Python314\python.exe -m playwright install chromium
```

## Run (agent path)

**1. Start the server.** From `server/`, so `app` is importable:

```powershell
cd server
.\.venv\Scripts\python.exe -m uvicorn app.api:app --host 0.0.0.0 --port 8484
```

It logs `demo mode: simulated draft + scanner + hands running in-process` and
`Uvicorn running on http://0.0.0.0:8484`. Background it in an agent harness;
`--host 0.0.0.0` also exposes it to the LAN/Tailscale, so drop that flag to keep
it local.

**2. Drive it.** From `bootlegger/`, with the server up:

```powershell
C:\Python314\python.exe .claude\skills\run-bootlegger\driver.py api
C:\Python314\python.exe .claude\skills\run-bootlegger\driver.py check
C:\Python314\python.exe .claude\skills\run-bootlegger\driver.py shots  <outdir>
C:\Python314\python.exe .claude\skills\run-bootlegger\driver.py flow   <outdir>
```

| Command | What it proves | Verified output |
|---|---|---|
| `api` | all 14 GETs the UI needs return 200 | `OK: 14 endpoints healthy` |
| `check` | page paints, no JS errors | `title 'BOOTLEGGER' · 6 tabs · 6 cols · 182 rows · js errors none` |
| `shots` | all six tabs render; PNG each | `OK: all tabs rendered` |
| `flow` | approve → executed → verified, via real button click | `OK: rec reached verified` |
| `audit` | the A++ gate — 17 checks a screenshot cannot make | `OK: all 17 audit checks passed` |

`audit` (in `audit.py`, next to the driver) is the one that catches what
review by eye does not: live regions announcing on an idle poll, a tablist
that is only styled like one, focus that never returns from the scout's file,
a control under 44px, rendered contrast under AA, sideways scroll at any of
five widths, the deciding columns wrapping, and the extension's inline palette
drifting from the board's. Every check corresponds to a defect that shipped at
least once — that is the entry requirement, so the file stays a record rather
than a wishlist.

`all` runs the five in sequence. Every command exits nonzero on failure, so they
are usable as a gate.

To smoke-test the **live** Pi after a deploy, point the driver at it — but run
only `api`, `check`, and `shots`. Never `flow` against live: it clicks a real
approve button on your real league.

```powershell
$env:BOOTLEGGER_BASE = "http://192.168.1.160:8484"
```

Verified against the live deploy: `14 endpoints healthy`, `6 tabs · 647 player
rows · js errors none`. Pre-draft, The League correctly reads every seat 0.0 /
`–` under a "Records open Week 1." note — empty rosters, not a broken page. **Look at the PNGs** — `check` passing only means the shell
resolved and the board painted rows.

**3. Stop it.** Ctrl-C does not reach a backgrounded uvicorn on Windows:

```powershell
Get-NetTCPConnection -LocalPort 8484 -State Listen |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { Stop-Process -Id $_ -Force }
```

## Run (human path)

Same launch, then open <http://localhost:8484>. `Start-Process "http://localhost:8484"`
opens the default browser.

## Test

```powershell
cd server
.\.venv\Scripts\python.exe -m pytest
```

`260 passed` (2026-08-27). Counts in this file go stale fast — re-run rather
than trust one that looks old.

## Rehearse the draft pilot

`server/tools/pilot_rehearsal.py` drives the REAL `hands.draft_pilot` process
against a simulated live snake draft, in dry run, and prints every pick it
would have made and where the choice came from. Safe by construction: a
throwaway DB in a temp dir, a demo server on an ephemeral port,
`HANDS_DRY_RUN` forced on, no Sleeper contact.

```powershell
cd server
.\.venv\Scripts\python.exe tools\pilot_rehearsal.py --rounds 3   # a quick look
.\.venv\Scripts\python.exe tools\pilot_rehearsal.py             # full 15 rounds
```

Nine checks; exits nonzero on any failure. **Expect one failure on this
machine** and it is a true one:

```
[FAIL] the browser stack would actually come up — playwright is not installed
```

Playwright is deliberately kept out of the server venv (see Prerequisites), so
that check is telling you *this host cannot fly the pilot* — run
`playwright install chromium` on whichever host will. It exists because a dry
run returns before the lazy Playwright import, so a clean rehearsal otherwise
says nothing about whether the browser would come up at all, and the three
ways bringup dies are reached only AFTER arming.

What it does NOT prove: the browser half. Dry run opens no page, so the
Sleeper locators are not exercised. A live-room rehearsal is still owed before
the pilot flies for real.

## Reset the demo

**Restarting the server does not reseed.** `demo.seed()` short-circuits on a
`demo_seeded` meta flag, so a demo whose lineup rec was already approved stays
approved across restarts. `POST /api/draft/reset` only re-runs the *draft*, not
the lineup rec. The only full reset is deleting the DB — all three files, or you
get a half-reset from the WAL:

```powershell
Get-NetTCPConnection -LocalPort 8484 -State Listen |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { Stop-Process -Id $_ -Force }
Remove-Item data\bootlegger.db* -Force
```

Then start the server again. Confirm you got the intended pending state:

```powershell
(curl.exe -s http://localhost:8484/api/recs | ConvertFrom-Json)[0].state
```

That prints `notified` (the field is `state`, not `status`), and **This Week** should show "You
have trouble in the lineup" with Drake London struck through as OUT and an
enabled **APPROVE & EXECUTE** button.

## Gotchas

- **Run `audit` against the live Pi too, not only the demo.** The demo seeds
  an EMPTY slip, so the slip's reorder/remove buttons never render locally —
  they shipped at 20x22px and only the live board exposed it. Any control that
  appears solely in a populated state is invisible to a demo-only gate.
  `$env:BOOTLEGGER_BASE = "http://192.168.1.160:8484"` then `audit`; it is
  read-only and safe against live (unlike `flow`).
- **A running server is not proof it is running THIS code.** `StaticFiles`
  reads HTML/CSS/JS from disk per request, so the frontend is always current;
  the Python is whatever was imported at process start. A server started before
  a commit that added routes serves a NEW frontend calling OLD endpoints, and
  it presents as a broken feature rather than a stale process — a `/api/wire`
  404 on every tab once read as "the wire endpoint is broken". Hashing
  `/assets/app.js` against the worktree proves nothing. Probe a route from a
  recent commit, or just restart, before reviewing anything.
- **The committed review PNGs under `.impeccable/review/` are provenance for
  the commit that wrote them, not the current build.** Before grading or
  critiquing the UI, `git log -1 -- .impeccable/review/` and recapture if
  anything under `server/app/` has landed since. One of them showed a Parlor
  bug that had been fixed four commits earlier.

- **`drafts.updated_at` is a liveness heartbeat, not a pick log.** The board
  banners `PICK FEED STALE` whenever it ages past 10s during a drafting draft
  (`app/web/app.js:302`), so `demo.tick()` refreshes it on *every* visit — the
  mirror of `etl_draft_picks(full=False)` in live mode. Do not "simplify" this
  into `record_pick`: the sim idles up to `demo_my_clock_seconds` (16s) while it
  leaves you on the clock, so a heartbeat that only moved when a pick landed
  banners a healthy feed through every one of those pauses. Both invariants are
  pinned by `tests/test_flow.py -k heartbeat`. A correct demo shows a green
  **WIRE LIVE** and no red band; heartbeat age should stay ≤2s.
- **`/health` is not under `/api`.** Every other endpoint is `/api/...`, but the
  health check is bare `/health`; `/api/health` returns 404 and reads like an
  outage.
- **The demo shows a played season next to a live draft, on purpose.** The
  League's records come from `demo._play_season()`, a deterministic 14-week
  round-robin, so the room opens on a real table instead of twelve 0-0 rows.
  The demo has always been a showcase fixture rather than a coherent timeline —
  it already seeds a week-1 lineup card mid-draft. Its noise term is dialled
  below real fantasy variance deliberately (`_SEASON_SIGMA`); at a realistic 25
  the standings stop tracking roster quality at all (r≈0.35) and the table
  reads as random beside the ranking. Pinned by `tests/test_league.py`.
- **Driving the UI consumes the demo.** The Approve button really executes the
  one seeded rec, so `driver.py flow` (or any stray click) leaves This Week in
  "table's folded". Reseed before capturing screenshots meant to show the
  pending decision.
- **Tabs cannot be clicked by text.** Each tab's `innerText` is two lines
  (`"THE BOARD\nthe draft"`), so `get_by_text(..., exact=True)` matches nothing
  and every click silently times out. `driver.py` locates them by
  `nav a, nav button, [role=tab]` and index.
- **Player rows are `.prow`**, board columns are `section.col` — not the
  `.player` / `.player-row` you would guess. A selector guess that matches
  nothing makes a render check pass vacuously.
- **`server/.venv/` is gitignored, so git worktrees do not share it.** A fresh
  worktree has no venv even when the main checkout does. Build it per
  Prerequisites; it is ~4 min.
- **Demo state lives in `data/bootlegger.db`**, which the compose file mounts at
  `/data`. The `-wal` file reaches ~3 MB; it holds most of the recent state.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `curl` to 8484 refuses connection | Server not up, or it was killed and the harness reported exit 127. Relaunch from `server/`. |
| `driver.py` reports `player rows: 0` but no JS error | The board did not paint. Check `/api/draft/board` returns players; a 200 with an empty list still renders an empty board. |
| `driver.py flow` prints `SKIP: no enabled #btn-approve` | The rec was already approved. Reseed (see **Reset the demo**). |
| Tab clicks time out in a hand-written script | You matched on text. Use role + index — see Gotchas. |
