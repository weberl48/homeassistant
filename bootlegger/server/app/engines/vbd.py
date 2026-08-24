"""Value Based Drafting over VOLS baselines (design doc §4):
vbd = projected points − points of the baseline player at that position."""
from __future__ import annotations

from ..config import VOLS_BASELINES


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
