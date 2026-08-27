"""The market-implied game environment.

The properties that matter here are the conservative ones: an unpriced game
must not penalise anybody, a bad line must not be able to invert a lineup, and
draft-day math must not move on a Week 1 betting line.
"""
import sqlite3

import pytest

from app import brain, db, demo
from app.config import settings
from app.engines import environment as env


@pytest.fixture()
def world():
    """A seeded demo league in memory — same shape as test_surfaces."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    demo.seed(conn)
    return conn


# --- the multiplier ---------------------------------------------------------

def test_no_line_is_an_average_spot_not_a_penalty():
    """The failure this guards is quiet and expensive: a player whose game the
    books have not priced yet drifting down the lineup for it."""
    assert env.multiplier(None, 22.5) == 1.0
    e = env.for_team("BUF", None, 22.5, "the league norm")
    assert e.multiplier == 1.0
    assert not e.known
    assert "no line" in e.reason


def test_direction_and_damping():
    """A better spot lifts, a worse spot cuts, and neither moves the full
    distance — half the environment delta reaches the player."""
    mean = 22.5
    hot = env.multiplier(27.0, mean)
    cold = env.multiplier(18.0, mean)
    assert hot > 1.0 > cold
    # 27 is 20% above the mean; damped by half that is ~10%.
    assert hot == pytest.approx(1.10, abs=0.005)
    assert cold == pytest.approx(1.10 - 2 * 0.10, abs=0.02)


def test_swing_is_capped_both_ways():
    """A stale or mis-signed line must be able to nudge a lineup, never invert
    one. Even an absurd total stays inside the band."""
    assert env.multiplier(80.0, 22.5) == pytest.approx(1 + env.MAX_SWING)
    assert env.multiplier(0.5, 22.5) == pytest.approx(1 - env.MAX_SWING)


def test_apply_never_invents_points():
    e = env.for_team("KC", 30.0, 22.5, "measured")
    assert env.apply(0.0, e) == 0.0          # a zeroed man stays zeroed
    assert env.apply(-3.0, e) == -3.0        # a negative projection is left alone
    assert env.apply(10.0, e) > 10.0


# --- the slate mean ---------------------------------------------------------

def test_short_slate_falls_back_to_the_norm():
    """Six teams on a Thursday-to-Monday island are not a league. Averaging
    them would read the whole slate as below average."""
    mean, note = env.slate_mean([21.0, 22.0, 23.0])
    assert mean == env.DEFAULT_MEAN_IMPLIED
    assert "league norm" in note


def test_full_slate_uses_its_own_mean():
    totals = [20.0] * 6 + [25.0] * 6
    mean, note = env.slate_mean(totals)
    assert mean == pytest.approx(22.5)
    assert "12 lined teams" in note


def test_slate_mean_ignores_unpriced_games():
    mean, note = env.slate_mean([20.0] * 8 + [None] * 20)
    assert mean == pytest.approx(20.0)
    assert "8 lined teams" in note


# --- strength of schedule ---------------------------------------------------

def test_schedule_read_reports_its_own_coverage():
    """"+2.1 over three games" and "+2.1 over fourteen" are different claims.
    A reader who cannot tell them apart has been misled by a number that was
    technically correct."""
    r = env.schedule_read("BUF", {1: 26.0, 2: 24.0, 3: None}, 22.5)
    assert r.weeks == 2
    assert r.vs_league == pytest.approx(2.5)
    assert "2 priced" in r.covered


def test_schedule_read_refuses_an_unpriced_team():
    r = env.schedule_read("NYJ", {1: None, 2: None}, 22.5)
    assert r.weeks == 0 and r.mean_implied is None
    assert "no games priced" in r.covered


# --- the wiring -------------------------------------------------------------

def _line(conn, team, week, implied):
    conn.execute(
        "INSERT INTO nfl_games(season,week,team,opponent,is_home,implied_total) "
        "VALUES(?,?,?,?,1,?) ON CONFLICT(season,week,team) DO UPDATE SET "
        "implied_total=excluded.implied_total",
        (settings.season, week, team, "OPP", implied))


def test_week_card_moves_with_the_market(world):
    """The whole point: the lineup is chosen on environment-adjusted numbers,
    and the card shows both the adjusted figure and the one it started from."""
    base = brain.get_week_card(world, week=1)
    assert base["ready"]
    teams = sorted({r["team"] for r in base["actual"] if r["team"]})
    assert teams, "the demo roster must carry clubs"

    # Price a full slate: the first roster team in a great spot, the rest flat.
    for i, t in enumerate(teams):
        _line(world, t, 1, 32.0 if i == 0 else 22.0)
    for j in range(10):                     # pad the slate past MIN_SLATE
        _line(world, f"Z{j}", 1, 22.0)
    world.commit()

    after = brain.get_week_card(world, week=1)
    hot = next(r for r in after["actual"] if r["team"] == teams[0])
    assert hot["env"]["known"] is True
    assert hot["env"]["multiplier"] > 1.0
    # The row reconciles: the adjusted number is what the lineup counted.
    if hot["proj"] > 0:
        assert hot["proj"] > hot["proj_base"], "a hot spot must lift the projection"
    assert "implied 32.0" in hot["env"]["reason"]


def test_week_card_survives_a_slate_with_no_lines(world):
    """Preseason and most of the year: no game is priced. Every multiplier is
    1.0, every reason says so, and nothing about the card degrades."""
    world.execute("UPDATE nfl_games SET implied_total=NULL, spread=NULL, total_line=NULL")
    world.commit()
    card = brain.get_week_card(world, week=1)
    assert card["ready"]
    for row in card["actual"]:
        assert row["env"]["multiplier"] == 1.0
        assert row["env"]["known"] is False
        assert row["proj"] == pytest.approx(row["proj_base"]) or row["proj"] == 0.0


def test_draft_board_does_not_move_on_a_week_one_line(world):
    """Draft-day math is about seventeen weeks. The two of them that carry
    lines today say almost nothing about the other fifteen, and a board that
    reshuffles because Buffalo is favoured in September is worse than one that
    ignores it. Same boundary engines/calibration.py draws at week 0."""
    before = {p["id"]: p["vbd"] for p in brain.get_board(world)["players"]}
    for j in range(12):
        _line(world, f"Z{j}", 1, 34.0)
    for t in ("BUF", "KC", "DET", "PHI"):
        _line(world, t, 1, 34.0)
    world.commit()
    after = {p["id"]: p["vbd"] for p in brain.get_board(world)["players"]}
    assert before == after, "a week-1 betting line must not move the draft board"
