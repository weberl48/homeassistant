"""Value Based Drafting over VOLS baselines (design doc §4):
vbd = projected points − points of the baseline player at that position."""
from __future__ import annotations

import math

from ..config import VOLS_BASELINES

# How flex starts actually distribute in practice: mostly WR, a solid RB
# share, TEs almost never. floor() on the shares makes the 12-team 2-flex
# derivation land exactly on the hand-tuned design-doc table (31/40/12) —
# pinned by test, so live values cannot shift under this refactor.
FLEX_WEIGHTS = {"RB": 0.30, "WR": 0.67, "TE": 0.03}
FLEX_SLOTS = {"FLEX", "WRRB_FLEX", "REC_FLEX"}
SFLEX_SLOTS = {"SUPER_FLEX", "SUPERFLEX"}


def derive_baselines(teams: int, roster_positions: list[str]) -> dict[str, int]:
    """VOLS baselines from the league's own shape instead of a hardcoded
    12-team table: dedicated starters × teams, plus each position's share of
    the flex pool (superflex feeds QB)."""
    counts: dict[str, int] = {}
    for s in roster_positions:
        if s in ("QB", "RB", "WR", "TE", "K", "DEF"):
            counts[s] = counts.get(s, 0) + 1
    flex = sum(1 for s in roster_positions if s in FLEX_SLOTS) * teams
    sflex = sum(1 for s in roster_positions if s in SFLEX_SLOTS) * teams
    out = {}
    for pos in ("QB", "RB", "WR", "TE", "K", "DEF"):
        n = counts.get(pos, 0) * teams
        n += math.floor(flex * FLEX_WEIGHTS.get(pos, 0.0))
        if pos == "QB" and sflex:
            n += math.floor(sflex * 0.75)
        out[pos] = max(n, 1)
    return out


def baseline_points(pos_points_desc: list[float], pos: str,
                    baselines: dict[str, int] | None = None) -> float:
    """Points of the Nth-ranked player at the position (N = VOLS baseline).
    When the pool is shallower than the baseline, the last player is the baseline."""
    baselines = baselines or VOLS_BASELINES
    n = baselines.get(pos, len(pos_points_desc))
    if not pos_points_desc:
        return 0.0
    idx = min(n, len(pos_points_desc)) - 1
    return pos_points_desc[idx]


def compute_vbd(points_by_pos: dict[str, list[tuple[str, float]]],
                baselines: dict[str, int] | None = None) -> dict[str, float]:
    """{player_id: vbd} across all positions. Input lists are (player_id, pts),
    any order."""
    out: dict[str, float] = {}
    for pos, rows in points_by_pos.items():
        ranked = sorted(rows, key=lambda r: -r[1])
        base = baseline_points([pts for _, pts in ranked], pos, baselines)
        for pid, pts in ranked:
            out[pid] = pts - base
    return out
