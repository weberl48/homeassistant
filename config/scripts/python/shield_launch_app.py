#!/usr/bin/env python3
"""Replaces script.shield_launch_app: wake the Shield if needed, then launch an app
by Android package name. Idempotent. Run: python3 shield_launch_app.py <package>
Example: python3 shield_launch_app.py com.netflix.ninja"""
import sys, os, time
sys.path.insert(0, os.path.dirname(__file__))
from ha_client import call_service, get_state, wait_for_state


SHIELD = "media_player.shield_2"


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: shield_launch_app.py <android_package>")
    package = sys.argv[1]

    state = (get_state(SHIELD) or {}).get("state")
    if state != "on":
        print(f"Shield was {state!r} — waking it.")
        call_service("media_player", "turn_on", target={"entity_id": SHIELD})
        if not wait_for_state(SHIELD, "on", timeout_s=8):
            sys.exit("FAIL: Shield did not come on within 8 seconds")
        time.sleep(1)

    call_service("media_player", "play_media",
                 target={"entity_id": SHIELD},
                 data={"media_content_type": "app", "media_content_id": package})
    print(f"launched: {package}")


if __name__ == "__main__":
    main()
