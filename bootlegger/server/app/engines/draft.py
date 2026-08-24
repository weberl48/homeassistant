"""Draft engine: survival odds and pick-suggestion scoring (design doc §4).

score = need_mult × (VBD_now − E[best VBD available at my next pick]),
with the expectation taken over the candidate's own position pool — "if I pass
here, what does this slot look like when the snake comes back around?"
Survival: P = 1 − Φ((my_next_pick − ADP)/σ), σ from source ranges with a
0.15×ADP fallback and a 2-pick floor.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..config import (ADP_SIGMA_FALLBACK, ADP_SIGMA_FLOOR, FLEX_ELIGIBLE,
                      SUPER_FLEX_ELIGIBLE)


def phi(z: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def adp_sigma(adp: float, stdev: float | None) -> float:
    sigma = stdev if stdev and stdev > 0 else ADP_SIGMA_FALLBACK * adp
    return max(sigma, ADP_SIGMA_FLOOR)


def survival_prob(adp: float, stdev: float | None, my_next_pick: int) -> float:
    """P(player is still on the board at overall pick `my_next_pick`)."""
    sigma = adp_sigma(adp, stdev)
    return 1.0 - phi((my_next_pick - adp) / sigma)


def snake_pick_numbers(slot: int, teams: int, rounds: int) -> list[int]:
    """Overall pick numbers owned by a draft slot (1-indexed) in a snake draft."""
    picks = []
    for rnd in range(1, rounds + 1):
        if rnd % 2 == 1:
            picks.append((rnd - 1) * teams + slot)
        else:
            picks.append(rnd * teams - slot + 1)
    return picks


def next_pick_after(current_pick: int, slot: int, teams: int, rounds: int) -> int | None:
    """My next overall pick strictly after `current_pick` (the pick now on the
    clock); None when I have no picks left."""
    for p in snake_pick_numbers(slot, teams, rounds):
        if p > current_pick:
            return p
    return None


@dataclass
class Candidate:
    player_id: str
    pos: str
    vbd: float
    survival: float  # to my next pick


def expected_best_vbd(pool: list[Candidate]) -> float:
    """E[max VBD among pool members that survive], assuming independent survival:
    sort by VBD desc; player i is the best available iff everyone better is gone
    and i survives. Closed form: Σ vbd_i · P_i · Π_{j<i}(1 − P_j)."""
    e, p_all_better_gone = 0.0, 1.0
    for c in sorted(pool, key=lambda c: -c.vbd):
        e += c.vbd * c.survival * p_all_better_gone
        p_all_better_gone *= (1.0 - c.survival)
        if p_all_better_gone < 1e-9:
            break
    return e


def suggestion_score(candidate: Candidate, position_pool: list[Candidate],
                     need_mult: float) -> float:
    """Regret avoided by taking him now. The wait-alternative INCLUDES the
    candidate himself — passing only costs value to the extent he might be
    gone, so a high-survival star correctly scores ~0 ("wait on him") while a
    vanishing one scores his full cliff."""
    return need_mult * (candidate.vbd - expected_best_vbd(position_pool))


def roster_need_multiplier(pos: str, my_pos_counts: dict[str, int],
                           roster_positions: list[str]) -> float:
    """1.0 while a dedicated starter slot is unfilled; 0.85 when only a flex
    share remains open; 0.55 once starters are covered (bench depth still has
    value). K/DEF stay at 0.25 until every skill starter slot is filled — their
    raw VBD would otherwise outbid late-round RB/WR value."""
    counts = dict(my_pos_counts)
    dedicated = {p: roster_positions.count(p) for p in ("QB", "RB", "WR", "TE", "K", "DEF")}
    flex_slots = roster_positions.count("FLEX")
    sflex_slots = roster_positions.count("SUPER_FLEX")

    def unfilled_dedicated(p: str) -> bool:
        return counts.get(p, 0) < dedicated.get(p, 0)

    skill_starters_open = any(unfilled_dedicated(p) for p in ("QB", "RB", "WR", "TE"))
    # Flex demand: skill players beyond dedicated slots occupy flex.
    flex_used = sum(max(0, counts.get(p, 0) - dedicated.get(p, 0)) for p in FLEX_ELIGIBLE)
    sflex_used = sum(max(0, counts.get(p, 0) - dedicated.get(p, 0)) for p in SUPER_FLEX_ELIGIBLE) - flex_used
    flex_open = flex_used < flex_slots
    sflex_open = sflex_used < sflex_slots

    if pos in ("K", "DEF"):
        if skill_starters_open or flex_open:
            return 0.25
        return 1.0 if unfilled_dedicated(pos) else 0.55
    if unfilled_dedicated(pos):
        return 1.0
    if pos in FLEX_ELIGIBLE and flex_open:
        return 0.85
    if pos in SUPER_FLEX_ELIGIBLE and sflex_open:
        return 0.85
    return 0.55
