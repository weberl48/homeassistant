#!/usr/bin/env python3
"""Replaces script.adguard_resume_now: cancel the snooze timer + turn off the pause flag.
Idempotent — safe to run multiple times. Run: python3 adguard_resume_now.py"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from ha_client import call_service, get_state


def main():
    call_service("timer", "cancel", target={"entity_id": "timer.adguard_snooze"})
    call_service("input_boolean", "turn_off",
                 target={"entity_id": "input_boolean.adguard_block_paused"})

    timer_st = get_state("timer.adguard_snooze") or {}
    paused_st = get_state("input_boolean.adguard_block_paused") or {}
    print(f"timer.adguard_snooze: {timer_st.get('state')}")
    print(f"input_boolean.adguard_block_paused: {paused_st.get('state')}")
    if paused_st.get("state") != "off":
        sys.exit(f"FAIL: pause flag not off (got {paused_st.get('state')})")


if __name__ == "__main__":
    main()
