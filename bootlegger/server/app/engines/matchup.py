"""The opponent — and why the optimal lineup depends on him.

Every lineup tool in the field maximises expected points. That is the right
objective exactly once a week: when the matchup is close. It is wrong at both
ends, and wrong in opposite directions.

- **Heavy favourite.** You are 80% to win on the projections. The way you lose
  is a starter laying an egg, not a starter failing to explode. The correct
  play is the highest FLOOR lineup, even at a cost of expected points — you are
  buying insurance with points you do not need.
- **Heavy underdog.** You are 20% to win. A lineup that reliably scores its
  projection reliably loses. The correct play is the highest CEILING lineup:
  you need the tail, and expected points spent on safety are wasted.

So the engine produces three lineups, not one, and says which the week calls
for. Nothing here is exotic — it is the standard "leverage" argument from DFS
tournament play, applied to the one head-to-head matchup that actually exists.

The margin model is a normal on (my score − his score). Both team totals are
sums of nine-ish player projections, so the CLT is doing honest work; what it
cannot supply is the spread, and a guessed spread is the whole ballgame. So the
spread is measured from this league's own weeks the moment there are enough of
them, and only falls back to a documented full-PPR norm before that.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# Residual standard deviation of ONE team's weekly score against its own
# projection, in full-PPR points. The published range for 12-team full PPR
# clusters near the high twenties; 27 sits in it and is deliberately on the
# wide side, because an overconfident win probability is the more expensive
# error (it talks you into a floor lineup you did not need). Replaced by the
# league's own residuals as soon as MIN_HISTORY weeks exist.
DEFAULT_TEAM_SIGMA = 27.0
MIN_HISTORY = 12          # roster-weeks before the league's own spread is trusted
SIGMA_FLOOR = 12.0        # a suspiciously tight measurement is a bad measurement
SIGMA_CEILING = 45.0

# Where the strategy flips. Inside these the expected-points lineup is right;
# outside them the tails matter more than the mean.
FAVOURITE_AT = 0.68
UNDERDOG_AT = 0.32


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def margin_sigma(team_sigma: float) -> float:
    """Spread of (my score − his score) for two independent teams."""
    return team_sigma * math.sqrt(2.0)


def win_probability(my_proj: float, opp_proj: float,
                    team_sigma: float = DEFAULT_TEAM_SIGMA) -> float:
    """P(I outscore him), from the projected margin."""
    sigma = margin_sigma(team_sigma)
    if sigma <= 0:
        return 1.0 if my_proj > opp_proj else 0.0
    return _phi((my_proj - opp_proj) / sigma)


def sigma_from_history(residuals: list[float]) -> tuple[float, str]:
    """This league's own scoring spread, or the documented default.

    `residuals` are (actual − projected) per roster-week. Returns the sigma and
    a provenance string, because a win probability the owner cannot trace is a
    number he should not act on.
    """
    n = len(residuals)
    if n < MIN_HISTORY:
        return DEFAULT_TEAM_SIGMA, f"the full-PPR league norm — only {n} of this league's weeks are on the books"
    mean = sum(residuals) / n
    var = sum((r - mean) ** 2 for r in residuals) / (n - 1)
    sigma = math.sqrt(var)
    if not (SIGMA_FLOOR <= sigma <= SIGMA_CEILING):
        return DEFAULT_TEAM_SIGMA, (
            f"the league norm — {n} weeks measured {sigma:.0f}, outside the plausible range")
    return sigma, f"measured on {n} of this league's roster-weeks"


@dataclass(frozen=True)
class Strategy:
    key: str          # floor | balanced | ceiling
    label: str
    line: str

    def as_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "line": self.line}


def strategy(win_prob: float) -> Strategy:
    """Which lineup this week actually wants."""
    if win_prob >= FAVOURITE_AT:
        return Strategy(
            "floor", "Play the floor",
            f"You're {win_prob * 100:.0f}% to win on the projections. The way "
            "you lose from here is a starter laying an egg — take the safer "
            "man even when he projects a shade lower.")
    if win_prob <= UNDERDOG_AT:
        return Strategy(
            "ceiling", "Chase the ceiling",
            f"You're {win_prob * 100:.0f}% to win on the projections. A lineup "
            "that scores exactly its projection loses this one — start the "
            "boom, not the bankable.")
    return Strategy(
        "balanced", "Play it straight",
        f"{win_prob * 100:.0f}% to win — close enough that every projected "
        "point is worth the same. Take the best expected score.")


def swing(win_prob_floor: float, win_prob_expected: float,
          win_prob_ceiling: float, key: str) -> float:
    """How much win probability the recommended lineup actually buys over the
    expected-points one. A strategy that moves the odds by half a point is a
    lecture, not advice — the caller uses this to decide whether to speak."""
    alt = {"floor": win_prob_floor, "ceiling": win_prob_ceiling}.get(key)
    if alt is None:
        return 0.0
    return alt - win_prob_expected
