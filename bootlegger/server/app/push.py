"""Outbound-only push via Expo's service (design doc §2 [push]). Channels:
'recommendations' (normal) and 'game-time-emergency' (DND-bypass, defined on
the Android side). With no tokens registered, every push degrades to a log
line — visible, never silent."""
from __future__ import annotations

import logging
import sqlite3
from typing import Any

import httpx

from .config import settings

log = logging.getLogger("bootlegger.push")
EXPO_URL = "https://exp.host/--/api/v2/push/send"

CHANNEL_NORMAL = "recommendations"
CHANNEL_EMERGENCY = "game-time-emergency"


def _tokens(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT push_token FROM devices").fetchall()
    return list({r["push_token"] for r in rows} | set(settings.expo_push_tokens))


def send(conn: sqlite3.Connection, title: str, body: str,
         channel: str = CHANNEL_NORMAL, data: dict[str, Any] | None = None) -> int:
    """Returns the number of device messages attempted. 0 = logged only."""
    tokens = _tokens(conn)
    log.info("push [%s] %s — %s (%d devices)", channel, title, body, len(tokens))
    if not tokens:
        return 0
    messages = [{
        "to": t, "title": title, "body": body,
        "channelId": channel,
        "priority": "high" if channel == CHANNEL_EMERGENCY else "default",
        "data": data or {},
    } for t in tokens]
    try:
        r = httpx.post(EXPO_URL, json=messages, timeout=15,
                       headers={"Accept": "application/json"})
        r.raise_for_status()
    except httpx.HTTPError as e:
        # Failure to notify is itself a failure mode that must not be silent.
        log.error("push delivery failed: %s", e)
        return 0

    # Expo answers 200 for a request it PARSED, and reports each message's fate
    # in the body. Returning len(messages) here counted a rejected notification
    # as a delivered one — so the subsystem whose entire purpose is never
    # failing quietly was the one reporting success into a void. Verified
    # against the live service: a token belonging to no device comes back
    # HTTP 200, status "error", DeviceNotRegistered.
    try:
        tickets = (r.json() or {}).get("data") or []
    except ValueError:
        log.error("push: Expo returned a non-JSON body; delivery unconfirmed")
        return 0

    ok, dead = 0, []
    for token, ticket in zip(tokens, tickets):
        if (ticket or {}).get("status") == "ok":
            ok += 1
            continue
        detail = ((ticket or {}).get("details") or {}).get("error")
        log.error("push rejected for %s: %s (%s)", token[-12:],
                  (ticket or {}).get("message"), detail)
        if detail == "DeviceNotRegistered":
            dead.append(token)

    # Expo asks that a DeviceNotRegistered token be dropped rather than retried
    # forever; a reinstalled app registers a fresh one on next launch.
    if dead:
        conn.executemany("DELETE FROM devices WHERE push_token=?",
                         [(t,) for t in dead])
        conn.commit()
        log.warning("push: dropped %d unregistered device(s)", len(dead))

    if ok < len(messages):
        log.error("push: %d of %d messages accepted", ok, len(messages))
    return ok
