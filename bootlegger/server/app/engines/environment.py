"""The game a player is standing in.

Every projection on this board has been matchup-blind. A receiver whose team is
implied for 30 points on Sunday and one whose team is implied for 16 have
carried the same number, and that is the single largest gap between this board
and what Draft Sharks or FantasyPros sell.

The correction is market-implied, deliberately. Three reasons:

1. **It is already here.** `nfl_games` carries spread and total from nflverse,
   and implied_total = (total + spread)/2 is the market's own expectation of
   THIS team's score. No model of mine to calibrate, no season of history to
   wait for.
2. **It is the sharpest public read there is.** A betting line is thousands of
   people with money at stake pricing injuries, weather, pace and personnel
   into one number, updated continuously. A points-allowed-by-position table
   built here would be a worse estimate of the same thing.
3. **It cannot quietly rot.** A line is either published or it is not. There is
   no state where this silently returns a stale number believing it is fresh —
   the way a hand-rolled defensive rating would after a trade deadline.

The cost is coverage: books post a week or two out, so most of the season has
no line yet. That is stated, not hidden — `adjust()` returns the reason it did
nothing, and the surfaces print it.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not touch season-long (week 0) projections. Draft-day math must not
move on a Week 1 betting line: the draft is about seventeen weeks, and the two
of them that have lines today say almost nothing about the other fifteen. Same
boundary the source calibration draws in engines/calibration.py, for the same
reason.
"""
from __future__ import annotations

from dataclasses import dataclass

# The league-average implied team total. Both teams' implied totals sum to the
# game total, so the mean implied total IS half the mean game total — around
# 44-46 in the modern NFL, giving 22-23. Recomputed from the week's own slate
# whenever there are enough lines to mean anything; this is only the fallback.
DEFAULT_MEAN_IMPLIED = 22.5
# Below this many lined teams, the slate's own mean is noise and the constant
# above is the better estimate. Eight teams is four games.
MIN_SLATE = 8

# How much of the environment delta reaches the player. NOT 1.0, and the
# reason is not caution for its own sake:
#
#   A team implied for 27 against a league mean of 22.5 is expected to score
#   20% more than average. Its quarterback does NOT project 20% more fantasy
#   points, because a chunk of that team total is defensive and special-teams
#   scoring, because touchdowns concentrate unevenly, and because the
#   projection being adjusted ALREADY contains an average-environment
#   expectation for this player — some of the game script is priced in twice.
#
# Half is the honest damping: it moves the number in the right direction by an
# amount that survives being wrong. Published work on implied totals versus
# realised fantasy points finds a real but attenuated relationship; 0.5 sits
# inside every estimate I could find rather than at the edge of one.
PASS_THROUGH = 0.5

# No adjustment may move a projection more than this. A 12% swing is already
# the difference between a flex start and a bench, and beyond it the estimate
# is doing more work than a single market number can support. It also bounds
# the damage from a bad line — a stale or mis-signed number cannot invert a
# lineup, it can only nudge one.
MAX_SWING = 0.12


@dataclass(frozen=True)
class Environment:
    """What the market says about one team's Sunday."""
    team: str
    implied: float | None
    mean: float
    multiplier: float
    reason: str

    @property
    def known(self) -> bool:
        return self.implied is not None

    def as_dict(self) -> dict:
        return {"team": self.team, "implied": self.implied,
                "mean": round(self.mean, 1),
                "multiplier": round(self.multiplier, 3),
                "pct": round((self.multiplier - 1.0) * 100, 1),
                "reason": self.reason, "known": self.known}


def slate_mean(implied_totals: list[float], min_slate: int = MIN_SLATE) -> tuple[float, str]:
    """The week's own average implied total, or the documented norm.

    Using the slate's own mean rather than a constant matters on a short week:
    six teams on a Thursday-to-Monday island are not a league, and a constant
    would read the whole slate as below average.
    """
    live = [t for t in implied_totals if t is not None]
    if len(live) < min_slate:
        return DEFAULT_MEAN_IMPLIED, (
            f"the league norm — only {len(live)} team{'' if len(live) == 1 else 's'} "
            f"on this week's board carry a line")
    return sum(live) / len(live), f"measured across {len(live)} lined teams this week"


def multiplier(implied: float | None, mean: float,
               pass_through: float = PASS_THROUGH,
               max_swing: float = MAX_SWING) -> float:
    """How much to scale a projection for the game its owner is playing in.

    1.0 when there is no line — an unknown environment is an AVERAGE
    environment, never a penalty. A player whose game has not been priced yet
    must not drift down the board for it.
    """
    if implied is None or mean <= 0:
        return 1.0
    raw = 1.0 + pass_through * ((implied - mean) / mean)
    return max(1.0 - max_swing, min(1.0 + max_swing, raw))


def for_team(team: str | None, implied: float | None,
             mean: float, mean_note: str) -> Environment:
    if not team:
        return Environment("", None, mean, 1.0, "no club on file")
    if implied is None:
        return Environment(team, None, mean, 1.0,
                           "no line posted for this game yet — treated as an average spot")
    m = multiplier(implied, mean)
    pct = (m - 1.0) * 100
    if abs(pct) < 1.0:
        line = f"{team} implied {implied:.1f}, right at this week's average"
    else:
        direction = "a better spot than average" if pct > 0 else "a worse spot than average"
        line = (f"{team} implied {implied:.1f} against {mean:.1f} — {direction}, "
                f"worth {pct:+.0f}% on the projection ({mean_note})")
    return Environment(team, implied, mean, m, line)


def apply(proj: float, env: Environment) -> float:
    """The adjusted projection. Never negative, never invented from nothing."""
    if proj <= 0:
        return proj
    return round(proj * env.multiplier, 2)


# ---------------------------------------------------------------------------
# Strength of schedule
# ---------------------------------------------------------------------------
# SOS here is the market's, which means it exists only as far ahead as books
# have posted. That is a real limit and it is reported rather than papered
# over: a schedule read that silently covers two of seventeen weeks while
# looking like a season is worse than no schedule read at all.

@dataclass(frozen=True)
class ScheduleRead:
    team: str
    weeks: int          # how many of this team's games carry a line
    mean_implied: float | None
    vs_league: float | None    # points above/below the league's mean implied
    covered: str

    def as_dict(self) -> dict:
        return {"team": self.team, "weeks": self.weeks,
                "mean_implied": round(self.mean_implied, 1) if self.mean_implied else None,
                "vs_league": round(self.vs_league, 1) if self.vs_league is not None else None,
                "covered": self.covered}


def schedule_read(team: str, implied_by_week: dict[int, float | None],
                  league_mean: float) -> ScheduleRead:
    """How good this team's PRICED games look, and how many that is.

    `weeks` is the headline as much as the average is. "+2.1 over three games"
    and "+2.1 over fourteen" are different claims, and a reader who cannot tell
    them apart has been misled by a number that was technically correct.
    """
    lined = {w: v for w, v in implied_by_week.items() if v is not None}
    if not lined:
        return ScheduleRead(team, 0, None, None, "no games priced yet")
    mean = sum(lined.values()) / len(lined)
    weeks = sorted(lined)
    span = (f"week {weeks[0]}" if len(weeks) == 1
            else f"weeks {weeks[0]}–{weeks[-1]}, {len(weeks)} priced")
    return ScheduleRead(team, len(lined), mean, mean - league_mean, span)
