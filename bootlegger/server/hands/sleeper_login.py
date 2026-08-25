"""Credential login for sleeper.com — the pilot's auth path.

Hard-won facts about this page (verified live 2026-08-25):

- /login 308-redirects to /?login= : the form is a Radix dialog over the
  homepage, and the dialog renders EVERY panel of the auth flow at once
  (log in, sign up, password reset, verify code, "welcome back"). They all
  stack at the same coordinates.
- Consequently the usual visibility tests all lie here: `offsetParent` is
  null for the fixed dialog, Playwright's `is_visible()` returns true for
  every stacked panel, and `get_by_label("Password").first` resolves to a
  HIDDEN panel's field. Blind filling puts the password into the username
  box.
- The only trustworthy test is hit-testing: `document.elementFromPoint`
  returns what is actually painted. Everything below is built on that.
- The flow is TWO steps: username -> Enter -> password -> Enter.
- **hCaptcha gates the password step for automated browsers** ("drag the
  shape into its outline"). That is a deliberate anti-automation control
  and we do not attempt to defeat it — no solver services, no bypass. So
  credential login is NOT a viable unattended auth path.
- Auth also does not survive export from a fresh automated login: no token
  reaches localStorage, so storage_state() files captured that way do not
  replay in a new context.

CONCLUSION: the pilot authenticates with a storageState captured from the
owner's own everyday browser (see docs — DevTools localStorage capture),
which carries a long-lived token and replays fine. This module remains for
the interactive case and to document the dead end.
"""
from __future__ import annotations

import re
import time


class CaptchaBlocked(RuntimeError):
    """Sleeper demanded an hCaptcha solve. Not something we work around."""


# Probe the painted controls inside the dialog card by hit-testing a grid.
_PAINTED_JS = """() => {
  const dlg = document.querySelector('[role="dialog"][data-state="open"]');
  if (!dlg) return null;
  const b = dlg.getBoundingClientRect();
  const seen = new Map();
  for (let y = b.top + 6; y < b.bottom - 6; y += 8) {
    for (let x = b.left + 10; x < b.right - 10; x += 24) {
      let el = document.elementFromPoint(x, y);
      while (el && el !== dlg) {
        const tag = el.tagName;
        if (tag === 'INPUT' || tag === 'BUTTON' ||
            (tag === 'A' && el.getAttribute('role') === 'button')) {
          const r = el.getBoundingClientRect();
          const key = tag + ':' + Math.round(r.x) + ':' + Math.round(r.y);
          if (!seen.has(key)) seen.set(key, {
            tag, text: (el.innerText || '').trim().slice(0, 30),
            type: el.getAttribute('type'),
            aria: el.getAttribute('aria-label'),
            x: r.x + r.width / 2, y: r.y + r.height / 2 });
          break;
        }
        el = el.parentElement;
      }
    }
  }
  return [...seen.values()];
}"""


def painted(page):
    """Controls actually painted in the auth dialog (or None if no dialog)."""
    return page.evaluate(_PAINTED_JS)


def _click(page, ctrl):
    page.mouse.click(ctrl["x"], ctrl["y"])


def _fill(page, ctrl, value):
    page.mouse.click(ctrl["x"], ctrl["y"])
    page.keyboard.press("Control+A")
    page.keyboard.type(value, delay=25)


def _find(ctrls, tag, pattern=None, type_=None):
    for c in ctrls or []:
        if c["tag"] != tag:
            continue
        if type_ and (c["type"] or "") != type_:
            continue
        if pattern and not re.search(pattern, (c["text"] or "") + (c["aria"] or ""), re.I):
            continue
        return c
    return None


def captcha_present(page) -> bool:
    """hCaptcha challenge on screen — the automated-login dead end."""
    return page.evaluate(
        """() => !!document.querySelector('iframe[src*="hcaptcha"]') ||
                 /drag the shape/i.test(document.body.innerText)""")


def logged_in(page) -> bool:
    """The nav swaps LOG IN / SIGN UP for the account controls."""
    return page.evaluate(
        """() => !document.querySelector('[role="dialog"][data-state="open"]') &&
                 !/\\bLOG IN\\b/.test(document.body.innerText.slice(0, 400))""")


def login(page, user: str, pw: str, timeout_s: float = 60.0) -> bool:
    """Two-step credential login. Idempotent; returns True when the app is
    signed in."""
    page.goto("https://sleeper.com/login", timeout=45000)
    deadline = time.time() + timeout_s
    stage = "user"
    while time.time() < deadline:
        time.sleep(1.5)
        if logged_in(page):
            return True
        if captcha_present(page):
            raise CaptchaBlocked(
                "Sleeper served an hCaptcha challenge — automated credential "
                "login is not available. Use a storageState captured from "
                "your own browser.")
        ctrls = painted(page)
        if not ctrls:
            continue
        # Enter submits each step; the CONTINUE button sits outside the
        # dialog's own rect, so key submission is the reliable path.
        if stage == "user":
            box = _find(ctrls, "INPUT", r"email|phone|username")
            if box:
                _fill(page, box, user)
                page.keyboard.press("Enter")
                stage = "pw"
                time.sleep(3)
                continue
        if stage == "pw":
            pwbox = _find(ctrls, "INPUT", type_="password")
            if pwbox:
                _fill(page, pwbox, pw)
                page.keyboard.press("Enter")
                stage = "done"
                time.sleep(3)
                continue
        # a final "CONTINUE TO WEB" interstitial can appear after auth
        cont = _find(ctrls, "BUTTON", r"continue to web")
        if cont:
            _click(page, cont)
            time.sleep(2.5)
    return logged_in(page)
