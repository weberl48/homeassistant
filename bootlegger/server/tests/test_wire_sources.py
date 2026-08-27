"""Five newsrooms on one wire.

The wire was single-source in production: PFT had a fetcher and nothing ever
called it. It now runs RotoWire plus four untagged feeds, which changes two
things about what can go wrong — a feed can die on its own, and the same story
arrives several times — so both get tested rather than assumed.

Every test here uses stub fetchers. The point is the assembly logic, not
whether ESPN is up.
"""
import json
import sqlite3

import pytest

from app import alerts, db, demo, ingest
from app.engines import wire


@pytest.fixture()
def world():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    demo.seed(conn)
    return conn


def _named_player(conn):
    r = conn.execute("SELECT sleeper_id, name FROM players LIMIT 1").fetchone()
    return r["sleeper_id"], r["name"]


def _stub(monkeypatch, *, rotowire=None, general=None, fail=()):
    monkeypatch.setattr(ingest, "fetch_rotowire_news",
                        lambda *a, **k: list(rotowire or []))
    monkeypatch.setattr(ingest.sources, "GENERAL_RSS",
                        {"pft": "x", "espn": "x", "cbs": "x", "yahoo": "x"})

    def fake(source, timeout=25.0):
        if source in fail:
            raise RuntimeError(f"{source} exploded")
        return list((general or {}).get(source, []))
    monkeypatch.setattr(ingest.sources, "fetch_general_news", fake)


def _item(guid, title, body=""):
    return {"guid": guid, "title": title, "body": body,
            "link": f"http://x/{guid}", "published_at": "2026-09-20T12:00:00+00:00"}


def test_all_five_sources_are_polled(world, monkeypatch):
    """The regression this whole change exists for: PFT was defined and never
    called, so the wire ran on one feed while the schema documented two."""
    _, name = _named_player(world)
    _stub(monkeypatch,
          rotowire=[{"guid": "rw-1", "seq": 10, "name": name,
                     "headline": "Ruled out for Sunday", "body": "",
                     "link": "http://rw/1", "published_at": None}],
          general={s: [_item(f"{s}-1", f"{name} ruled out for Sunday")]
                   for s in ("pft", "espn", "cbs", "yahoo")})
    out = ingest.etl_news(world)
    assert out["live_sources"] == 5 and out["of"] == 5
    got = {r["source"] for r in world.execute("SELECT DISTINCT source FROM news")}
    assert {"rotowire", "pft", "espn", "cbs", "yahoo"} <= got


def test_one_dead_feed_costs_only_its_own_items(world, monkeypatch):
    """A feed that dies degrades to a VISIBLE hole, never to a poll that
    quietly returns fewer sources than it did yesterday."""
    _, name = _named_player(world)
    _stub(monkeypatch,
          rotowire=[],
          general={s: [_item(f"{s}-1", f"{name} is fine")] for s in ("pft", "espn", "yahoo")},
          fail=("cbs",))
    out = ingest.etl_news(world)
    assert out["sources"]["cbs"]["ok"] is False
    assert out["sources"]["cbs"]["error"] == "RuntimeError"
    assert out["sources"]["espn"]["ok"] is True
    assert out["live_sources"] == 4, "the other four must be unaffected"


def test_a_dead_feed_is_named_on_the_board(world, monkeypatch):
    """"4 of 5 feeds" tells you something is wrong; "CBS is down" tells you
    what. The board gets the second."""
    _stub(monkeypatch, rotowire=[], general={}, fail=("cbs", "yahoo"))
    ingest.etl_news(world)
    f = alerts.feed(world)
    assert set(f["down"]) == {"cbs", "yahoo"}
    assert "cbs" not in f["live"] and "espn" in f["live"]


def test_rotowire_dying_does_not_take_the_wire_with_it(world, monkeypatch):
    """RotoWire is the only feed that can prove a gap, so it is fetched
    separately — which is exactly the code path that could drop everything
    else on the floor if it raised."""
    _, name = _named_player(world)

    def boom(*a, **k):
        raise RuntimeError("rotowire down")
    monkeypatch.setattr(ingest, "fetch_rotowire_news", boom)
    monkeypatch.setattr(ingest.sources, "GENERAL_RSS", {"espn": "x"})
    monkeypatch.setattr(ingest.sources, "fetch_general_news",
                        lambda s, timeout=25.0: [_item("e-1", f"{name} ruled out for Sunday")])
    out = ingest.etl_news(world)
    assert out["sources"]["rotowire"]["ok"] is False
    assert out["fetched"] >= 1, "the other feeds still delivered"


def test_the_gap_claim_stays_a_rotowire_property(world, monkeypatch):
    """Only RotoWire stamps monotonic ids. Claiming a gap number for feeds
    that cannot support one would be a fabricated guarantee."""
    _stub(monkeypatch, rotowire=[],
          general={"espn": [_item(f"e-{i}", f"story {i}") for i in range(20)]})
    out = ingest.etl_news(world)
    assert out["gap"] == 0
    rows = world.execute(
        "SELECT seq FROM news WHERE source='espn'").fetchall()
    assert all(r["seq"] is None for r in rows), "an untagged feed has no sequence"


# --- corroboration ----------------------------------------------------------

def _feed_item(pid, sev, day, source, name="Man"):
    return {"guid": f"{source}-{day}-{sev}", "player_id": pid, "name": name,
            "pos": "WR", "team": "TB", "headline": "h", "body": "", "link": "",
            "severity": sev, "ailment": None, "departure": False,
            "published_at": f"{day}T12:00:00+00:00", "source": source,
            "audience": "mine"}


def test_the_same_story_collapses_and_keeps_the_witnesses():
    """Three desks reporting one fact is a firmer fact, not three facts. The
    row survives once and names who else has it."""
    items = [_feed_item("p1", "out", "2026-09-20", s) for s in ("yahoo", "cbs", "espn")]
    out = alerts.corroborate(items)
    assert len(out) == 1
    assert sorted(out[0]["also"]) == ["cbs", "espn"]
    assert out[0]["source"] == "yahoo", "the first to file keeps the byline"


def test_different_grades_are_different_stories():
    """Questionable on Friday and out on Sunday is a development, not a
    duplicate — collapsing them would hide the thing that changed."""
    items = [_feed_item("p1", "questionable", "2026-09-20", "cbs"),
             _feed_item("p1", "out", "2026-09-20", "espn")]
    assert len(alerts.corroborate(items)) == 2


def test_different_days_are_different_stories():
    items = [_feed_item("p1", "out", "2026-09-20", "cbs"),
             _feed_item("p1", "out", "2026-09-27", "cbs")]
    assert len(alerts.corroborate(items)) == 2


def test_unmatched_items_are_never_merged():
    """Without a player id the only thing two items share is prose. Merging on
    prose alone would silently hide genuinely different news."""
    items = [_feed_item(None, "info", "2026-09-20", s) for s in ("cbs", "espn", "yahoo")]
    assert len(alerts.corroborate(items)) == 3
