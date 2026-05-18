#!/usr/bin/env python3
"""Speakeasy controls. Subcommands: activity_next, activity_prev, play_pause_smart,
skip_next, skip_prev, heater_target_up, heater_target_down.
Run: python3 speakeasy_controls.py <subcommand>"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from ha_client import call_service, state_value

ACTIVITY = "input_select.speakeasy_activity"
HEATER_TARGET = "input_number.speakeasy_heater_target"
TARGETS = [
    "media_player.spotify_lucas_weber",
    "media_player.shield",
    "media_player.denon_avr_x3800h_2",
]


def activity_step(direction):
    service = "select_next" if direction > 0 else "select_previous"
    call_service("input_select", service, target={"entity_id": ACTIVITY})
    print(f"activity stepped {'+1' if direction>0 else '-1'}")


def _media_target():
    """Pick the first media_player that's playing or paused; fall back to Denon."""
    for ent in TARGETS:
        st = state_value(ent)
        if st in ("playing", "paused"):
            return ent
    return TARGETS[-1]


def media_service(service):
    tgt = _media_target()
    call_service("media_player", service, target={"entity_id": tgt})
    print(f"{service} on {tgt}")


def heater_target_step(delta, lo=60, hi=75):
    cur = float(state_value(HEATER_TARGET) or 67)
    new = max(lo, min(hi, cur + delta))
    call_service("input_number", "set_value",
                 target={"entity_id": HEATER_TARGET},
                 data={"value": new})
    print(f"heater target: {cur:.0f}°F -> {new:.0f}°F")


CMDS = {
    "activity_next": lambda: activity_step(+1),
    "activity_prev": lambda: activity_step(-1),
    "play_pause_smart": lambda: media_service("media_play_pause"),
    "skip_next": lambda: media_service("media_next_track"),
    "skip_prev": lambda: media_service("media_previous_track"),
    "heater_target_up": lambda: heater_target_step(+1),
    "heater_target_down": lambda: heater_target_step(-1),
}


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in CMDS:
        sys.exit(f"usage: speakeasy_controls.py <{','.join(CMDS)}>")
    CMDS[sys.argv[1]]()
