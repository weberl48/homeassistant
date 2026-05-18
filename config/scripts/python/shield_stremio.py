#!/usr/bin/env python3
"""Stremio integration on the Shield. Absorbs stremio_play_on_shield.sh.
Subcommands:
  search <query>  -- open Stremio search results page for query
  play <query>    -- find best-match movie/series via Cinemeta + open detail page
Run: python3 shield_stremio.py <search|play> <query>"""
import sys, os, time, json, urllib.parse, urllib.request
sys.path.insert(0, os.path.dirname(__file__))
from ha_client import call_service, state_value, wait_for_state

SHIELD = "media_player.shield_2"
SHIELD_ADB = "media_player.shield_adb"
STREMIO_PACKAGE = "com.stremio.one"


def _ensure_shield_on():
    if state_value(SHIELD) != "on":
        call_service("media_player", "turn_on", target={"entity_id": SHIELD})
        if not wait_for_state(SHIELD, "on", timeout_s=8):
            sys.exit("FAIL: Shield did not come on within 8 seconds")
        time.sleep(1)


def _launch_stremio():
    _ensure_shield_on()
    call_service("media_player", "play_media",
                 target={"entity_id": SHIELD},
                 data={"media_content_type": "app", "media_content_id": STREMIO_PACKAGE})
    time.sleep(2)


def _adb(command):
    call_service("androidtv", "adb_command",
                 data={"entity_id": SHIELD_ADB, "command": command})


def search(query):
    _launch_stremio()
    enc = urllib.parse.quote(query)
    _adb(f'am start -a android.intent.action.VIEW -d "stremio:///search?search={enc}"')
    print(f"opened Stremio search for: {query}")


def _cinemeta(kind, query):
    """Hit Cinemeta's catalog endpoint and return the parsed metas list."""
    enc = urllib.parse.quote(query)
    url = f"https://v3-cinemeta.strem.io/catalog/{kind}/top/search={enc}.json"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.load(r).get("metas", [])
    except Exception:
        return []


def _score(item, q):
    n = (item.get("name") or "").lower().strip()
    if not n:
        return 0
    if n == q: return 1000
    if n.startswith(q) or q.startswith(n): return 500
    if q in n: return 300
    return 10 * len(set(n.split()) & set(q.split()))


def play(query):
    q = query.lower().strip()
    movies = _cinemeta("movie", query)
    series = _cinemeta("series", query)
    cands = [("movie", x) for x in movies[:3]] + [("series", x) for x in series[:3]]
    if not cands:
        sys.exit(f"no Cinemeta match for: {query!r}")
    cands.sort(key=lambda c: _score(c[1], q), reverse=True)
    kind, item = cands[0]
    name = item.get("name", "")
    iid = item["id"]
    print(f"match: {name} ({kind}/{iid})")

    _launch_stremio()
    _adb(f'am start -a android.intent.action.VIEW -d "stremio:///detail/{kind}/{iid}"')
    print(f"opened: stremio:///detail/{kind}/{iid}")


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in ("search", "play"):
        sys.exit("usage: shield_stremio.py <search|play> <query>")
    {"search": search, "play": play}[sys.argv[1]](sys.argv[2])
