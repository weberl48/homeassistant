#!/usr/bin/env python3
"""Speakeasy heaters. Subcommands: on, off, toggle.
on  → set both heaters to heat mode at the input_number target temp (calls set_hvac_mode FIRST
      because smart_envi drops hvac_mode kwarg in set_temperature — known quirk).
off → climate.turn_off on both heaters.
toggle → if either heater is in heat mode, turn both off (and clear preheat flag);
         otherwise turn both on (and set preheat flag).
Run: python3 speakeasy_heaters.py <on|off|toggle>"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from ha_client import call_service, state_value

HEATERS = ["climate.smax_b6a6d8_smax_b6a6d8", "climate.smax_b6c6f4_smax_b6c6f4"]
HEATER_TARGET = "input_number.speakeasy_heater_target"
PREHEAT = "input_boolean.speakeasy_heater_preheat"


def on():
    call_service("climate", "set_hvac_mode",
                 target={"entity_id": HEATERS},
                 data={"hvac_mode": "heat"})
    temp = float(state_value(HEATER_TARGET) or 67)
    call_service("climate", "set_temperature",
                 target={"entity_id": HEATERS},
                 data={"temperature": temp})
    print(f"heaters → heat at {temp:.0f}°F")


def off():
    call_service("climate", "turn_off", target={"entity_id": HEATERS})
    print("heaters → off")


def toggle():
    in_heat = any(state_value(h) == "heat" for h in HEATERS)
    if in_heat:
        call_service("input_boolean", "turn_off", target={"entity_id": PREHEAT})
        off()
    else:
        call_service("input_boolean", "turn_on", target={"entity_id": PREHEAT})
        on()


CMDS = {"on": on, "off": off, "toggle": toggle}


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in CMDS:
        sys.exit(f"usage: speakeasy_heaters.py <{','.join(CMDS)}>")
    CMDS[sys.argv[1]]()
