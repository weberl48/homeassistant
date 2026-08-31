"""The forecast ledger — remembering what was said, so it can be graded.

The 2026 draft ran on six equal-weighted sources. Two graders then called the
resulting backfield the league's worst while a third called those same picks
highlights, and the disagreement could not be settled because `projections` is
rewritten in place: within two nightlies the numbers the draft was made from
were gone. These tests pin the memory and the grading, and — as much as
anything here — pin the honesty of the grade.
"""
from __future__ import annotations

from app import db
from app.engines import ledger


def _proj(conn, pid, source, pts, week=0):
    conn.execute(
        "INSERT OR REPLACE INTO projections(player_id,week,source,pts) VALUES(?,?,?,?)",
        (pid, week, source, pts))
    conn.commit()


def _actual(conn, pid, week, pts, season=2026):
    conn.execute(
        "INSERT OR REPLACE INTO player_week_actuals(player_id,season,week,pts) "
        "VALUES(?,?,?,?)", (pid, season, week, pts))
    conn.commit()


# ---------------------------------------------------------------------------
# Memory


def test_a_snapshot_survives_the_nightly_overwrite(conn):
    """The whole point. `projections` is DELETEd and rewritten every night; a
    frozen copy must not be."""
    _proj(conn, "p1", "cbs", 300.0)
    ledger.snapshot(conn, "preseason-2026", 2026)
    conn.execute("DELETE FROM projections WHERE week=0")   # what the nightly does
    conn.commit()
    rows = conn.execute(
        "SELECT pts FROM projection_ledger WHERE tag='preseason-2026' "
        "AND player_id='p1' AND source='cbs'").fetchall()
    assert [r["pts"] for r in rows] == [300.0]


def test_re_running_a_tag_replaces_rather_than_duplicates(conn):
    """A cron that fires twice must be harmless."""
    _proj(conn, "p1", "cbs", 300.0)
    ledger.snapshot(conn, "preseason-2026", 2026)
    _proj(conn, "p1", "cbs", 250.0)
    ledger.snapshot(conn, "preseason-2026", 2026)
    rows = conn.execute(
        "SELECT pts FROM projection_ledger WHERE tag='preseason-2026' "
        "AND player_id='p1'").fetchall()
    assert [r["pts"] for r in rows] == [250.0]


def test_snapshots_are_listed_with_provenance(conn):
    _proj(conn, "p1", "cbs", 300.0)
    ledger.snapshot(conn, "preseason-2026", 2026)
    got = ledger.snapshots(conn)
    assert got and got[0]["tag"] == "preseason-2026"
    assert got[0]["taken_at"], "a snapshot with no timestamp cannot be reasoned about"


# ---------------------------------------------------------------------------
# Grading


def _two_sources(conn):
    """`sharp` nails a 170-point pace; `blunt` is 100 points high on both men."""
    for pid, real_per_week in (("a", 10.0), ("b", 8.0)):
        _proj(conn, pid, "sharp", real_per_week * ledger.SEASON_WEEKS)
        _proj(conn, pid, "blunt", real_per_week * ledger.SEASON_WEEKS + 100)
        for wk in (1, 2, 3, 4):
            _actual(conn, pid, wk, real_per_week)
    ledger.snapshot(conn, "preseason-2026", 2026)


def test_the_better_source_scores_lower_error(conn):
    _two_sources(conn)
    scores = {s.source: s.mae for s in
              ledger.grade(conn, "preseason-2026", 2026, weeks={1, 2, 3, 4})}
    assert scores["sharp"] < scores["blunt"]
    # 100 season points over 4 of 18 weeks is ~22.2 points of prorated error.
    assert 20 < scores["blunt"] - scores["sharp"] < 25


def test_a_season_projection_is_prorated_not_compared_whole(conn):
    """The trap this would fall into: charging a 170-point season projection
    against four weeks of actuals and declaring every source catastrophic."""
    _two_sources(conn)
    sharp = next(s for s in ledger.grade(conn, "preseason-2026", 2026,
                                         weeks={1, 2, 3, 4}) if s.source == "sharp")
    assert sharp.mae < 1.0, (
        f"a source that called the pace exactly scored {sharp.mae:.1f} error — "
        f"the projection is being compared unprorated")


def test_grading_is_silent_below_three_weeks(conn):
    """One Sunday is not evidence. A number nobody should act on should not
    be produced at all, rather than produced and hedged."""
    _two_sources(conn)
    assert ledger.grade(conn, "preseason-2026", 2026, weeks={1}) == []
    assert ledger.grade(conn, "preseason-2026", 2026, weeks={1, 2}) == []
    assert ledger.grade(conn, "preseason-2026", 2026, weeks={1, 2, 3})


def test_unfinished_weeks_are_the_callers_problem(conn):
    """Sleeper reports points continuously — 0.0 before kickoff. Grading a
    week that has not been played charges every source for a miss that has not
    happened. The caller passes FINISHED weeks; passing none grades nothing."""
    _two_sources(conn)
    assert ledger.grade(conn, "preseason-2026", 2026, weeks=set()) == []
    assert ledger.grade(conn, "preseason-2026", 2026, weeks=None) == []


def test_irrelevant_players_do_not_pad_the_score(conn):
    """A source cannot win by correctly predicting that the 400th receiver
    scores nothing — the same filter calibration applies."""
    _two_sources(conn)
    _proj(conn, "nobody", "sharp", 1.0)
    _actual(conn, "nobody", 1, 0.0)
    ledger.snapshot(conn, "preseason-2026", 2026)
    sharp = next(s for s in ledger.grade(conn, "preseason-2026", 2026,
                                         weeks={1, 2, 3, 4}) if s.source == "sharp")
    assert sharp.n == 2, f"scored {sharp.n} men; the sub-replacement body counted"


def test_the_read_out_refuses_to_rank_one_source(conn):
    """A ranking of one is not a ranking."""
    from app.engines.calibration import SourceScore
    assert ledger.read_out([]) == []
    assert ledger.read_out([SourceScore("cbs", 50, 12.0)]) == []
    said = ledger.read_out([SourceScore("cbs", 50, 12.0),
                            SourceScore("espn", 50, 4.0)])
    assert said and "espn" in said[0], "the best source must be named first"


def test_the_blend_itself_is_frozen_not_just_its_inputs(conn):
    """The board drafts on consensus.pts_robust, not on any single source, and
    that table is rewritten nightly too. Freezing only the inputs would leave
    the module's stated motive — settling which read of a backfield was right —
    still unanswerable."""
    _proj(conn, "p1", "cbs", 300.0)
    conn.execute("INSERT OR REPLACE INTO consensus(player_id,week,pts_robust) "
                 "VALUES(?,?,?)", ("p1", 0, 275.0))
    conn.commit()
    ledger.snapshot(conn, "preseason-2026", 2026)
    got = dict(conn.execute(
        "SELECT source, pts FROM projection_ledger WHERE tag='preseason-2026' "
        "AND player_id='p1'").fetchall())
    assert got.get("consensus") == 275.0, "the number actually drafted on was not kept"
    assert got.get("cbs") == 300.0, "and its inputs are still kept beside it"
