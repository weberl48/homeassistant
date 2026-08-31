"""The forecast ledger: remembering what each source said, so it can be graded.

`projections` is a picture of what is believed right now. Every nightly deletes
a source's rows for a week and writes them again, which means this project has
never been able to answer the one question that would improve a draft: **which
of these six sources was actually right last year?**

It cost something real to learn that. The 2026 draft ran on a six-source
equal-weighted consensus; two independent graders then called the resulting
backfield the worst in the league while a third called those same picks
highlights. That disagreement is settleable — but only against what the players
actually go on to score, and by the time anyone thought to look, two nightlies
had overwritten the numbers the draft was made from.

So: freeze the forecast when it is made, grade it when the season has run
enough to say something, and let the next draft inherit the answer.

Three things this deliberately is not:

1. **It does not change this season's draft.** That draft is over, and a
   season-long projection cannot be scored the week it is written. The payoff
   is next August. calibration.py's docstring argued that week-0 projections
   have "nothing realized to be scored against until the season is over, which
   is exactly when it stops mattering" — right about the timing, wrong about
   the mattering. It stops mattering for THAT draft. It is exactly what the
   next one needs, and evidence that takes a season to accumulate has to start
   on the day the forecast is made.

2. **It does not score absolute accuracy.** A prorated season projection is
   charged for injuries, benchings and holdouts nobody forecast. That noise is
   real, but every source faces the same players in the same weeks, so it is
   common — and for RANKING sources against each other, common noise cancels.
   Read the output as "who was less wrong than whom", never as "how wrong".

3. **It does not weight anything yet.** Grading is read-only. Feeding these
   numbers into the week-0 consensus is a separate decision, to be taken on a
   full season of evidence rather than on the first four weeks.
"""
from __future__ import annotations

import sqlite3

from .calibration import RELEVANT_PTS, SourceScore

# A season-long projection is a TOTAL for the regular season, and the regular
# season runs eighteen weeks — seventeen games plus each man's bye. Prorating
# by weeks elapsed over 17 (games, not weeks) inflated every expectation by
# 18/17, and because that inflation is proportional to the projection it fell
# hardest on the most optimistic source. That is a systematic thumb on the
# scale, not the common noise this module claims cancels.
SEASON_WEEKS = 18
# Weeks that must be finished before a prorated grade means anything. Three is
# the point where a single blow-up Sunday stops dominating; it is the same
# instinct as calibration.MIN_SAMPLE, expressed in weeks because a season-long
# projection produces one observation per player, not one per player-week.
MIN_WEEKS = 3


def snapshot(conn: sqlite3.Connection, tag: str, season: int,
             week: int = 0) -> int:
    """Freeze every source's current projections under `tag`.

    Idempotent: re-running with the same tag replaces that snapshot rather than
    accumulating duplicates, so a cron that fires twice is harmless.
    """
    from .. import db
    now = db.utcnow()
    conn.execute("DELETE FROM projection_ledger WHERE tag=? AND week=?", (tag, week))
    n = conn.execute(
        "INSERT INTO projection_ledger(tag,season,player_id,source,week,pts,taken_at) "
        "SELECT ?,?,player_id,source,week,pts,? FROM projections WHERE week=?",
        (tag, season, now, week)).rowcount
    # And the blend itself, under the reserved source name `consensus`.
    #
    # Freezing only the inputs would have missed the point: the board does not
    # draft on any source's number, it drafts on consensus.pts_robust, and that
    # table is DELETE-and-rewritten every nightly exactly like `projections`.
    # Storing it here costs one more INSERT and buys the question actually
    # worth asking in October — not just which source read the season best, but
    # whether averaging the six of them beat the best of them.
    n += conn.execute(
        "INSERT INTO projection_ledger(tag,season,player_id,source,week,pts,taken_at) "
        "SELECT ?,?,player_id,'consensus',week,pts_robust,? FROM consensus "
        "WHERE week=? AND pts_robust IS NOT NULL",
        (tag, season, now, week)).rowcount
    conn.commit()
    return n


def snapshots(conn: sqlite3.Connection) -> list[dict]:
    """What has been frozen, and when."""
    return [dict(r) for r in conn.execute(
        "SELECT tag, week, season, COUNT(*) rows, COUNT(DISTINCT source) sources, "
        "MIN(taken_at) taken_at FROM projection_ledger "
        "GROUP BY tag, week, season ORDER BY taken_at")]


def grade(conn: sqlite3.Connection, tag: str, season: int,
          weeks: set[int] | None = None,
          relevant_pts: float = RELEVANT_PTS) -> list[SourceScore]:
    """Mean absolute error per source for a season-long snapshot, prorated.

    A season TOTAL of P points, judged after W finished weeks, predicts
    P * W / 18 — eighteen because that is how many weeks the regular season
    takes, bye included. It is charged the gap between that and what the man
    actually scored across those weeks.

    Bye timing still adds noise: two men with equal projections differ by
    whether their week off has happened yet. That noise IS common across
    sources — every source forecasts the same man with the same bye — so it
    cancels for ranking sources against each other, which is the only thing
    this function claims to do.

    `weeks` must be the FINISHED weeks — the same discipline calibration
    applies, and for the same reason: Sleeper reports points continuously, so
    scoring an unplayed game charges every source for a miss that has not
    happened yet. Returns [] below MIN_WEEKS rather than a number nobody should
    act on.
    """
    if weeks is None or len(weeks) < MIN_WEEKS:
        return []
    marks = ",".join("?" * len(weeks))
    rows = conn.execute(
        f"""
        SELECT l.source,
               COUNT(*) AS n,
               AVG(ABS(l.pts * ? / ? - a.total)) AS mae
        FROM projection_ledger l
        JOIN (SELECT player_id, SUM(pts) AS total
              FROM player_week_actuals
              WHERE season = ? AND week IN ({marks})
              GROUP BY player_id) a ON a.player_id = l.player_id
        WHERE l.tag = ? AND l.week = 0 AND l.season = ?
          AND (l.pts >= ? OR a.total >= ?)
        GROUP BY l.source
        """,
        (len(weeks), SEASON_WEEKS, season, *sorted(weeks), tag, season,
         relevant_pts, relevant_pts)).fetchall()
    return [SourceScore(r["source"], r["n"], r["mae"])
            for r in rows if r["mae"] is not None]


def read_out(scores: list[SourceScore]) -> list[str]:
    """The grade in words, best first. Silent on an empty or single result —
    a ranking of one source is not a ranking."""
    if len(scores) < 2:
        return []
    ranked = sorted(scores, key=lambda s: s.mae)
    best, worst = ranked[0], ranked[-1]
    out = [f"{best.source} is reading this season best "
           f"({best.mae:.1f} pts of error over {best.n} men)."]
    if worst.mae > best.mae:
        out.append(f"{worst.source} is furthest off ({worst.mae:.1f}), "
                   f"a gap of {worst.mae - best.mae:.1f} points a man.")
    return out
