"""Lineup optimizer: maximum-weight assignment of rostered players to starting
slots (optimal), then a diff against the actual starters read from the API.
Notify only when the gap clears materiality or an injury flag is involved
(design doc §4)."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import linear_sum_assignment

from ..config import FLEX_ELIGIBLE, MATERIALITY_PTS, SUPER_FLEX_ELIGIBLE

INactive = {"Out", "Doubtful", "IR", "Sus", "PUP", "NA"}
# Out/Doubtful are THIS WEEK's condition; IR/PUP/Sus/NA carry across the
# season. Rest-of-season math (trades, waiver value, dossier, draft grades)
# must only zero the long-term ones — treating a one-week Out tag as
# season-worthless made the Parlor value dumping a healthy starter at +50.
SEASON_INACTIVE = {"IR", "Sus", "PUP", "NA"}


def ros_status(status: str | None) -> str | None:
    """The injury tag as rest-of-season math should see it."""
    return status if (status or "") in SEASON_INACTIVE else None
_PENALTY = 1e6  # cost of an ineligible pairing; also flags unfillable slots


def slot_accepts(slot: str, pos: str) -> bool:
    if slot == "FLEX":
        return pos in FLEX_ELIGIBLE
    if slot in ("SUPER_FLEX", "SUPERFLEX"):
        return pos in SUPER_FLEX_ELIGIBLE
    if slot in ("WRRB_FLEX", "REC_FLEX"):
        return pos in FLEX_ELIGIBLE
    return slot == pos


# Week-to-week spread of a player's score around his projection, as a
# coefficient of variation, by position. Only a fallback: when Draft Sharks'
# own floor/ceiling is on the wire it wins, because it is measured rather than
# assumed. These are deliberately coarse — the ordering (quarterbacks steady,
# defenses wild) is what the floor/ceiling lineups actually key off, and that
# ordering is not controversial.
POS_CV = {"QB": 0.35, "RB": 0.55, "WR": 0.60, "TE": 0.65, "K": 0.45, "DEF": 0.70}
# Roughly a 20th/80th percentile band on a normal — wide enough to separate a
# boom candidate from a metronome, narrow enough not to invent outcomes.
_BAND_Z = 0.85


@dataclass
class PlayerProj:
    player_id: str
    pos: str
    proj: float
    name: str = ""
    injury_status: str | None = None
    on_bye: bool = False
    # Measured floor/ceiling for THIS week when a source supplies them
    # (Draft Sharks, per game). None falls back to the positional band.
    floor: float | None = None
    ceiling: float | None = None

    @property
    def startable_proj(self) -> float:
        """OUT/bye players contribute zero — the optimizer must route around them."""
        if self.on_bye or (self.injury_status or "") in INactive:
            return 0.0
        return self.proj

    def _band(self, low: bool) -> float:
        """The startable floor or ceiling. A man who cannot play has both at
        zero — an unavailable starter has no upside either."""
        base = self.startable_proj
        if base <= 0:
            return 0.0
        measured = self.floor if low else self.ceiling
        if measured is not None:
            # A source band around a different mean still has to bracket the
            # projection this lineup is built on.
            return max(0.0, min(measured, base) if low else max(measured, base))
        sd = POS_CV.get(self.pos, 0.55) * base
        return max(0.0, base - _BAND_Z * sd) if low else base + _BAND_Z * sd

    @property
    def startable_floor(self) -> float:
        return self._band(low=True)

    @property
    def startable_ceiling(self) -> float:
        return self._band(low=False)


# What a lineup is being optimized FOR. See engines/matchup.py for why the
# answer is not always "expected points".
OBJECTIVES = {
    "proj": lambda p: p.startable_proj,
    "floor": lambda p: p.startable_floor,
    "ceiling": lambda p: p.startable_ceiling,
}


@dataclass
class OptimalLineup:
    assignment: list[tuple[str, PlayerProj]] = field(default_factory=list)  # (slot, player)
    total: float = 0.0              # always EXPECTED points, whatever the objective
    objective: str = "proj"
    objective_total: float = 0.0    # the quantity this lineup actually maximised
    floor_total: float = 0.0
    ceiling_total: float = 0.0

    @property
    def starter_ids(self) -> set[str]:
        return {p.player_id for _, p in self.assignment}


def optimize(players: list[PlayerProj], roster_positions: list[str],
             objective: str = "proj") -> OptimalLineup:
    """Hungarian assignment over eligible (player, slot) pairs — provably optimal,
    trivial at roster scale. BN/IR pseudo-slots are ignored.

    `objective` picks what "optimal" means: expected points (the default and
    the right answer in a close week), the highest floor, or the highest
    ceiling. `total` is always reported in EXPECTED points whatever the
    objective, so three lineups can be compared on one scale; the objective
    value itself is on `objective_total`.
    """
    value = OBJECTIVES.get(objective, OBJECTIVES["proj"])
    slots = [s for s in roster_positions if s not in ("BN", "IR", "TAXI")]
    if not slots or not players:
        return OptimalLineup()
    cost = np.full((len(players), len(slots)), _PENALTY, dtype=float)
    for i, p in enumerate(players):
        for j, s in enumerate(slots):
            if slot_accepts(s, p.pos):
                cost[i, j] = -value(p)
    rows, cols = linear_sum_assignment(cost)
    lineup = OptimalLineup(objective=objective)
    for i, j in zip(rows, cols):
        if cost[i, j] >= _PENALTY:  # slot had no eligible player
            continue
        lineup.assignment.append((slots[j], players[i]))
        lineup.total += players[i].startable_proj
        lineup.objective_total += value(players[i])
        lineup.floor_total += players[i].startable_floor
        lineup.ceiling_total += players[i].startable_ceiling
    lineup.assignment.sort(key=lambda t: slots.index(t[0]))
    return lineup


@dataclass
class LineupDiff:
    swaps: list[dict] = field(default_factory=list)  # {out_id,in_id,slot,gain}
    delta: float = 0.0
    injury_flag: bool = False
    optimal: OptimalLineup | None = None
    actual_total: float = 0.0

    @property
    def material(self) -> bool:
        return self.delta > MATERIALITY_PTS or (self.injury_flag and bool(self.swaps))


def diff_lineup(players: list[PlayerProj], actual_starter_ids: list[str],
                roster_positions: list[str]) -> LineupDiff:
    by_id = {p.player_id: p for p in players}
    optimal = optimize(players, roster_positions)
    actual = [by_id[pid] for pid in actual_starter_ids if pid in by_id]
    actual_total = sum(p.startable_proj for p in actual)
    d = LineupDiff(optimal=optimal, actual_total=actual_total,
                   delta=optimal.total - actual_total)
    actual_ids = {p.player_id for p in actual}
    d.injury_flag = any(
        (p.injury_status or "") in INactive or p.on_bye for p in actual
    )
    ins = [(slot, p) for slot, p in optimal.assignment if p.player_id not in actual_ids]
    outs = [p for p in actual if p.player_id not in optimal.starter_ids]
    outs.sort(key=lambda p: p.startable_proj)          # weakest actual starter first
    ins.sort(key=lambda t: -t[1].startable_proj)       # strongest missing starter first
    for (slot, p_in), p_out in zip(ins, outs):
        d.swaps.append({
            "out_id": p_out.player_id,
            "in_id": p_in.player_id,
            "slot": slot,
            "gain": round(p_in.startable_proj - p_out.startable_proj, 2),
        })
    return d
