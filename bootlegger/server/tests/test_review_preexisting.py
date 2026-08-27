"""The five pre-existing bugs the max review surfaced.

None of these came from the recent work — they had been shipped for a while,
and three of them are the kind that never announce themselves: a schedule
toggle that destroys evidence, news that stops reaching a phone, and a spread
that is quietly the wrong width.
"""
import datetime
import json
import sqlite3

import pytest

from app import alerts, brain, db, demo, push, schedule
from app.config import settings
from app.engines import calibration as cal
from app.engines import consensus as cx


@pytest.fixture()
def world():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    demo.seed(conn)
    return conn


def _iso(hours_ago: float) -> str:
    return (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(hours=hours_ago)).isoformat(timespec="seconds")


def _item(conn, guid, pid, severity="out", hours_ago=1.0,
          headline="Ruled out for Sunday", source="espn"):
    conn.execute(
        "INSERT INTO news(guid,seq,source,player_id,name_raw,headline,body,link,"
        "severity,ailment,departure,published_at,fetched_at) "
        "VALUES(?,NULL,?,?,'x',?,'','',?,NULL,0,?,?)",
        (guid, source, pid, headline, severity, _iso(hours_ago), db.utcnow()))
    conn.commit()


# --- the push window is hours, not a calendar day --------------------------

def test_the_age_window_is_actually_twelve_hours(world):
    """published_at is ISO-8601 ('...T01:00:00+00:00'); SQLite's datetime()
    returns a space-separated string. Comparing them as strings diverges at
    byte 10 ('T' vs ' ') and the guard stopped being an hours window at all —
    13h, 18h and 24h all passed a 12-hour cutoff."""
    pid = json.loads(brain.my_roster_row(world)["players_json"])[0]
    world.execute("DELETE FROM news")
    for h in (1, 11, 13, 24, 30):
        _item(world, f"h{h}", pid, hours_ago=h)
    got = {r["guid"] for r in alerts.pending(world)}
    assert got == {"h1", "h11"}, f"12h window admitted {sorted(got)}"


# --- seen_at must not latch ------------------------------------------------

def test_a_regraded_item_becomes_pending_again(world):
    """ingest re-grades an item in place while pushed_at IS NULL — that is the
    point, the classifier keeps improving. But scan() stamped seen_at on every
    item it merely considered, so a rival's man passed over as Questionable
    was corrected to 'on injured reserve' in the database and could never be
    considered again. The news that opens a job never reached the phone."""
    league = {p for r in world.execute("SELECT players_json FROM rosters")
              for p in json.loads(r["players_json"])}
    mine = set(json.loads(brain.my_roster_row(world)["players_json"]))
    rival = next(iter(league - mine))
    world.execute("DELETE FROM news")
    _item(world, "g1", rival, severity="questionable",
          headline="Questionable for Sunday")

    assert len(alerts.pending(world)) == 1
    alerts.scan(world)
    assert alerts.pending(world) == [], "an unchanged grade must stay quiet"

    world.execute("UPDATE news SET severity='out', departure=1, "
                  "headline='Placed on injured reserve' WHERE guid='g1'")
    world.commit()
    again = alerts.pending(world)
    assert len(again) == 1, "a re-graded item is news and must be re-considered"


def test_a_pushed_item_does_not_push_twice(world, monkeypatch):
    """The latch fix must not reopen the thing seen_at was protecting."""
    starter = json.loads(brain.my_roster_row(world)["starters_json"])[0]
    world.execute("DELETE FROM news")
    _item(world, "g1", starter)
    sent: list = []
    monkeypatch.setattr(push, "send", lambda *a, **k: sent.append(a))
    alerts.scan(world)
    alerts.scan(world)
    assert len(sent) == 1, "the same grade must never push twice"


# --- a Scrimmage toggle must not destroy history ---------------------------

def test_the_practice_sweep_spares_historical_drafts(world):
    """etl_draft_history writes this league's past drafts into the same tables
    the live draft uses and deliberately skips the current league — so every
    historical id matched the sweep's old `NOT IN (target, league)`. A single
    Scrimmage toggle silently destroyed the only evidence engines/room.py has
    for how this room drafts, and Sleeper keeps no historical ADP to rebuild
    it from.

    The sweep exists to kill a ghost from the clear/poll race, and the ghost is
    always the room being left. This asserts the narrower rule directly.
    """
    league_draft, practice, historical = "L1", "P1", "H1"
    for did in (league_draft, practice, historical):
        world.execute(
            "INSERT OR REPLACE INTO drafts(draft_id,status,settings_json,updated_at) "
            "VALUES(?,'complete','{}',?)", (did, db.utcnow()))
        world.execute(
            "INSERT INTO draft_picks(draft_id,pick_no,round,roster_id,player_id) "
            "VALUES(?,1,1,1,'p')", (did,))
    world.commit()

    # The sweep as the poller performs it: leaving `practice` for the league.
    target, last_target = league_draft, practice
    if last_target not in (target, league_draft):
        world.execute("DELETE FROM draft_picks WHERE draft_id=?", (last_target,))
        world.execute("DELETE FROM drafts WHERE draft_id=?", (last_target,))
    world.commit()

    left = {r["draft_id"] for r in world.execute("SELECT draft_id FROM drafts")}
    assert historical in left, "the sweep destroyed this room's draft history"
    assert league_draft in left
    assert practice not in left, "the ghost must still be swept"


# --- an unplayed week is not evidence --------------------------------------

def _game(conn, week, team, kickoff):
    conn.execute(
        "INSERT OR REPLACE INTO nfl_games(season,week,team,opponent,is_home,kickoff_utc) "
        "VALUES(?,?,?,'OPP',1,?)", (settings.season, week, team, kickoff))


def test_completed_weeks_waits_for_the_last_kickoff(world):
    """The LAST kickoff, not the first: a week with a Monday nighter is not
    over on Sunday evening, and half its rosters would be scored on whatever
    they happened to have at that moment."""
    world.execute("DELETE FROM nfl_games")
    _game(world, 1, "AAA", _iso(60))          # sunday, long done
    _game(world, 1, "BBB", _iso(30))          # monday night, done
    _game(world, 2, "CCC", _iso(30))          # sunday, done
    _game(world, 2, "DDD", _iso(1))           # monday night, still running
    world.commit()
    done = schedule.completed_weeks(world, settings.season)
    assert 1 in done
    assert 2 not in done, "a week is not over until its last game is"


def test_sigma_ignores_the_week_in_progress(world):
    """Sleeper reports points continuously — 0.0 before kickoff, partial
    totals during — and the nightly persists them. Twelve in-progress rows of
    (0 - 110) mixed into real N(0,27) residuals measured sigma 39.4 instead of
    27, and sigma is what decides floor lineup against ceiling lineup."""
    world.execute("DELETE FROM nfl_games")
    world.execute("DELETE FROM matchups")
    for wk in range(1, 14):
        _game(world, wk, "AAA", _iso(200 - wk))       # weeks 1-13 all finished
    _game(world, 14, "AAA", _iso(1))                  # week 14 in progress
    for wk in range(1, 14):
        for rid in range(1, 13):
            world.execute(
                "INSERT OR REPLACE INTO matchups(week,roster_id,opp_roster_id,"
                "matchup_id,proj_for,points_for) VALUES(?,?,?,?,?,?)",
                (wk, rid, 2, 1, 110.0, 110.0 + (rid - 6) * 4.0))
    for rid in range(1, 13):                          # the unplayed week: all zeros
        world.execute(
            "INSERT OR REPLACE INTO matchups(week,roster_id,opp_roster_id,"
            "matchup_id,proj_for,points_for) VALUES(?,?,?,?,?,?)",
            (14, rid, 2, 1, 110.0, 0.0))
    world.commit()

    sigma, note = brain._matchup_sigma(world)
    assert sigma < 20.0, f"the unplayed week leaked into the spread: {sigma} ({note})"


def test_calibration_only_scores_finished_weeks(world):
    """A source charged for 'missing' a game nobody has played is being scored
    on nothing."""
    assert cal.score_sources(world, settings.season, weeks=set()) == []


# --- calibration must not lose the outlier trim ----------------------------

def test_trim_extremes_protects_the_weighted_mean():
    """weighted_mean has no trimming and took over at n>=4, exactly where the
    trimmed mean was the only protection. ESPN's keyless endpoint returning
    SEASON totals for a week is the real case."""
    vals = {"espn": 419.0, "cbs": 14.2, "fp": 13.1,
            "fft": 15.0, "sleeper": 14.6, "ds": 14.4}
    w = {k: 1 / 6 for k in vals}
    raw = cal.weighted_mean(vals, w)
    trimmed = cal.weighted_mean(cx.trim_extremes(vals), w)
    assert raw > 60, "fixture must actually be poisoned"
    assert trimmed == pytest.approx(cx.robust_mean(list(vals.values())), abs=0.5)
    assert "espn" not in cx.trim_extremes(vals)


def test_trim_extremes_leaves_thin_coverage_alone():
    """With three sources, dropping two leaves a single unchecked vote — worse
    than the disagreement it removes."""
    three = {"a": 1.0, "b": 2.0, "c": 99.0}
    assert cx.trim_extremes(three) == three
