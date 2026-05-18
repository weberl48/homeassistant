#!/usr/bin/env python3
"""
Force-launch the visualizer at the saved resume position. Called by
script.speakeasy_music_viz at end so the projector has visual content
before the per-track switcher kicks in on first Spotify track change.

Usage:
  launch_visualizer.py <viz_id>
"""
import json
import os
import sys
from datetime import datetime, timezone

from adb_shell.adb_device import AdbDeviceTcp
from adb_shell.auth.sign_pythonrsa import PythonRSASigner

SHIELD_HOST = "192.168.1.79"
SHIELD_PORT = 5555
ADB_KEY_PRIV = "/config/.storage/androidtv_adbkey"
ADB_KEY_PUB = "/config/.storage/androidtv_adbkey.pub"
STATE_FILE = "/config/scripts/visualizer_state.json"


def main():
    if len(sys.argv) < 2:
        sys.stderr.write("usage: launch_visualizer.py <viz_id>\n")
        return 2
    viz_id = sys.argv[1].strip()
    if len(viz_id) != 11:
        sys.stderr.write(f"invalid viz_id: {viz_id!r}\n")
        return 2

    # Load saved position
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        state = {"current": "", "viz_position": 0, "viz_started_at": None}

    pos = int(state.get("viz_position", 0))
    url = f"https://www.youtube.com/watch?v={viz_id}"
    if pos > 0:
        url += f"&t={pos}"

    # ADB launch
    with open(ADB_KEY_PRIV) as f:
        priv = f.read()
    with open(ADB_KEY_PUB) as f:
        pub = f.read()
    signer = PythonRSASigner(pub, priv)
    dev = AdbDeviceTcp(SHIELD_HOST, SHIELD_PORT, default_transport_timeout_s=8.0)
    dev.connect(rsa_keys=[signer], auth_timeout_s=5)
    try:
        out = dev.shell(f'am start -a android.intent.action.VIEW -d "{url}"')
        sys.stderr.write(f"adb out: {out!r}\n")
    finally:
        dev.close()

    # Mark state
    state["current"] = "visualizer"
    state["viz_started_at"] = datetime.now(timezone.utc).isoformat()
    state["display_title"] = "Visualizer"
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(tmp, STATE_FILE)
    sys.stderr.write(f"launched visualizer at t={pos}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
