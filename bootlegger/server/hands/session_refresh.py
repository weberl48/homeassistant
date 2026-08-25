"""Export a Sleeper session to a storageState file — WITH A BIG CAVEAT.

Verified 2026-08-25: a FRESH credential login keeps its auth as session
state only — no token ever lands in localStorage, the export does not
replay in a new context, and even a persistent profile reopens logged out.
Exports only carry auth when captured from a long-lived everyday browser
(legacy localStorage token), i.e. the manual DevTools capture.

The pilot therefore does NOT use this: it logs in fresh at launch via
hands/sleeper_login and keeps its browser open (SLEEPER_LOGIN_USER/PW).
This tool remains for producing a state file from an established profile,
and as the reference for the login-form selectors.

Credentials come from env only and are never printed or logged.

Usage:  SLEEPER_LOGIN_USER=... SLEEPER_LOGIN_PW=... \
        python -m hands.session_refresh /path/to/storage_state.json
"""
from __future__ import annotations

import os
import re
import sys
import time

USER = os.environ.get("SLEEPER_LOGIN_USER", "")
PW = os.environ.get("SLEEPER_LOGIN_PW", "")


def main() -> int:
    if not USER or not PW:
        print("SLEEPER_LOGIN_USER / SLEEPER_LOGIN_PW not set", file=sys.stderr)
        return 2
    out = sys.argv[1] if len(sys.argv) > 1 else "sleeper_storage_state.json"

    from playwright.sync_api import sync_playwright

    args = ["--no-sandbox", "--disable-dev-shm-usage"]
    profile = os.environ.get("SLEEPER_PROFILE_DIR", "")
    with sync_playwright() as pw:
        # A persistent profile is the whole trick: Sleeper challenges NEW
        # devices with an emailed code, but a profile that passed the
        # challenge once stays trusted — later refreshes sail through.
        if profile:
            ctx = pw.chromium.launch_persistent_context(
                profile, args=args, viewport={"width": 1200, "height": 850})
            b = None
            pg = ctx.pages[0] if ctx.pages else ctx.new_page()
        else:
            b = pw.chromium.launch(args=args)
            ctx = b.new_context(viewport={"width": 1200, "height": 850})
            pg = ctx.new_page()
        pg.goto("https://sleeper.com/login", timeout=45000)
        time.sleep(4)
        if USER in pg.evaluate("document.body.innerText"):
            state = "in"                       # trusted profile, still signed in
        else:
            pg.get_by_label("Email, phone, or username").first.fill(USER)
            pg.get_by_label("Password").first.fill(PW)
            # the visible "LOG IN" is CSS-uppercased; match DOM text loosely
            pg.get_by_role("button", name=re.compile(r"log\s*in", re.I)).first.click()

            # Sleeper's login page keeps EVERY panel in the DOM (reset flow
            # included), so body-text sniffing lies. Trust only success or a
            # VISIBLE code input.
            deadline = time.time() + 30
            state = "unknown"
            while time.time() < deadline:
                time.sleep(1.5)
                if USER in pg.evaluate("document.body.innerText"):
                    state = "in"
                    break
                code_input_visible = pg.evaluate(
                    """() => [...document.querySelectorAll('input')].some(i =>
                         !!i.offsetParent &&
                         /code|verif/i.test((i.placeholder || '') +
                                            (i.getAttribute('aria-label') || '')))""")
                if code_input_visible:
                    state = "challenge"
                    break
        if state == "challenge":
            # New-device gate. If the caller gave us a code channel (a file a
            # human or another process drops the emailed code into), wait for
            # it; otherwise report the refusal.
            code_file = os.environ.get("SLEEPER_CODE_FILE", "")
            if not code_file:
                print("Sleeper wants a verification code — set SLEEPER_CODE_FILE "
                      "to complete the challenge", file=sys.stderr)
                (b or ctx).close()
                return 3
            print("CHALLENGE — waiting for the code file (5 min)", flush=True)
            code = ""
            deadline = time.time() + 300
            while time.time() < deadline and not code:
                if os.path.exists(code_file):
                    code = open(code_file).read().strip()
                time.sleep(2)
            if not code:
                (b or ctx).close()
                return 3
            pg.keyboard.type(code)             # the code box autofocuses
            time.sleep(1)
            try:
                pg.get_by_role("button", name=re.compile(
                    r"continue|verify|submit", re.I)).first.click(timeout=3000)
            except Exception:
                pg.keyboard.press("Enter")
            deadline = time.time() + 20
            state = "unknown"
            while time.time() < deadline:
                time.sleep(1.5)
                if USER in pg.evaluate("document.body.innerText"):
                    state = "in"
                    break
        if state != "in":
            # belt and braces: an authed page shows the user's leagues
            pg.goto("https://sleeper.com/leagues", timeout=30000)
            time.sleep(4)
            if USER not in pg.evaluate("document.body.innerText"):
                print("login did not stick", file=sys.stderr)
                (b or ctx).close()
                return 1
        ctx.storage_state(path=out)
        (b or ctx).close()
        print(f"session saved to {out}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
