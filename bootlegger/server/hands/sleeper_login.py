"""Credential login for sleeper.com — the pilot's primary auth since 2026-08-25.

Findings that shaped this (all verified live):
- Password login completes in ~2s with NO verification challenge.
- A fresh web login keeps its auth as session-state only: no token ever
  lands in localStorage (90s watch), so storage_state() exports do NOT
  replay in a new context, and even a persistent profile reopens logged
  out. Exported-session auth only works when captured from a long-lived
  everyday browser (legacy token in localStorage).
- Therefore: log in fresh at launch and KEEP THE BROWSER OPEN. For a
  draft-night worker that's no cost at all.

Sleeper's login page keeps every panel in the DOM (reset flow included), so
body-text sniffing lies — only the username appearing in rendered content
counts as success.
"""
from __future__ import annotations

import re
import time


def login(page, user: str, pw: str, timeout_s: float = 40.0) -> bool:
    """Returns True once the app shows `user`. Idempotent — an already
    signed-in page short-circuits."""
    page.goto("https://sleeper.com/login", timeout=45000)
    time.sleep(4)
    if user in page.evaluate("document.body.innerText"):
        return True
    page.get_by_label("Email, phone, or username").first.fill(user)
    page.get_by_label("Password").first.fill(pw)
    # the visible "LOG IN" is CSS-uppercased; match the DOM text loosely
    page.get_by_role("button", name=re.compile(r"log\s*in", re.I)).first.click()
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(1.5)
        if user in page.evaluate("document.body.innerText"):
            return True
    return False
