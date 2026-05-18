#!/usr/bin/env python3
"""Snooze AdGuard blocking for N minutes (15, 30, or 60). Sets the snooze timer and
the input_boolean. Run: python3 adguard_snooze.py <minutes>"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from ha_client import call_service

TIMER = "timer.adguard_snooze"
PAUSED = "input_boolean.adguard_block_paused"


def snooze(minutes):
    duration = f"00:{minutes:02d}:00" if minutes < 60 else f"01:00:00"
    call_service("timer", "start",
                 target={"entity_id": TIMER},
                 data={"duration": duration})
    call_service("input_boolean", "turn_on", target={"entity_id": PAUSED})
    print(f"snoozed AdGuard blocking for {minutes} min")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: adguard_snooze.py <15|30|60>")
    m = int(sys.argv[1])
    if m not in (15, 30, 60):
        sys.exit("minutes must be 15, 30, or 60")
    snooze(m)
