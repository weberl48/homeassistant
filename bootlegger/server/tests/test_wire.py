"""The wire: classification, matching, gap detection, and the alert filter.

The load-bearing property under test is restraint. A wire that grades a
reassuring note as a status change, or matches a headline to the wrong Josh
Allen, or buzzes twice for the same item, gets muted by its owner — and a muted
wire is worse than none. Every test here is a way that could happen.
"""
from __future__ import annotations

import json

from app import alerts, db
from app.engines import wire


# --------------------------------------------------------------------------
# Severity
# --------------------------------------------------------------------------

def test_headline_grades_span_the_scale():
    assert wire.severity("Ruled out for Sunday") == "out"
    assert wire.severity("Placed on injured reserve") == "out"
    assert wire.severity("Listed as doubtful") == "doubtful"
    assert wire.severity("Questionable for Week 4") == "questionable"
    assert wire.severity("Did not practice Wednesday") == "practice"
    assert wire.severity("Expected to start Sunday") == "role"
    assert wire.severity("Discusses offseason training") == "info"


def test_first_rule_wins_when_a_headline_carries_two_words():
    # "Ruled out" must beat the bare word "questionable" in the same line —
    # the decision outranks the tag it replaced.
    assert wire.severity("Questionable tag lifted, ruled out for Sunday") == "out"


def test_body_may_escalate_but_never_invents_a_soft_grade():
    """The bug this pins: 'believes he will resume practicing soon' is a
    reassuring note, and reading 'practicing' out of the body graded it as a
    practice report — a status change that never happened."""
    assert wire.severity(
        "Calf injury appears minor",
        'Downs said his calf injury is "very minor" and believes he will '
        "resume practicing soon.") == "info"
    # But a body that carries the actual decision still escalates.
    assert wire.severity(
        "Update on Sunday's availability",
        "Chase has been ruled out for Sunday's game.") == "out"


def test_departures_are_the_ones_that_open_a_job():
    assert wire.is_departure("Placed on injured reserve")
    assert wire.is_departure("Released by the club")
    assert wire.is_departure("Out for the season with a torn ACL")
    assert not wire.is_departure("Questionable for Sunday")
    assert not wire.is_departure("Limited in Wednesday's practice")


def test_ailment_reads_the_parenthetical_only():
    assert wire.ailment("Chase (knee) is not participating in practice.") == "knee"
    assert wire.ailment("Downs said his calf injury is minor.") is None


def test_worse_than_orders_the_scale():
    assert wire.worse_than("out", "questionable")
    assert wire.worse_than("questionable", "info")
    assert not wire.worse_than("practice", "doubtful")


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------

ROWS = [
    {"sleeper_id": "1", "name": "Josh Allen", "pos": "QB", "team": "BUF"},
    {"sleeper_id": "2", "name": "Josh Allen", "pos": "LB", "team": "JAX"},
    {"sleeper_id": "3", "name": "Ja'Marr Chase", "pos": "WR", "team": "CIN"},
    {"sleeper_id": "4", "name": "Michael Carter", "pos": "RB", "team": "ARI"},
    {"sleeper_id": "5", "name": "Michael Carter", "pos": "RB", "team": "NYJ"},
]


def test_a_unique_name_matches():
    idx = wire.build_index(ROWS)
    assert wire.match("Ja'Marr Chase", idx) == "3"
    assert wire.match("JaMarr Chase", idx) == "3"   # punctuation-insensitive


def test_a_fantasy_position_beats_a_defensive_one():
    idx = wire.build_index(ROWS)
    assert wire.match("Josh Allen", idx) == "1"


def test_a_rostered_man_breaks_a_same_position_tie():
    idx = wire.build_index(ROWS)
    assert wire.match("Michael Carter", idx, prefer_ids={"5"}) == "5"


def test_an_unbreakable_tie_refuses_rather_than_guesses():
    """A wrong join pushes 'your starter is out' about the wrong man. Refusing
    leaves the item on the feed under its printed name, which is recoverable."""
    idx = wire.build_index(ROWS)
    assert wire.match("Michael Carter", idx) is None
    assert wire.match("Nobody At All", idx) is None


# --------------------------------------------------------------------------
# Gap detection
# --------------------------------------------------------------------------

def test_overlapping_polls_report_no_gap():
    assert wire.gap_since(100, [99, 100, 101, 102, 103]) == 0
    assert wire.gap_since(100, [101, 102]) == 0


def test_a_jump_past_the_window_proves_missed_items():
    # Last poll ended at 100; this poll's oldest item is 106, so 101-105 were
    # published and never seen.
    assert wire.gap_since(100, [106, 107, 108]) == 5


def test_the_first_poll_has_nothing_to_miss():
    assert wire.gap_since(None, [500, 501]) == 0
    assert wire.gap_since(100, []) == 0


def test_poll_cadence_tightens_toward_kickoff():
    assert wire.poll_interval_seconds(None, in_season=False) == 1800.0
    assert wire.poll_interval_seconds(None, in_season=True) == 900.0
    assert wire.poll_interval_seconds(48.0, in_season=True) == 900.0
    assert wire.poll_interval_seconds(20.0, in_season=True) == 300.0
    assert wire.poll_interval_seconds(1.0, in_season=True) == 120.0
    assert wire.poll_interval_seconds(-1.0, in_season=True) == 120.0


# --------------------------------------------------------------------------
# The alert filter
# --------------------------------------------------------------------------

def _news(conn, guid, pid, headline, body="", severity="out", departure=0):
    conn.execute(
        "INSERT OR REPLACE INTO news(guid,seq,source,player_id,name_raw,headline,body,"
        "link,severity,ailment,departure,published_at,fetched_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (guid, None, "test", pid, "Someone", headline, body, "", severity,
         None, departure, db.utcnow(), db.utcnow()))
    conn.commit()


def test_only_a_starter_in_trouble_rings_the_emergency_channel(conn, monkeypatch):
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(alerts.push, "send",
                        lambda conn, title, body, channel=None, data=None:
                        sent.append((channel, title)) or 1)
    row = conn.execute(
        "SELECT players_json, starters_json FROM rosters WHERE roster_id=?",
        (7,)).fetchone()
    players = json.loads(row["players_json"])
    starters = json.loads(row["starters_json"])
    bench = [p for p in players if p not in starters]

    conn.execute("DELETE FROM news")
    _news(conn, "g-bench", bench[0], "Ruled out for Sunday")
    out = alerts.scan(conn, week=1)
    assert out["alarm"] == 0, "a benched man going Out changes nothing about Sunday"
    assert all(c == alerts.push.CHANNEL_NORMAL for c, _ in sent)

    sent.clear()
    _news(conn, "g-starter", starters[0], "Ruled out for Sunday")
    out = alerts.scan(conn, week=1)
    assert out["alarm"] == 1
    assert sent[0][0] == alerts.push.CHANNEL_EMERGENCY


def test_an_item_notifies_at_most_once(conn, monkeypatch):
    sent = []
    monkeypatch.setattr(alerts.push, "send",
                        lambda conn, title, body, channel=None, data=None:
                        sent.append(title) or 1)
    row = conn.execute(
        "SELECT starters_json FROM rosters WHERE roster_id=?", (7,)).fetchone()
    starters = json.loads(row["starters_json"])
    conn.execute("DELETE FROM news")
    _news(conn, "g-once", starters[0], "Ruled out for Sunday")
    assert alerts.scan(conn, week=1)["alarm"] == 1
    n = len(sent)
    assert alerts.scan(conn, week=1)["considered"] == 0
    assert len(sent) == n, "a re-poll must not re-alarm"


def test_a_rivals_departure_is_a_waiver_window_not_an_emergency(conn, monkeypatch):
    channels = []
    monkeypatch.setattr(alerts.push, "send",
                        lambda conn, title, body, channel=None, data=None:
                        channels.append(channel) or 1)
    theirs = json.loads(conn.execute(
        "SELECT players_json FROM rosters WHERE roster_id<>? LIMIT 1",
        (7,)).fetchone()["players_json"])
    conn.execute("DELETE FROM news")
    _news(conn, "g-rival", theirs[0], "Placed on injured reserve", departure=1)
    out = alerts.scan(conn, week=1)
    assert out["window"] == 1 and out["alarm"] == 0
    assert channels == [alerts.push.CHANNEL_NORMAL]


def test_an_unmatched_item_reaches_the_feed_but_never_a_push(conn, monkeypatch):
    monkeypatch.setattr(alerts.push, "send",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("must not push an unmatched item")))
    conn.execute("DELETE FROM news")
    _news(conn, "g-null", None, "Ruled out for Sunday")
    assert alerts.scan(conn, week=1)["considered"] == 0
    assert any(i["guid"] == "g-null" for i in alerts.feed(conn)["items"])


def test_the_feed_carries_its_own_health(conn):
    f = alerts.feed(conn)
    assert "last_ok" in f and "missed_total" in f and f["source"] == "RotoWire"
