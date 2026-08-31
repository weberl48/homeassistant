"""Push must not report success into a void.

Confirmed against the live Expo service on 2026-08-31: a token belonging to no
device comes back **HTTP 200** with `status: "error"`,
`details.error: "DeviceNotRegistered"`. The old send() called
raise_for_status(), saw 200, and returned len(messages) — so the one subsystem
whose entire purpose is never failing quietly logged a delivery that never
happened. These tests pin the fix.
"""
from __future__ import annotations

import httpx
import pytest

from app import db, push


@pytest.fixture()
def wired(conn, monkeypatch):
    """Two registered devices and a controllable Expo."""
    for tok in ("ExponentPushToken[aaaaaaaaaaaaaaaaaaaaaa]",
                "ExponentPushToken[bbbbbbbbbbbbbbbbbbbbbb]"):
        conn.execute("INSERT OR REPLACE INTO devices(push_token,platform,created_at) "
                     "VALUES(?,?,?)", (tok, "android", db.utcnow()))
    conn.commit()
    return conn


def _expo(monkeypatch, payload, status=200):
    def fake_post(url, **kw):
        return httpx.Response(status, json=payload,
                              request=httpx.Request("POST", url))
    monkeypatch.setattr(push.httpx, "post", fake_post)


def _expo_rejecting(monkeypatch, bad_token, error="DeviceNotRegistered"):
    """Reject one NAMED token, whichever position it lands in.

    push._tokens() returns a set union, so device order varies between runs.
    send() builds its messages from that same list and Expo answers in message
    order, so the pairing inside send() is sound — but a test that assumes
    "the first device is the aaaa one" is flaky by construction, and this one
    was until it failed.
    """
    def fake_post(url, **kw):
        sent = kw["json"]
        data = [{"status": "ok", "id": "x"} if m["to"] != bad_token else
                {"status": "error", "message": "nope", "details": {"error": error}}
                for m in sent]
        return httpx.Response(200, json={"data": data},
                              request=httpx.Request("POST", url))
    monkeypatch.setattr(push.httpx, "post", fake_post)


DEAD = "ExponentPushToken[bbbbbbbbbbbbbbbbbbbbbb]"
ALIVE = "ExponentPushToken[aaaaaaaaaaaaaaaaaaaaaa]"


def test_a_rejected_message_is_not_counted_as_delivered(wired, monkeypatch):
    _expo_rejecting(monkeypatch, DEAD)
    assert push.send(wired, "t", "b") == 1, "both counted; one was refused"


def test_every_message_rejected_reports_zero(wired, monkeypatch):
    """The exact live shape. HTTP 200 and nobody was notified."""
    _expo(monkeypatch, {"data": [
        {"status": "error", "message": "not registered",
         "details": {"error": "DeviceNotRegistered"}}] * 2})
    assert push.send(wired, "t", "b") == 0


def test_a_dead_token_is_dropped_not_retried_forever(wired, monkeypatch):
    _expo_rejecting(monkeypatch, DEAD)
    push.send(wired, "t", "b")
    left = [r["push_token"] for r in wired.execute("SELECT push_token FROM devices")]
    assert left == [ALIVE], f"dropped the wrong device: {left}"


def test_a_transient_error_keeps_the_token(wired, monkeypatch):
    """MessageRateExceeded is Expo asking for patience, not a dead device.
    Dropping the token would silence the phone permanently."""
    _expo_rejecting(monkeypatch, DEAD, error="MessageRateExceeded")
    assert push.send(wired, "t", "b") == 1
    assert wired.execute("SELECT COUNT(*) FROM devices").fetchone()[0] == 2


def test_all_accepted_reports_all(wired, monkeypatch):
    _expo(monkeypatch, {"data": [{"status": "ok", "id": "1"},
                                 {"status": "ok", "id": "2"}]})
    assert push.send(wired, "t", "b") == 2


def test_a_non_json_body_is_not_read_as_success(wired, monkeypatch):
    def fake_post(url, **kw):
        return httpx.Response(200, content=b"<html>maintenance</html>",
                              request=httpx.Request("POST", url))
    monkeypatch.setattr(push.httpx, "post", fake_post)
    assert push.send(wired, "t", "b") == 0


def test_no_devices_is_still_zero_and_never_calls_out(conn, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("called Expo with nothing to send")
    monkeypatch.setattr(push.httpx, "post", boom)
    assert push.send(conn, "t", "b") == 0
