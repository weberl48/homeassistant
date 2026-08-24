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

ENV_COMMON="-e BOOTLEGGER_MODE=live -e SLEEPER_LEAGUE_ID=$LEAGUE \
  -e SLEEPER_USER_ID=$USER_ID -e BOOTLEGGER_MY_ROSTER_ID=$ROSTER \
  -e BOOTLEGGER_SEASON=$SEASON -e FANTASYPROS_API_KEY=$FANTASYPROS_API_KEY \
  -e BOOTLEGGER_API_TOKEN=$API_TOKEN -v $DATA:/data"

docker build -t $IMG $SRC

docker rm -f bootlegger bootlegger-ingest bootlegger-nightly 2>/dev/null || true

# The board + API (the only container that needs the port and the hands gates)
# shellcheck disable=SC2086
docker run -d --name bootlegger --restart unless-stopped -p 8484:8484 \
  -e BOOTLEGGER_APPROVE_REQUIRED=1 -e HANDS_DRY_RUN=1 $ENV_COMMON $IMG

# Draft-pick poller (2s cadence; crash-tolerant loop inside)
# shellcheck disable=SC2086
docker run -d --name bootlegger-ingest --restart unless-stopped $ENV_COMMON \
  $IMG python -m app.ingest draft-poll

# Nightly ETL loop (players, ADP, values, ECR, three projection sources)
# shellcheck disable=SC2086
docker run -d --name bootlegger-nightly --restart unless-stopped $ENV_COMMON \
  --entrypoint sh $IMG -c 'while true; do sleep 86400; python -m app.ingest nightly; done'

sleep 5
curl -sf http://192.168.1.160:8484/health && echo && echo "deployed."
