"""Regressions from the max-effort review of 2026-08-27.

Every case here was introduced by this session's own work and proven by the
reviewer before it was fixed. They are grouped by what they cost rather than by
file, because the cost is what makes each one worth a permanent test.
"""
import json
import sqlite3

import pytest

from app import alerts, brain, db, demo, ingest, push
from app.config import settings


@pytest.fixture()
def world():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    demo.seed(conn)
    return conn


# --- one story must not become five pushes ---------------------------------

def _news(conn, guid, source, pid, name, headline, severity, day="2026-09-20"):
    conn.execute(
        "INSERT INTO news(guid,seq,source,player_id,name_raw,headline,body,link,"
        "severity,ailment,departure,published_at,fetched_at) "
        "VALUES(?,NULL,?,?,?,?,'','',?,NULL,0,?,?)",
        (guid, source, pid, name, headline, severity,
         f"{day}T12:00:00+00:00", db.utcnow()))


def test_five_desks_one_story_is_one_push(world, monkeypatch):
    """The wire went from one newsroom to five. Five rows carry five distinct
    guids for one fact, so every existing dedupe passes each of them — and the
    duplicates burned MAX_PUSHES, so the pass's genuinely different news was
    stamped seen and never sent at all.
    """
    me = brain.my_roster_row(world)
    starter = json.loads(me["starters_json"])[0]
    name = world.execute("SELECT name FROM players WHERE sleeper_id=?",
                         (starter,)).fetchone()["name"]
    world.execute("DELETE FROM news")
    for src in ("rotowire", "espn", "cbs", "yahoo", "pft"):
        _news(world, f"{src}-1", src, starter, name, "Ruled out for Sunday", "out")
    world.commit()

    sent: list = []
    monkeypatch.setattr(push, "send", lambda *a, **k: sent.append(a))
    out = alerts.scan(world)

    assert out["alarm"] == 1, "one story, one alarm"
    assert len(sent) == 1, f"one story sent {len(sent)} pushes"
    seen = world.execute(
        "SELECT COUNT(*) c FROM news WHERE seen_at IS NOT NULL").fetchone()["c"]
    assert seen == 5, "every duplicate must still be marked seen, or it lingers"


def test_two_different_stories_still_both_push(world, monkeypatch):
    """The collapse must key on the STORY, not merely on the player: a grade
    change is news, and suppressing it would be worse than the duplicates."""
    me = brain.my_roster_row(world)
    starters = json.loads(me["starters_json"])
    rows = [(starters[0], "out"), (starters[1], "doubtful")]
    world.execute("DELETE FROM news")
    for i, (pid, sev) in enumerate(rows):
        nm = world.execute("SELECT name FROM players WHERE sleeper_id=?",
                           (pid,)).fetchone()["name"]
        _news(world, f"g{i}", "espn", pid, nm, "Ruled out for Sunday", sev)
    world.commit()
    sent: list = []
    monkeypatch.setattr(push, "send", lambda *a, **k: sent.append(a))
    alerts.scan(world)
    assert len(sent) == 2, "two different men are two stories"


# --- a dead wire must never read as a healthy one --------------------------

def test_every_feed_failing_is_not_a_successful_poll(world, monkeypatch):
    """etl_news stamped wire_last_ok on its empty-result early return, so a
    poll in which all five feeds raised advanced the freshness clock and left
    fail_streak at zero. A totally dead wire rendered as a quiet news day."""
    db.meta_set(world, "wire_last_ok", "2020-01-01T00:00:00+00:00")

    def boom(*a, **k):
        raise RuntimeError("feed down")
    monkeypatch.setattr(ingest, "fetch_rotowire_news", boom)
    monkeypatch.setattr(ingest.sources, "GENERAL_RSS", {"espn": "x", "cbs": "x"})
    monkeypatch.setattr(ingest.sources, "fetch_general_news", boom)

    with pytest.raises(RuntimeError, match="every wire source failed"):
        ingest.etl_news(world)
    assert db.meta_get(world, "wire_last_ok") == "2020-01-01T00:00:00+00:00", \
        "a failed poll must not advance the freshness clock"


def test_a_genuinely_quiet_wire_still_counts_as_a_poll(world, monkeypatch):
    """The other half: feeds that answer with nothing new are a quiet news
    day, and that MUST keep the clock moving or the board cries outage."""
    db.meta_set(world, "wire_last_ok", "2020-01-01T00:00:00+00:00")
    monkeypatch.setattr(ingest, "fetch_rotowire_news", lambda *a, **k: [])
    monkeypatch.setattr(ingest.sources, "GENERAL_RSS", {"espn": "x"})
    monkeypatch.setattr(ingest.sources, "fetch_general_news", lambda *a, **k: [])
    out = ingest.etl_news(world)
    assert out["fetched"] == 0
    assert db.meta_get(world, "wire_last_ok") != "2020-01-01T00:00:00+00:00"


# --- the trade tools ---------------------------------------------------------

def test_counter_offers_search_the_whole_roster(world):
    """Pairs were built from mine[:8] of a worth-DESCENDING sort, which cut off
    exactly the cheap throw-ins a two-man package needs. On this fixture the
    only package that clears every filter for Tyreek Hill is rank 2 plus rank
    10, and the panel used to answer 'nothing you own gets him'."""
    hit = world.execute(
        "SELECT sleeper_id FROM players WHERE name LIKE 'Tyreek%'").fetchone()
    if not hit:
        pytest.skip("fixture has no Tyreek Hill")
    out = brain.what_would_it_take(world, hit["sleeper_id"])
    assert out["offers"], "the deep-throw-in package must be reachable"
    desk = brain._TradeDesk(world)
    ranked = sorted((i for i in desk.my_ids if i in desk.players),
                    key=desk.worth, reverse=True)
    depth = max(ranked.index(g) for o in out["offers"] for g in o["give_ids"])
    assert depth >= 8, f"deepest man used is rank {depth} — slice may be back"


def test_the_trade_schedule_chip_looks_forward(world):
    """_TradeDesk read schedule_strength at from_week=1, averaging every
    already-played week's closing line — a season-to-date review sold as a
    look-ahead, which can carry the opposite sign of the schedule you are
    buying."""
    world.execute("DELETE FROM nfl_games")
    for wk, imp in ((1, 32.0), (2, 32.0), (3, 32.0), (9, 15.5), (10, 15.5)):
        world.execute(
            "INSERT INTO nfl_games(season,week,team,opponent,is_home,implied_total) "
            "VALUES(?,?,?,?,1,?)", (settings.season, wk, "ZZZ", "OPP", imp))
    # a league around 22-23 so ZZZ's early weeks read high and late weeks low
    for wk in range(1, 11):
        for t in range(8):
            world.execute(
                "INSERT INTO nfl_games(season,week,team,opponent,is_home,implied_total) "
                "VALUES(?,?,?,?,1,?)", (settings.season, wk, f"T{t}", "OPP", 22.5))
    db.meta_set(world, "current_week", "9")
    world.commit()

    back = brain.schedule_strength(world, from_week=1)
    fwd = brain.schedule_strength(world, from_week=9)
    b = next(t["vs_league"] for t in back["teams"] if t["team"] == "ZZZ")
    f = next(t["vs_league"] for t in fwd["teams"] if t["team"] == "ZZZ")
    assert b > 0 > f, f"fixture must invert: looking back {b}, forward {f}"

    desk = brain._TradeDesk(world)
    assert desk.sos.get("ZZZ") == pytest.approx(f, abs=0.05), \
        "the trade chip must read the schedule that is still to come"
