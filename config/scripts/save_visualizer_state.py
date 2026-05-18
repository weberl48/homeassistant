#!/usr/bin/env python3
"""
Snapshot the visualizer position so next Music+Viz session resumes from
where we left off. Called by automation.speakeasy_pause_spotify_when_leaving_music_viz.

If we were on the visualizer when Off was pressed, computes elapsed wall-clock
since last launch and saves it. If we were on a music video, the saved position
is whatever was preserved when we last switched FROM viz to MV (no further
update needed).
"""
import json
import os
from datetime import datetime, timezone

STATE_FILE = "/config/scripts/visualizer_state.json"


def main():
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return 0

    current = state.get("current", "")
    started = state.get("viz_started_at")
    if current == "visualizer" and started:
        try:
            dt = datetime.fromisoformat(started)
            elapsed = int((datetime.now(timezone.utc) - dt).total_seconds())
            state["viz_position"] = max(0, int(state.get("viz_position", 0)) + elapsed)
        except Exception:
            pass

    # Mark as not actively playing
    state["viz_started_at"] = None
    state["current"] = "off"
    state["display_title"] = "—"

    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(tmp, STATE_FILE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
