"""Capture the ESPN session cookies a private league needs. Interactive.

Opens a headed browser at ESPN's fantasy site; you log in like a person, and
the tool watches the cookie jar until `SWID` and `espn_s2` both exist, then
writes them to a JSON file and stops. Nothing about your password is read,
stored, or seen — the browser handles the login; this only keeps the two
cookies ESPN's own site would keep.

Those cookies are long-lived (months). When they finally expire, the ESPN
client raises EspnAuthError in words, and you run this again.

    python tools/espn_login.py --out .espn_cookies.json
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=".espn_cookies.json")
    ap.add_argument("--timeout-s", type=int, default=420)
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=False, args=["--no-sandbox"])
        ctx = b.new_context(viewport={"width": 1280, "height": 900})
        pg = ctx.new_page()
        pg.goto("https://www.espn.com/fantasy/football/", timeout=60000)
        print("Log in to ESPN in the browser window (profile icon, top right).")
        print("Watching for the session cookies...")
        deadline = time.time() + args.timeout_s
        while time.time() < deadline:
            jar = {c["name"]: c["value"] for c in ctx.cookies()
                   if c["name"] in ("SWID", "espn_s2")}
            if len(jar) == 2:
                out = pathlib.Path(args.out)
                out.write_text(json.dumps(
                    {"SWID": jar["SWID"], "espn_s2": jar["espn_s2"],
                     "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S")},
                    indent=1), encoding="utf-8")
                try:
                    out.chmod(0o600)
                except OSError:
                    pass          # Windows: chmod is advisory at best
                print(f"\ncaptured both cookies -> {out}")
                b.close()
                return 0
            time.sleep(2)
        print("timed out waiting for a login", file=sys.stderr)
        b.close()
        return 1


if __name__ == "__main__":
    sys.exit(main())
