#!/usr/bin/env python3
"""Tablet view controls — fires Fully Kiosk REST commands to load a view + bring to foreground.
Subcommands: jukebox, dashboard.
Run: python3 tablet_show.py <jukebox|dashboard>"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from ha_client import call_service


def fire(load_cmd):
    call_service("rest_command", load_cmd)
    call_service("rest_command", "fk_bring_to_foreground")
    print(f"tablet → {load_cmd} + foreground")


CMDS = {
    "jukebox":   lambda: fire("fk_load_jukebox"),
    "dashboard": lambda: fire("fk_load_dashboard"),
}


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in CMDS:
        sys.exit(f"usage: tablet_show.py <{'|'.join(CMDS)}>")
    CMDS[sys.argv[1]]()
