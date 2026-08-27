"""What would it take to get him.

The suggester answers "what deals exist in this room". This answers the
question a manager actually asks out loud, and the properties that matter are
about honesty rather than cleverness: it must not invent offers the other seat
would decline, and it must not rank overpaying first.
"""
import json
import sqlite3

import pytest

from app import brain, db, demo


@pytest.fixture()
def world():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    demo.seed(conn)
    return conn


def _mine(conn):
    return set(json.loads(brain.my_roster_row(conn)["players_json"]))


def _theirs(conn, n=40):
    mine = _mine(conn)
    rows = conn.execute(
        "SELECT c.player_id, p.name, c.vbd FROM consensus c "
        "JOIN players p ON p.sleeper_id = c.player_id "
        "WHERE c.week=0 ORDER BY c.vbd DESC LIMIT ?", (n,)).fetchall()
    return [r for r in rows if r["player_id"] not in mine]


def test_refuses_a_player_you_already_hold(world):
    pid = next(iter(_mine(world)))
    out = brain.what_would_it_take(world, pid)
    assert out["offers"] == []
    assert "already yours" in out["note"]


def test_refuses_a_free_agent(world):
    """He is a waiver add, not a trade — and saying so is more useful than an
    empty list."""
    rostered = set()
    for r in world.execute("SELECT players_json FROM rosters"):
        rostered |= set(json.loads(r["players_json"]))
    free = world.execute(
        "SELECT sleeper_id FROM players WHERE sleeper_id NOT IN "
        "(SELECT player_id FROM consensus WHERE week=0 LIMIT 0)").fetchall()
    fa = next((r["sleeper_id"] for r in free if r["sleeper_id"] not in rostered), None)
    if fa is None:
        pytest.skip("demo league rosters everyone")
    out = brain.what_would_it_take(world, fa)
    assert out["offers"] == []
    assert "waiver" in out["note"]


def test_refuses_an_unknown_player(world):
    out = brain.what_would_it_take(world, "not-a-real-id")
    assert out["offers"] == [] and out["target"] is None


def test_finds_packages_for_gettable_players(world):
    """A generator that returns nothing for everyone is indistinguishable from
    a broken one, so this asserts it actually works across the board."""
    hits = sum(1 for t in _theirs(world)[:12]
               if brain.what_would_it_take(world, t["player_id"])["offers"])
    assert hits >= 6, f"only {hits} of 12 targets produced a package"


def test_every_offer_leaves_the_other_seat_better(world):
    """The filter that makes this a tool rather than a wish list. A package the
    holder declines is not an offer."""
    for t in _theirs(world)[:8]:
        for o in brain.what_would_it_take(world, t["player_id"])["offers"]:
            assert o["their_gain"] >= 0.5, f"{t['name']}: {o['their_gain']}"


def test_every_offer_also_helps_you(world):
    """Getting the man while your own starting nine stands still is a trade
    won on paper for nothing."""
    for t in _theirs(world)[:8]:
        for o in brain.what_would_it_take(world, t["player_id"])["offers"]:
            assert o["my_gain"] >= 1.0, f"{t['name']}: {o['my_gain']}"


def test_the_target_is_always_in_what_you_receive(world):
    """The whole question is how to get HIM. An offer that does not include
    him has answered a different one."""
    for t in _theirs(world)[:8]:
        out = brain.what_would_it_take(world, t["player_id"])
        for o in out["offers"]:
            assert t["player_id"] in o["receive_ids"]


def test_cheapest_band_comes_first(world):
    """Ranking by the holder's gain — which this did on its first run — puts
    the biggest overpay at the top of a list whose entire question is what the
    man costs.

    The assertion is a BAND, not an exact minimum: 75.4 and 75.0 are the same
    price, and inside a band the package that helps your own lineup more is
    plainly the better ask. Strict cost ordering would be false precision.
    """
    checked = 0
    for t in _theirs(world)[:10]:
        offers = brain.what_would_it_take(world, t["player_id"])["offers"]
        if len(offers) < 2:
            continue
        checked += 1
        cheapest = min(o["cost"] for o in offers)
        assert offers[0]["cost"] <= cheapest + 5.0, \
            f"{t['name']}: first costs {offers[0]['cost']}, cheapest is {cheapest}"
    assert checked, "no target produced two offers to compare"


def test_a_real_overpay_never_outranks_a_cheap_package(world):
    """The band breaks ties; it must not let a genuine overpay climb."""
    for t in _theirs(world)[:10]:
        offers = brain.what_would_it_take(world, t["player_id"])["offers"]
        if len(offers) < 2:
            continue
        cheapest = min(o["cost"] for o in offers)
        if max(o["cost"] for o in offers) - cheapest < 15:
            continue
        assert offers[0]["cost"] < cheapest + 15, \
            f"{t['name']}: an overpay at {offers[0]['cost']} led over {cheapest}"


def test_you_never_offer_a_man_you_do_not_have(world):
    mine = _mine(world)
    for t in _theirs(world)[:8]:
        for o in brain.what_would_it_take(world, t["player_id"])["offers"]:
            assert set(o["give_ids"]) <= mine


def test_a_cornerstone_can_be_unavailable(world):
    """"Nothing you own gets him" is a real answer and must be sayable. A
    generator that always finds something is flattering, not useful."""
    outs = [brain.what_would_it_take(world, t["player_id"])
            for t in _theirs(world)[:12]]
    assert any(not o["offers"] for o in outs), \
        "every single target was gettable — the holder's filter is not binding"
