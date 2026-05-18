#!/usr/bin/env python3
"""Spotify jukebox helper. Three modes:

  search-and-queue <query>  -- search Spotify and queue the top result (default if no mode given)
  search <query>             -- search and print top 5 results (one per line, pipe-delimited fields)
  queue-uri <uri>            -- queue a specific Spotify track URI

Reads HA's stored Spotify token; auto-refreshes if within 60s of expiry.
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

CONFIG_ENTRIES = "/config/.storage/core.config_entries"
APP_CREDS = "/config/.storage/application_credentials"


def _read_spotify_entry() -> dict:
    data = json.load(open(CONFIG_ENTRIES))
    for e in data["data"]["entries"]:
        if e.get("domain") == "spotify":
            return e
    raise SystemExit("ERROR: no spotify integration in core.config_entries")


def _read_credentials(impl_id: str) -> tuple[str, str]:
    data = json.load(open(APP_CREDS))
    for c in data["data"]["items"]:
        if c["id"] == impl_id or c.get("auth_domain") == "spotify":
            return c["client_id"], c["client_secret"]
    raise SystemExit(f"ERROR: no application_credential matched impl {impl_id}")


def _refresh(refresh_token: str, client_id: str, client_secret: str) -> dict:
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode()
    req = urllib.request.Request(
        "https://accounts.spotify.com/api/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    return json.load(urllib.request.urlopen(req, timeout=8))


def _get_access_token() -> str:
    entry = _read_spotify_entry()
    tok = entry["data"]["token"]
    if tok["expires_at"] - time.time() > 60:
        return tok["access_token"]
    cid, csec = _read_credentials(entry["data"]["auth_implementation"])
    fresh = _refresh(tok["refresh_token"], cid, csec)
    return fresh["access_token"]


def _search(token: str, query: str, limit: int = 5) -> list:
    q = urllib.parse.urlencode({"q": query, "type": "track", "limit": limit})
    req = urllib.request.Request(
        f"https://api.spotify.com/v1/search?{q}",
        headers={"Authorization": f"Bearer {token}"},
    )
    res = json.load(urllib.request.urlopen(req, timeout=8))
    return res.get("tracks", {}).get("items", [])


def _queue(token: str, uri: str) -> None:
    q = urllib.parse.urlencode({"uri": uri})
    req = urllib.request.Request(
        f"https://api.spotify.com/v1/me/player/queue?{q}",
        data=b"",
        method="POST",
        headers={"Authorization": f"Bearer {token}"},
    )
    urllib.request.urlopen(req, timeout=8)


def _track_summary(t: dict) -> str:
    artists = ", ".join(a["name"] for a in t.get("artists", []))
    return f"{t['name']} — {artists}"


def _smallest_album_art(t: dict) -> str:
    images = t.get("album", {}).get("images", [])
    if not images:
        return ""
    # Spotify returns largest first; pick smallest sufficient (last)
    return images[-1].get("url", "")


def _sanitize(s: str) -> str:
    """Strip pipe and newline so we can use them as field/row delimiters in stdout."""
    return s.replace("|", "/").replace("\n", " ").replace("\r", " ").strip()


def cmd_search_and_queue(args: list) -> int:
    if not args:
        print("usage: search-and-queue <query>")
        return 1
    query = " ".join(args).strip()
    if not query:
        print("EMPTY QUERY")
        return 1
    try:
        token = _get_access_token()
        items = _search(token, query, limit=1)
    except Exception as e:
        print(f"SEARCH ERROR: {e}")
        return 3
    if not items:
        print(f"NO RESULTS for: {query}")
        return 4
    t = items[0]
    try:
        _queue(token, t["uri"])
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")[:200]
        print(f"QUEUE ERROR {e.code}: {body}")
        return 5
    print(f"QUEUED: {_track_summary(t)}")
    return 0


def cmd_search(args: list) -> int:
    """Print top 5 results, one per line, pipe-delimited:
       title|||artist|||uri|||album_art_url
    """
    if not args:
        return 1
    query = " ".join(args).strip()
    if not query:
        return 1
    try:
        token = _get_access_token()
        items = _search(token, query, limit=5)
    except Exception as e:
        print(f"SEARCH ERROR: {e}", file=sys.stderr)
        return 3
    for t in items:
        title = _sanitize(t.get("name", ""))
        artists = _sanitize(", ".join(a["name"] for a in t.get("artists", [])))
        uri = t.get("uri", "")
        art = _smallest_album_art(t)
        print(f"{title}|||{artists}|||{uri}|||{art}")
    # Pad to 5 lines so HA can index slots predictably
    for _ in range(5 - len(items)):
        print("|||||||")
    return 0


def cmd_queue_uri(args: list) -> int:
    if not args:
        print("usage: queue-uri <spotify:track:URI>")
        return 1
    uri = args[0].strip()
    if not uri.startswith("spotify:track:"):
        print(f"INVALID URI: {uri}")
        return 1
    try:
        token = _get_access_token()
        _queue(token, uri)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")[:200]
        print(f"QUEUE ERROR {e.code}: {body}")
        return 5
    except Exception as e:
        print(f"ERROR: {e}")
        return 6
    print(f"QUEUED URI: {uri}")
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: spotify_search_queue.py <mode> <args...>")
        print("modes: search-and-queue | search | queue-uri")
        return 1
    mode = sys.argv[1]
    args = sys.argv[2:]
    if mode == "search":
        return cmd_search(args)
    if mode == "queue-uri":
        return cmd_queue_uri(args)
    if mode == "search-and-queue":
        return cmd_search_and_queue(args)
    # Backward-compatible: if mode isn't recognised, treat the whole tail as a query.
    return cmd_search_and_queue([mode] + args)


if __name__ == "__main__":
    sys.exit(main())
