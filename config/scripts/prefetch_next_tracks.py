#!/usr/bin/env python3
"""
Speakeasy Music+Viz prefetcher.

Reads Spotify's queue via Web API (using HA's OAuth token), then runs
yt-dlp searches for the next N upcoming tracks and caches the results.
By the time the user skips, the lookup is a cache hit.

Cache schema: {key: {"id": str, "title": str, "channel": str}} per entry.
Legacy entries (bare id strings) are tolerated and left in place; the
matcher script auto-upgrades them on first hit.

Run on every Spotify track change while activity = music.

No CLI args.
  /config/.storage/core.config_entries  → Spotify access_token
  /config/scripts/youtube_cache.json    → existing cache (read+write)
"""
import json
import os
import subprocess
import sys
import urllib.request

CACHE_FILE = "/config/scripts/youtube_cache.json"
CONFIG_ENTRIES = "/config/.storage/core.config_entries"
PREFETCH_COUNT = 3
SEARCH_TIMEOUT = 12


def get_spotify_token():
    with open(CONFIG_ENTRIES) as f:
        entries = json.load(f)
    for e in entries["data"]["entries"]:
        if e.get("domain") == "spotify":
            return e["data"]["token"]["access_token"]
    return None


def get_queue(token):
    req = urllib.request.Request(
        "https://api.spotify.com/v1/me/player/queue",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


def cache_load():
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def cache_save(cache):
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cache, f, indent=2, sort_keys=True)
    os.replace(tmp, CACHE_FILE)


def yt_search(query):
    """Return dict {id,title,channel} or None."""
    try:
        r = subprocess.run(
            [
                "yt-dlp",
                f"ytsearch1:{query}",
                "--print",
                "%(id)s|||%(title)s|||%(channel)s",
                "--skip-download",
                "--no-warnings",
                "--quiet",
            ],
            capture_output=True,
            text=True,
            timeout=SEARCH_TIMEOUT,
        )
        out = r.stdout.strip().splitlines()
        if not out:
            return None
        parts = out[0].split("|||")
        if not parts[0].strip():
            return None
        return {
            "id": parts[0].strip(),
            "title": parts[1].strip() if len(parts) > 1 else "",
            "channel": parts[2].strip() if len(parts) > 2 else "",
        }
    except Exception as e:
        sys.stderr.write(f"yt_search err: {e}\n")
        return None


def is_cached_good(cached):
    """Cache entry is considered good if it has an id (any schema)."""
    if isinstance(cached, dict):
        return bool(cached.get("id"))
    if isinstance(cached, str):
        return bool(cached)
    return False


def main():
    token = get_spotify_token()
    if not token:
        sys.stderr.write("no spotify token\n")
        return 1

    try:
        queue_data = get_queue(token)
    except Exception as e:
        sys.stderr.write(f"queue api err: {e}\n")
        return 1

    upcoming = queue_data.get("queue", []) or []
    if not upcoming:
        sys.stderr.write("queue empty\n")
        return 0

    cache = cache_load()
    new_keys = 0
    for track in upcoming[:PREFETCH_COUNT]:
        artists = track.get("artists", []) or []
        artist = ", ".join(a["name"] for a in artists) if artists else ""
        title = track.get("name", "")
        if not artist or not title:
            continue
        key = f"{artist}|{title}"
        if key in cache and is_cached_good(cache[key]):
            sys.stderr.write(f"hit:  {key}\n")
            continue
        sys.stderr.write(f"miss: {key} -> searching...\n")
        info = yt_search(f"{artist} {title} official music video")
        cache[key] = info if info else {}
        sys.stderr.write(f"      cached as {info!r}\n")
        new_keys += 1

    if new_keys > 0:
        cache_save(cache)
    sys.stderr.write(f"done: {new_keys} new entries cached\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
