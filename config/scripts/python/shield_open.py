#!/usr/bin/env python3
"""Open a known app on the Shield by name. Delegates to shield_launch_app.
Run: python3 shield_open.py <app_name>
Known apps: netflix, youtube, plex, jellyfin, disney_plus, prime_video, max, hulu,
            apple_tv, spotify, tubi, pluto_tv, twitch, steam_link, kodi, vlc, stremio"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from shield_launch_app import main as _launch_main

PACKAGES = {
    "netflix":      "com.netflix.ninja",
    "youtube":      "com.google.android.youtube.tv",
    "plex":         "com.plexapp.android",
    "jellyfin":     "org.jellyfin.androidtv",
    "disney_plus":  "com.disney.disneyplus",
    "prime_video":  "com.amazon.amazonvideo.livingroom",
    "max":          "com.wbd.stream",
    "hulu":         "com.hulu.livingroomplus",
    "apple_tv":     "com.apple.atve.androidtv.appletv",
    "spotify":      "com.spotify.tv.android",
    "tubi":         "com.tubitv",
    "pluto_tv":     "tv.pluto.android",
    "twitch":       "tv.twitch.android.viewer",
    "steam_link":   "com.valvesoftware.steamlink",
    "kodi":         "org.xbmc.kodi",
    "vlc":          "org.videolan.vlc",
    "stremio":      "com.stremio.one",
}


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in PACKAGES:
        sys.exit(f"usage: shield_open.py <{'|'.join(sorted(PACKAGES))}>")
    # Reuse shield_launch_app's main by injecting the package as argv[1]
    sys.argv = [sys.argv[0], PACKAGES[sys.argv[1]]]
    _launch_main()
