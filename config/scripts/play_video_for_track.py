#!/usr/bin/env python3
"""
Speakeasy Music+Viz: per-track YouTube switcher.

Two modes, gated on input_boolean.speakeasy_music_video_only:

  OFF (default — "music + viz"): if yt-dlp returns anything for the track,
  switch the Shield to that video. If nothing, resume the generic
  visualizer. Spotify keeps playing.

  ON ("music videos only"): the cached/searched yt-dlp result must pass a
  quality heuristic (real official music video, not lyric / audio / cover /
  Topic-channel). If not, call media_next_track on Spotify. Capped at
  MAX_CONSECUTIVE_SKIPS so a no-MV playlist eventually falls through to viz
  instead of skipping forever.

Usage:
  play_video_for_track.py <artist> <title> <viz_id> <spotify_position>

State file: /config/scripts/visualizer_state.json
Cache file: /config/scripts/youtube_cache.json (shared with prefetcher)
"""
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

from adb_shell.adb_device import AdbDeviceTcp
from adb_shell.auth.sign_pythonrsa import PythonRSASigner

SHIELD_HOST = "192.168.1.79"
SHIELD_PORT = 5555
ADB_KEY_PRIV = "/config/.storage/androidtv_adbkey"
ADB_KEY_PUB = "/config/.storage/androidtv_adbkey.pub"
SEARCH_TIMEOUT = 10
CACHE_FILE = "/config/scripts/youtube_cache.json"
STATE_FILE = "/config/scripts/visualizer_state.json"

HA_URL = "http://192.168.1.160:8123"
HA_TOKEN_FILE = "/config/.ha_token"
MV_ONLY_FLAG = "input_boolean.speakeasy_music_video_only"
SPOTIFY_ENTITY = "media_player.spotify_lucas_weber"
MAX_CONSECUTIVE_SKIPS = 6


def now():
    return datetime.now(timezone.utc)


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _ha_token():
    try:
        with open(HA_TOKEN_FILE) as f:
            return f.read().strip()
    except Exception as e:
        sys.stderr.write(f"ha_token err: {e}\n")
        return ""


def _ha_get_state(entity):
    tok = _ha_token()
    if not tok:
        return None
    req = urllib.request.Request(
        f"{HA_URL}/api/states/{entity}",
        headers={"Authorization": f"Bearer {tok}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.load(r).get("state")
    except Exception as e:
        sys.stderr.write(f"ha_get_state {entity}: {e}\n")
        return None


def _ha_call(domain, service, payload):
    tok = _ha_token()
    if not tok:
        return False
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{HA_URL}/api/services/{domain}/{service}",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {tok}",
            "Content-Type": "application/json",
        },
    )
    try:
        urllib.request.urlopen(req, timeout=5).read()
        return True
    except Exception as e:
        sys.stderr.write(f"ha_call {domain}.{service}: {e}\n")
        return False


def yt_search(query):
    """Return dict {id,title,channel} of top result, or None."""
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
        line = r.stdout.strip().splitlines()
        if not line:
            return None
        parts = line[0].split("|||")
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


def find_mv_info(artist, title):
    """Return dict {id,title,channel} or None; cache results.

    Auto-upgrades legacy cache entries (bare id strings) to dict schema.
    """
    if not artist or not title:
        return None
    cache = load_json(CACHE_FILE, {})
    key = f"{artist}|{title}"
    cached = cache.get(key)
    if isinstance(cached, dict):
        return cached if cached.get("id") else None
    if isinstance(cached, str):
        if not cached:
            return None
        sys.stderr.write(f"cache legacy upgrade: {key}\n")
        info = yt_search(f"{artist} {title} official music video")
        cache[key] = info if info else {}
        save_json(CACHE_FILE, cache)
        return info if info else None
    sys.stderr.write(f"cache miss, searching: {artist} {title}\n")
    info = yt_search(f"{artist} {title} official music video")
    cache[key] = info if info else {}
    save_json(CACHE_FILE, cache)
    return info


def is_real_music_video(artist, video):
    """Heuristic: does this yt-dlp result look like a real official MV?"""
    if not video or not video.get("id"):
        return False
    t = (video.get("title") or "").lower()
    c = (video.get("channel") or "").lower()
    a = (artist or "").lower()
    if not t:
        return False
    if c.endswith(" - topic"):
        return False
    bad = ("lyric", "official audio", "(audio)", "karaoke",
           "instrumental", "(cover)", " cover by ", "extended mix",
           "sped up", "slowed")
    if any(b in t for b in bad):
        return False
    if "vevo" in c:
        return True
    if any(k in t for k in ("official video", "official music video",
                            "official mv", "music video")):
        return True
    first = a.split(",")[0].strip().split()[0] if a else ""
    if first and len(first) >= 3 and first in c:
        return True
    return False


def adb_launch(uri):
    with open(ADB_KEY_PRIV) as f:
        priv = f.read()
    with open(ADB_KEY_PUB) as f:
        pub = f.read()
    signer = PythonRSASigner(pub, priv)
    dev = AdbDeviceTcp(SHIELD_HOST, SHIELD_PORT, default_transport_timeout_s=8.0)
    dev.connect(rsa_keys=[signer], auth_timeout_s=5)
    try:
        out = dev.shell(f'am start -a android.intent.action.VIEW -d "{uri}"')
        sys.stderr.write(f"adb out: {out!r}\n")
    finally:
        dev.close()


def visualizer_resume_position(state):
    pos = int(state.get("viz_position", 0))
    started = state.get("viz_started_at")
    if state.get("current") == "visualizer" and started:
        try:
            dt = datetime.fromisoformat(started)
            pos += int((now() - dt).total_seconds())
        except Exception:
            pass
    return max(0, pos)


def main():
    if len(sys.argv) < 4:
        sys.stderr.write("usage: play_video_for_track.py <artist> <title> <viz_id> [<spotify_pos>]\n")
        return 2

    artist = sys.argv[1].strip()
    title = sys.argv[2].strip()
    viz_id = sys.argv[3].strip()
    try:
        spotify_pos = max(0, int(float(sys.argv[4]))) if len(sys.argv) >= 5 else 0
    except (ValueError, TypeError):
        spotify_pos = 0

    state = load_json(STATE_FILE, {"current": "", "viz_position": 0, "viz_started_at": None})
    current = state.get("current", "")
    mv_only = _ha_get_state(MV_ONLY_FLAG) == "on"

    info = find_mv_info(artist, title)
    has_any = bool(info and info.get("id") and len(info["id"]) == 11)

    if mv_only:
        passes = has_any and is_real_music_video(artist, info)
        if not passes:
            skips = int(state.get("consecutive_skips", 0)) + 1
            sys.stderr.write(f"mvonly: not a real MV ({info!r}) — skips={skips}\n")
            if skips < MAX_CONSECUTIVE_SKIPS:
                state["consecutive_skips"] = skips
                save_json(STATE_FILE, state)
                _ha_call("media_player", "media_next_track",
                         {"entity_id": SPOTIFY_ENTITY})
                return 0
            sys.stderr.write("mvonly: skip cap hit; falling through to visualizer\n")
            state["consecutive_skips"] = 0
            play_mv = False
        else:
            state["consecutive_skips"] = 0
            play_mv = True
    else:
        play_mv = has_any

    if play_mv:
        target_type = "mv"
        target_url = f"https://www.youtube.com/watch?v={info['id']}"
        if spotify_pos > 0:
            target_url += f"&t={spotify_pos}"
    else:
        target_type = "visualizer"
        viz_pos = visualizer_resume_position(state)
        target_url = f"https://www.youtube.com/watch?v={viz_id}"
        if viz_pos > 0:
            target_url += f"&t={viz_pos}"

    if target_type == "visualizer" and current == "visualizer":
        sys.stderr.write(f"already on visualizer (no-op); track={artist}|{title}\n")
        if state.get("consecutive_skips"):
            state["consecutive_skips"] = 0
            save_json(STATE_FILE, state)
        return 0

    if current == "visualizer" and target_type == "mv":
        state["viz_position"] = visualizer_resume_position(state)
        sys.stderr.write(f"saved viz position before MV switch: {state['viz_position']}\n")

    sys.stderr.write(f"launching {target_type}: {target_url}\n")
    try:
        adb_launch(target_url)
    except Exception as e:
        sys.stderr.write(f"adb err: {e}\n")
        return 1

    state["current"] = target_type
    if target_type == "visualizer":
        state["viz_started_at"] = now().isoformat()
        state["display_title"] = "Visualizer"
    else:
        state["viz_started_at"] = None
        if info and info.get("title"):
            state["display_title"] = info["title"]
        else:
            state["display_title"] = f"{artist} — {title}" if artist and title else "Music video"
    state["consecutive_skips"] = 0
    save_json(STATE_FILE, state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
