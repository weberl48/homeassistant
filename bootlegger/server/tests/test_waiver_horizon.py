"""The street, on the horizon the decision actually has.

Every test here pins a defect the live board shipped on 2026-08-31 and that
307 green tests did not see, because the demo fixture's street holds five men
and its roster has no season-ending tags.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from app import brain, db, demo
from app.config import settings
from app.engines import waivers as waivers_engine


@pytest.fixture()
def world():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    demo.seed(conn)
    return conn


def _my_ids(conn):
    row = conn.execute("SELECT players_json FROM rosters WHERE roster_id=?",
                       (settings.my_roster_id,)).fetchone()
    return json.loads(row["players_json"])


def _free_agent(conn, pid, name, pos, team, season, week):
    """Put one man on the street with both horizons stated."""
    conn.execute(
        "INSERT OR REPLACE INTO players(sleeper_id,name,pos,team,status,updated_at) "
        "VALUES(?,?,?,?, 'Active', '2026-08-31T00:00:00+00:00')",
        (pid, name, pos, team))
    for wk, pts in ((0, season), (1, week)):
        conn.execute(
            "INSERT OR REPLACE INTO consensus(player_id,week,pts_mean,pts_robust,"
            "stdev,tier,vbd) VALUES(?,?,?,?,1.0,3,0.0)", (pid, wk, pts, pts))
    conn.commit()


# ---------------------------------------------------------------------------
# The horizon
# ---------------------------------------------------------------------------

def test_a_weekly_decision_is_not_scored_on_season_totals(world):
    """The Cleveland-over-Jacksonville regression.

    The live board told the owner to bid on Cleveland (4.97 projected in week
    1) and Kansas City (5.66) to replace his own Jacksonville, which at 8.19
    was the highest-projected defense in the league that week. Every figure on
    the row was right; the advice was backwards, because `lineup_gain` was a
    season number wearing the label "starts". A man who is worse than your own
    starter THIS WEEK must never be presented as a lineup gain this week.
    """
    my = _my_ids(world)
    mine = {p["sleeper_id"]: p for p in world.execute("SELECT * FROM players")}
    my_def = next((pid for pid in my if mine[pid]["pos"] == "DEF"), None)
    assert my_def, "the demo roster carries a defense"

    # Mine: mediocre on the season, best in the league this week.
    for wk, pts in ((0, 90.0), (1, 14.0)):
        world.execute("UPDATE consensus SET pts_robust=?, pts_mean=? "
                      "WHERE player_id=? AND week=?", (pts, pts, my_def, wk))
    # Street: better on the season, much worse this week — exactly the shape
    # that fooled the old arithmetic.
    _free_agent(world, "FA-DEF", "Paper Tigers", "DEF", "PAP", 130.0, 3.0)

    out = brain.waiver_targets(world)
    row = next((r for r in out["targets"] + out["streamers"]
                if r["id"] == "FA-DEF"), None)
    assert row is not None, "he is a legitimate rest-of-season upgrade"
    # The inversion itself, pinned: the season horizon says add him, the week
    # horizon says he would cost you points on Sunday. Before the week lane
    # existed only the first number was computed, and it was labelled "starts".
    assert row["lineup_gain"] > 0, "fixture must actually exercise the inversion"
    assert row["week_gain"] <= 0, (
        "a defense projected 3.0 this week cannot be a lineup gain over a "
        f"rostered one projected 14.0 — got week_gain {row['week_gain']}")
    assert all(r["id"] != "FA-DEF" for r in out["streamers"]), \
        "he must not reach the streaming lane at all"


def test_a_streamer_beats_your_starter_this_week_only(world):
    """The other direction: a man who is nothing on the season and the right
    play on Sunday belongs on the page. The season lane will never find him,
    which is why there are two lanes."""
    my = _my_ids(world)
    mine = {p["sleeper_id"]: p for p in world.execute("SELECT * FROM players")}
    my_def = next(pid for pid in my if mine[pid]["pos"] == "DEF")
    for wk, pts in ((0, 120.0), (1, 4.0)):
        world.execute("UPDATE consensus SET pts_robust=?, pts_mean=? "
                      "WHERE player_id=? AND week=?", (pts, pts, my_def, wk))
    _free_agent(world, "FA-STREAM", "Sunday Only", "DEF", "SUN", 40.0, 15.0)

    out = brain.waiver_targets(world)
    ids = [r["id"] for r in out["streamers"]]
    assert "FA-STREAM" in ids, (
        "a 15-point week-1 defense over a 4-point one is the streaming lane's "
        f"entire purpose; streamers were {ids}")
    row = next(r for r in out["streamers"] if r["id"] == "FA-STREAM")
    assert row["week_gain"] > 0
    assert row["bid"] >= 1


def test_a_streamer_on_bye_is_not_a_streamer(world):
    """He cannot help the week you bought him."""
    my = _my_ids(world)
    mine = {p["sleeper_id"]: p for p in world.execute("SELECT * FROM players")}
    my_def = next(pid for pid in my if mine[pid]["pos"] == "DEF")
    for wk, pts in ((0, 120.0), (1, 4.0)):
        world.execute("UPDATE consensus SET pts_robust=?, pts_mean=? "
                      "WHERE player_id=? AND week=?", (pts, pts, my_def, wk))
    _free_agent(world, "FA-BYE", "Bye Week", "DEF", "BYE", 40.0, 15.0)
    world.execute("UPDATE players SET bye=1 WHERE sleeper_id='FA-BYE'")
    world.commit()

    out = brain.waiver_targets(world)
    assert all(r["id"] != "FA-BYE" for r in out["streamers"]), \
        "a man on bye in the target week has no week gain"


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def test_the_bar_is_the_man_you_would_cut_not_your_worst_at_the_position(world):
    """455 free agents filtered to two, silently.

    `fa_score` measured a man against the weakest body at his OWN position. On
    a fifteen-man roster your worst receiver is your WR5, so a receiver on the
    street had to beat a bench asset to appear at all — while the drop line at
    the bottom of the same page said cutting a running back cost the lineup
    nothing. The comparison must be against the man who would actually leave.
    """
    my = _my_ids(world)
    mine = {p["sleeper_id"]: p for p in world.execute("SELECT * FROM players")}
    cons = {r["player_id"]: r["pts_robust"] for r in
            world.execute("SELECT player_id, pts_robust FROM consensus WHERE week=0")}
    my_wrs = sorted((pid for pid in my if mine[pid]["pos"] == "WR"),
                    key=lambda p: cons.get(p, 0.0))
    assert my_wrs, "the demo roster carries receivers"
    worst_wr = cons[my_wrs[0]]

    out = brain.waiver_targets(world)
    replacement = out["replacement"]
    assert replacement is not None, "the demo shelf has a spare body"
    # The fixture is only interesting when the two bars actually differ.
    assert worst_wr > replacement["pts"], (
        "fixture no longer exercises the bug: the worst receiver is already "
        "the droppable man")

    # A receiver between the two bars: invisible under the old rule, a
    # legitimate add under the new one.
    between = (worst_wr + replacement["pts"]) / 2
    _free_agent(world, "FA-WR", "Middle Man", "WR", "MID", between, 9.0)

    out = brain.waiver_targets(world)
    row = next((r for r in out["targets"] if r["id"] == "FA-WR"), None)
    assert row is not None, (
        f"a receiver at {between:.1f} beats the droppable man at "
        f"{replacement['pts']:.1f} and must appear")
    assert row["fa_score"] < 0, "and his 'over your worst WR' number is honestly negative"
    assert row["over_drop"] > 0


def test_a_season_inactive_body_does_not_set_the_bar(world):
    """An IR'd receiver is not a standard the street has to beat. He used to
    hold the positional bar at his healthy projection for the rest of the
    year, because `worst_by_pos` read raw points while every other rest-of-
    season site in this file called `ros_status` first."""
    my = _my_ids(world)
    mine = {p["sleeper_id"]: p for p in world.execute("SELECT * FROM players")}
    my_wr = next(pid for pid in my if mine[pid]["pos"] == "WR")
    world.execute("UPDATE players SET injury_status='IR' WHERE sleeper_id=?", (my_wr,))
    world.execute("UPDATE consensus SET pts_robust=1.0, pts_mean=1.0 "
                  "WHERE player_id=? AND week=0", (my_wr,))
    world.commit()

    out = brain.waiver_targets(world)
    wr_rows = [r for r in out["targets"] + out["streamers"] if r["pos"] == "WR"]
    for r in wr_rows:
        assert r["fa_score"] != r["ros"] - 1.0, \
            "an IR'd man at 1.0 points must not be the positional baseline"


# ---------------------------------------------------------------------------
# The price
# ---------------------------------------------------------------------------

def test_the_best_of_a_barren_week_does_not_buy_the_top_of_the_book(world):
    """`value_pct` was the target's RANK inside the shortlist, so rank 1 —
    which exists every single week — always drew the 100th percentile of this
    room's bids. In the demo that quoted $51 of a $100 budget for a marginal
    back. The price has to be a statement about the man, not about the shape
    of the list he happens to top."""
    my = _my_ids(world)
    cons = {r["player_id"]: r["pts_robust"] for r in
            world.execute("SELECT player_id, pts_robust FROM consensus WHERE week=0")}
    out0 = brain.waiver_targets(world)
    replacement = out0["replacement"]
    assert replacement is not None

    # One man, barely better than the body you'd cut: the best available in a
    # week with nothing on it.
    _free_agent(world, "FA-MARGIN", "Barely Better", "WR", "MRG",
                replacement["pts"] + 2.0, 6.0)
    out = brain.waiver_targets(world)
    assert out["history_n"] > 0, "the demo carries a bid book"
    row = next((r for r in out["targets"] if r["id"] == "FA-MARGIN"), None)
    assert row is not None

    top_of_book = max(
        r["faab"] for r in world.execute(
            "SELECT faab FROM transactions WHERE type='waiver' AND faab IS NOT NULL"))
    assert row["bid"] < top_of_book / 2, (
        f"two points over the droppable man was priced at ${row['bid']} "
        f"against a book topping out at ${top_of_book}")
    assert not row["hard_confirm"], "and it must not trip the big-swing warning"


def test_price_tracks_value_not_position_in_the_list(world):
    """Two men, one twice as far above the drop as the other, must not price
    the same — and the better one must not price lower."""
    out0 = brain.waiver_targets(world)
    base = out0["replacement"]["pts"]
    span = out0["roster_span"]
    _free_agent(world, "FA-BIG", "Big Add", "WR", "BIG", base + span * 0.6, 12.0)
    _free_agent(world, "FA-SMALL", "Small Add", "WR", "SML", base + span * 0.1, 5.0)

    out = brain.waiver_targets(world)
    by_id = {r["id"]: r for r in out["targets"]}
    assert "FA-BIG" in by_id and "FA-SMALL" in by_id
    assert by_id["FA-BIG"]["bid"] > by_id["FA-SMALL"]["bid"], (
        f"big {by_id['FA-BIG']['bid']} vs small {by_id['FA-SMALL']['bid']}")


def test_value_fraction_is_scale_free_and_clamped():
    vf = waivers_engine.value_fraction
    assert vf(0.0, 200.0) == 0.0
    assert vf(200.0, 200.0) == 1.0
    assert vf(400.0, 200.0) == 1.0, "clamped above"
    assert vf(-10.0, 200.0) == 0.0, "clamped below"
    assert vf(50.0, 200.0) == pytest.approx(0.25)
    # Same ratio in week units gives the same answer — the whole point.
    assert vf(5.0, 20.0) == pytest.approx(0.25)
    assert vf(1.0, 0.0) == 1.0, "a roster with no width: any edge is the whole edge"


# ---------------------------------------------------------------------------
# Never a silent short list
# ---------------------------------------------------------------------------

def test_an_empty_street_says_so_with_the_number_behind_it(world):
    """The live page rendered two rows out of 455 free agents and said
    nothing about the other 453. 'Never silently fail' is principle #1."""
    my = _my_ids(world)
    # Make every free agent worthless so the upgrade lane empties honestly.
    world.execute(
        "UPDATE consensus SET pts_robust=0.5, pts_mean=0.5 WHERE week=0 "
        "AND player_id NOT IN (SELECT j.value FROM rosters r, json_each(r.players_json) j)")
    world.commit()

    out = brain.waiver_targets(world)
    assert out["targets"] == []
    assert out["pool"] > 0, "the street still exists even when nothing on it clears"
    assert out["considered"] > 0
    assert out["replacement"] is not None
    assert out["replacement"]["name"].lower() in out["note"].lower(), \
        "the note must name the man the street failed to beat"


def test_payload_carries_its_own_provenance(world):
    """The page captioned every bid 'P100 of this room's book' while the
    payload said the book was empty. Both facts have to be on the wire so the
    client cannot invent the second one."""
    out = brain.waiver_targets(world)
    assert set(out) >= {"targets", "streamers", "history_n", "pool", "considered",
                        "week", "replacement", "roster_span", "budget",
                        "pricing", "note"}
    assert isinstance(out["pricing"], str) and out["pricing"]

    bare = sqlite3.connect(":memory:")
    bare.row_factory = sqlite3.Row
    db.init_db(bare)
    demo.seed(bare)
    bare.execute("DELETE FROM transactions")
    bare.commit()
    empty = brain.waiver_targets(bare)
    assert empty["history_n"] == 0
    assert "no bid history" in empty["pricing"].lower(), \
        "with no book the payload must say so in its own words"


# ---------------------------------------------------------------------------
# The report card's reads
# ---------------------------------------------------------------------------

def test_every_seat_gets_its_own_read(world):
    """The live card printed "The deepest shelf in the room." on both of its
    top two rows, "A straight pour" on three more, and one "shopped the
    discounts" variant on three others. A ranking whose rows cannot be told
    apart is not a ranking anybody can read."""
    from app.engines import grades

    teams = [{"starters": 2200.0 - i * 15, "vbd": 470.0 - i * 9,
              "surplus": -30.0 + i * 14, "depth": max(0, 3 - i // 4),
              "risk": 40.0 + i, "owner": f"seat{i}"} for i in range(12)]
    grades.compose(teams)
    grades.seat_notes(teams)
    notes = [t["note"] for t in teams]
    assert len(set(notes)) == len(notes), (
        "seats sharing a read: "
        + repr(sorted(n for n in notes if notes.count(n) > 1)))
    for n in notes:
        assert n and n[0].isupper() and n.endswith("."), f"malformed read: {n!r}"


def test_a_read_never_invents_a_claim(world):
    """The de-duplicator may only fall back to signals the seat actually has
    and numbers already on it — never to a phrase it did not earn."""
    from app.engines import grades

    # Two seats identical in every metric: nothing distinguishes them but the
    # figures, so the second must fall through to the evidence tail.
    teams = [{"starters": 2000.0, "vbd": 400.0, "surplus": 0.0, "depth": 1,
              "risk": 40.0, "owner": "a"},
             {"starters": 2000.0, "vbd": 400.0, "surplus": 0.0, "depth": 1,
              "risk": 40.0, "owner": "b"}]
    grades.compose(teams)
    grades.seat_notes(teams)
    assert teams[0]["note"] != teams[1]["note"]
    assert "2000" in teams[1]["note"] or "400" in teams[1]["note"] \
        or "spare" in teams[1]["note"] or "risk" in teams[1]["note"], \
        f"the tail-breaker must name a real figure, got {teams[1]['note']!r}"
