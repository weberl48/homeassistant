#!/usr/bin/env bash
# Resolve a query to an IMDB id via Cinemeta and open the Stremio detail page on the Shield.
# User then picks a stream + clicks Play (Stremio's stream selection requires their addons).
set -euo pipefail

QUERY="${1:?usage: $0 <search query>}"
ENC=$(python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))' "$QUERY")

MOVIE=$(curl -fsS --max-time 5 "https://v3-cinemeta.strem.io/catalog/movie/top/search=$ENC.json" 2>/dev/null || echo '{}')
SERIES=$(curl -fsS --max-time 5 "https://v3-cinemeta.strem.io/catalog/series/top/search=$ENC.json" 2>/dev/null || echo '{}')

PICK=$(python3 - "$QUERY" "$MOVIE" "$SERIES" <<'PY'
import json, sys
q = sys.argv[1].lower().strip()
m = json.loads(sys.argv[2]).get('metas', [])
s = json.loads(sys.argv[3]).get('metas', [])

def score(item):
    n = (item.get('name') or '').lower().strip()
    if not n: return 0
    if n == q: return 1000
    if n.startswith(q) or q.startswith(n): return 500
    if q in n: return 300
    nw, qw = set(n.split()), set(q.split())
    return 10 * len(nw & qw)

cands = [('movie', x) for x in m[:3]] + [('series', x) for x in s[:3]]
if not cands: sys.exit(1)
cands.sort(key=lambda c: score(c[1]), reverse=True)
typ, item = cands[0]
print(f"{typ}\t{item['id']}\t{item.get('name','')}")
PY
) || { echo "no Cinemeta match for: $QUERY" >&2; exit 1; }

IFS=$'\t' read -r TYPE ID NAME <<<"$PICK"
echo "match: $NAME ($TYPE/$ID)"

curl -fsS -X POST \
    -H "Authorization: Bearer $SUPERVISOR_TOKEN" \
    -H "Content-Type: application/json" \
    --data "{\"entity_id\":\"media_player.shield_adb\",\"command\":\"am start -a android.intent.action.VIEW -d \\\"stremio:///detail/$TYPE/$ID\\\"\"}" \
    "http://supervisor/core/api/services/androidtv/adb_command" > /dev/null

echo "opened: stremio:///detail/$TYPE/$ID"
