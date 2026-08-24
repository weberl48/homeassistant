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
_PENALTY = 1e6  # cost of an ineligible pairing; also flags unfillable slots


def slot_accepts(slot: str, pos: str) -> bool:
    if slot == "FLEX":
        return pos in FLEX_ELIGIBLE
    if slot in ("SUPER_FLEX", "SUPERFLEX"):
        return pos in SUPER_FLEX_ELIGIBLE
    if slot in ("WRRB_FLEX", "REC_FLEX"):
        return pos in FLEX_ELIGIBLE
    return slot == pos


@dataclass
class PlayerProj:
    player_id: str
    pos: str
    proj: float
    name: str = ""
    injury_status: str | None = None
    on_bye: bool = False

    @property
    def startable_proj(self) -> float:
        """OUT/bye players contribute zero — the optimizer must route around them."""
        if self.on_bye or (self.injury_status or "") in INactive:
            return 0.0
        return self.proj


@dataclass
class OptimalLineup:
    assignment: list[tuple[str, PlayerProj]] = field(default_factory=list)  # (slot, player)
    total: float = 0.0

    @property
    def starter_ids(self) -> set[str]:
        return {p.player_id for _, p in self.assignment}


def optimize(players: list[PlayerProj], roster_positions: list[str]) -> OptimalLineup:
    """Hungarian assignment over eligible (player, slot) pairs — provably optimal,
    trivial at roster scale. BN/IR pseudo-slots are ignored."""
    slots = [s for s in roster_positions if s not in ("BN", "IR", "TAXI")]
    if not slots or not players:
        return OptimalLineup()
    cost = np.full((len(players), len(slots)), _PENALTY, dtype=float)
    for i, p in enumerate(players):
        for j, s in enumerate(slots):
            if slot_accepts(s, p.pos):
                cost[i, j] = -p.startable_proj
    rows, cols = linear_sum_assignment(cost)
    lineup = OptimalLineup()
    for i, j in zip(rows, cols):
        if cost[i, j] >= _PENALTY:  # slot had no eligible player
            continue
        lineup.assignment.append((slots[j], players[i]))
        lineup.total += players[i].startable_proj
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
