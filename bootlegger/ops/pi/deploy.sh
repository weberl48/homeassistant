#!/bin/sh
# Bootlegger — the deployment that actually runs on the household Pi (HAOS).
# HAOS has no docker-compose; this script IS the record of the running stack.
# Run from the Pi's SSH addon (Protection mode off for docker.sock access):
#
#   FANTASYPROS_API_KEY=... sh /share/bootlegger/server/../ops/pi/deploy.sh
#
# Source of truth for code: /share/bootlegger/server (rsync'd from the repo).
# NOTE: after rebuilding the image, containers must be RECREATED (rm -f + run)
# — `docker restart` reuses the old image and silently serves stale code.
set -eu

SRC=/share/bootlegger/server
DATA=/mnt/data/supervisor/share/bootlegger/data-live   # host path for -v
IMG=bootlegger:latest

LEAGUE=1389751438090977280
USER_ID=1000465535043203072
ROSTER=2          # draft-slot fallback until Sleeper publishes draft_order
SEASON=2026

: "${FANTASYPROS_API_KEY:?export FANTASYPROS_API_KEY (or pass inline)}"
API_TOKEN="${BOOTLEGGER_API_TOKEN:-}"   # empty = mutation gate off (LAN trust)

ENV_COMMON="-e BOOTLEGGER_MODE=live -e SLEEPER_LEAGUE_ID=$LEAGUE -e SLEEPER_USER_ID=$USER_ID -e BOOTLEGGER_MY_ROSTER_ID=$ROSTER -e BOOTLEGGER_SEASON=$SEASON -e FANTASYPROS_API_KEY=$FANTASYPROS_API_KEY -e BOOTLEGGER_LEAGUE_LABEL=Boko_no_Football -e BOOTLEGGER_SIBLING_PORT=8486 -e BOOTLEGGER_SIBLING_LABEL=No_Punts_Intended -e BOOTLEGGER_API_TOKEN=$API_TOKEN -v $DATA:/data"

docker build -t $IMG $SRC

docker rm -f bootlegger bootlegger-ingest bootlegger-nightly bootlegger-hands 2>/dev/null || true

# The board + API. HANDS_DRY_RUN comes from the same knob as the worker so
# /health and the board always report the mode the hands actually run in —
# a split value here made monitoring lie in the dangerous direction.
# shellcheck disable=SC2086
docker run -d --name bootlegger --restart unless-stopped -p 8484:8484 \
  -e BOOTLEGGER_APPROVE_REQUIRED=1 -e HANDS_DRY_RUN="${HANDS_DRY_RUN:-1}" $ENV_COMMON $IMG

# Draft-pick poller (2s cadence; crash-tolerant loop inside)
# shellcheck disable=SC2086
docker run -d --name bootlegger-ingest --restart unless-stopped $ENV_COMMON \
  $IMG python -m app.ingest draft-poll

# Nightly ETL loop (players, schedule+weather, ADP, values, ECR, projections)
# RUN FIRST, then sleep. Sleeping first means a container recreated at 7pm sits
# on whatever the sheet held that morning until 7pm tomorrow — which is exactly
# the shape of the 2026 draft, where the board opened eleven hours stale. A
# deploy should leave the data fresher than it found it, not older.
# shellcheck disable=SC2086
docker run -d --name bootlegger-nightly --restart unless-stopped $ENV_COMMON \
  --entrypoint sh $IMG -c 'while true; do python -m app.ingest nightly; sleep 86400; done'

# Hands worker — the consumer of the approval queue. Ships in DRY-RUN unless
# HANDS_DRY_RUN=0 is exported at deploy time: it drains approved jobs, runs the
# don't-act rules and pre/post verification, and logs act:dry_run_swap instead
# of touching a browser. Without this container an approval enqueued a job
# nothing consumed. Real execution additionally needs the calibrated swap
# selector map + session state (see hands/browser.py) and BOOTLEGGER_API_TOKEN.
# shellcheck disable=SC2086
# --memory=2g and --shm-size=512m: the hands drive a real Chromium against
# Sleeper's lineup editor, and at the old 1g ceiling it died mid-swap with
# "Target crashed" rendering a hydrated React roster. Only this container
# ever launches a browser, so the other three are unaffected.
docker run -d --name bootlegger-hands --restart unless-stopped \
  -e BOOTLEGGER_APPROVE_REQUIRED=1 -e HANDS_DRY_RUN="${HANDS_DRY_RUN:-1}" \
  --memory=2g --shm-size=512m $ENV_COMMON $IMG python -m hands.worker

# ---------------------------------------------------------------------------
# The second league: No Punts Intended (ESPN), read-only advisory stack.
#
# Same image, its own database and port. The platform switch changes exactly
# one thing — who answers the league-shaped calls (see app/espn.py) — while
# players, projections, ADP and news ride the same public pipeline as the
# Sleeper stack. No hands and no draft poller here: actuation and draft
# piloting are Sleeper-only, and the board's fallback copy says "Set it on
# ESPN" accordingly. Auth comes from /data-espn/.espn_cookies.json, captured
# by tools/espn_login.py; until it exists the nightly logs EspnAuthError in
# words and the board serves the national layer without league rows.
ESPN_LEAGUE=1435831655
ESPN_ROSTER="${ESPN_ROSTER:-3}"   # Wolverines team id, matched by owner SWID 2026-08-31
ESPN_DATA=/mnt/data/supervisor/share/bootlegger/data-espn
mkdir -p "$ESPN_DATA"
ENV_ESPN="-e BOOTLEGGER_MODE=live -e BOOTLEGGER_PLATFORM=espn   -e BOOTLEGGER_LEAGUE_ID=$ESPN_LEAGUE -e BOOTLEGGER_MY_ROSTER_ID=$ESPN_ROSTER   -e BOOTLEGGER_SEASON=$SEASON -e FANTASYPROS_API_KEY=$FANTASYPROS_API_KEY    -e BOOTLEGGER_LEAGUE_LABEL=No_Punts_Intended -e BOOTLEGGER_SIBLING_PORT=8484 -e BOOTLEGGER_SIBLING_LABEL=Boko_no_Football  -e BOOTLEGGER_API_TOKEN=$API_TOKEN -v $ESPN_DATA:/data"

docker rm -f bootlegger-espn bootlegger-espn-nightly bootlegger-espn-ingest 2>/dev/null || true
# shellcheck disable=SC2086
docker run -d --name bootlegger-espn --restart unless-stopped -p 8486:8484   $ENV_ESPN $IMG
# shellcheck disable=SC2086
docker run -d --name bootlegger-espn-nightly --restart unless-stopped $ENV_ESPN   --entrypoint sh $IMG -c 'while true; do python -m app.ingest nightly; sleep 86400; done'
# The draft watcher. ESPN publishes no pick stream, so this polls the league
# document: 30s while the room is idle, 3s once it is live, 5min once it is
# done. Without it the board sits at pre_draft through the whole draft.
# shellcheck disable=SC2086
docker run -d --name bootlegger-espn-ingest --restart unless-stopped $ENV_ESPN $IMG python -m app.ingest draft-poll

sleep 5
curl -sf http://192.168.1.160:8484/health && echo && echo "deployed."
curl -sf http://192.168.1.160:8486/health >/dev/null && echo "espn stack up on :8486."   || echo "espn stack not answering yet (fine on first boot — nightly seeds it)."
