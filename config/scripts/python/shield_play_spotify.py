#!/usr/bin/env python3
"""Replaces script.shield_play_spotify: launch Spotify app on the Shield, find the
device in Spotify Connect, search for a query, play the top result.
Run: python3 shield_play_spotify.py "<search query>"
Example: python3 shield_play_spotify.py "Bohemian Rhapsody" """
import sys, os, time
sys.path.insert(0, os.path.dirname(__file__))
from ha_client import call_service, get_state, wait_for_state, spotify_request


SHIELD = "media_player.shield_2"
SPOTIFY_PACKAGE = "com.spotify.tv.android"
DEVICE_NAME = "SHIELD"


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: shield_play_spotify.py <search query>")
    query = sys.argv[1]

    # 1. Wake Shield + launch Spotify (idempotent — works whether shield is on or off)
    state = (get_state(SHIELD) or {}).get("state")
    if state != "on":
        print(f"Shield was {state!r} — waking.")
        call_service("media_player", "turn_on", target={"entity_id": SHIELD})
        if not wait_for_state(SHIELD, "on", timeout_s=8):
            sys.exit("FAIL: Shield did not come on within 8 seconds")
        time.sleep(1)
    call_service("media_player", "play_media",
                 target={"entity_id": SHIELD},
                 data={"media_content_type": "app", "media_content_id": SPOTIFY_PACKAGE})
    print(f"launched Spotify on Shield")
    time.sleep(2)  # Give Spotify Connect time to register

    # 2. Find the Shield's current Spotify Connect device id (rotates)
    devices = spotify_request("GET", "/me/player/devices")
    device_id = next((d["id"] for d in devices.get("devices", []) if d["name"] == DEVICE_NAME), None)
    if not device_id:
        sys.exit(f"FAIL: '{DEVICE_NAME}' not visible to Spotify Connect — open Spotify on the Shield manually first")
    print(f"device_id: {device_id}")

    # 3. Search for the top track matching the query
    search = spotify_request("GET", "/search", params={"q": query, "type": "track", "limit": 1})
    items = (search.get("tracks") or {}).get("items", [])
    if not items:
        sys.exit(f"FAIL: no Spotify match for query: {query!r}")
    uri = items[0]["uri"]
    name = items[0]["name"]
    artist = items[0]["artists"][0]["name"]
    print(f"top match: {name} by {artist}")

    # 4. Play it
    spotify_request("PUT", "/me/player/play",
                    params={"device_id": device_id},
                    body={"uris": [uri]})
    print(f"playing: {uri}")


if __name__ == "__main__":
    main()
