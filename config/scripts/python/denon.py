#!/usr/bin/env python3
"""Denon AVR-X3800H controls. Subcommands: mute_toggle, vol_up, vol_down, source_next, source_prev.
Run: python3 denon.py <subcommand>"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from ha_client import call_service, state_attr

DENON = "media_player.denon_avr_x3800h"


def mute_toggle():
    muted = state_attr(DENON, "is_volume_muted", False)
    call_service("media_player", "volume_mute",
                 target={"entity_id": DENON},
                 data={"is_volume_muted": not muted})
    print(f"mute: {muted} -> {not muted}")


def vol_step(delta, lo=0.0, hi=0.85):
    cur = state_attr(DENON, "volume_level", 0.0)
    if cur is None:
        cur = 0.0
    new = max(lo, min(hi, cur + delta))
    call_service("media_player", "volume_set",
                 target={"entity_id": DENON},
                 data={"volume_level": new})
    print(f"volume: {cur:.2f} -> {new:.2f}")


def source_step(direction):
    sources = state_attr(DENON, "source_list", []) or []
    if not sources:
        sys.exit("no source_list available")
    current = state_attr(DENON, "source") or sources[0]
    idx = sources.index(current) if current in sources else 0
    new_idx = (idx + direction) % len(sources)
    new_source = sources[new_idx]
    call_service("media_player", "select_source",
                 target={"entity_id": DENON},
                 data={"source": new_source})
    print(f"source: {current} -> {new_source}")


CMDS = {
    "mute_toggle": lambda: mute_toggle(),
    "vol_up": lambda: vol_step(+0.02),
    "vol_down": lambda: vol_step(-0.02),
    "source_next": lambda: source_step(+1),
    "source_prev": lambda: source_step(-1),
}


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in CMDS:
        sys.exit(f"usage: denon.py <{','.join(CMDS)}>")
    CMDS[sys.argv[1]]()
