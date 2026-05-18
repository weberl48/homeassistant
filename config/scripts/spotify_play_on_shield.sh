#!/usr/bin/env bash
# Search Spotify for $1 and play the top track on the SHIELD Spotify Connect device.
# Reads the live Spotify access token from HA's config_entries (kept fresh by the integration).
set -euo pipefail

QUERY="${1:?usage: $0 <search query>}"
SHIELD_NAME="SHIELD"

TOKEN=$(python3 - <<'PY'
import json
d = json.load(open("/config/.storage/core.config_entries"))
for e in d["data"]["entries"]:
    if e.get("domain") == "spotify":
        print(e["data"]["token"]["access_token"])
        break
PY
)
[ -n "$TOKEN" ] || { echo "no spotify token in config_entries" >&2; exit 1; }

ENC_QUERY=$(python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))' "$QUERY")

# Resolve Shield's current Connect device id (rotates occasionally)
DEVICE_ID=$(curl -fsS "https://api.spotify.com/v1/me/player/devices" \
    -H "Authorization: Bearer $TOKEN" \
    | python3 -c "
import json,sys
d=json.load(sys.stdin)
for dev in d.get('devices',[]):
    if dev['name']==sys.argv[1]:
        print(dev['id']); break
" "$SHIELD_NAME")
[ -n "$DEVICE_ID" ] || { echo "Shield not visible to Spotify Connect — open Spotify on the Shield first" >&2; exit 1; }

# Top track URI for the query
URI=$(curl -fsS "https://api.spotify.com/v1/search?q=${ENC_QUERY}&type=track&limit=1" \
    -H "Authorization: Bearer $TOKEN" \
    | python3 -c "
import json,sys
d=json.load(sys.stdin)
items=d.get('tracks',{}).get('items',[])
print(items[0]['uri'] if items else '')
")
[ -n "$URI" ] || { echo "no Spotify match for query: $QUERY" >&2; exit 1; }

# Transfer + play in one call
curl -fsS -X PUT "https://api.spotify.com/v1/me/player/play?device_id=$DEVICE_ID" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    --data "{\"uris\":[\"$URI\"]}"

echo "playing: $URI"
