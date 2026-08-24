"""Trade analyzer (design doc §4): side sums of ROS VBD + FantasyCalc redraft
values as the market sanity check + a positional-need overlay. Output is
analysis only — trades have no actuation path, structurally."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SideView:
    player_ids: list[str]
    vbd_total: float = 0.0
    market_total: float = 0.0
    players: list[dict] = field(default_factory=list)


def analyze(side_a: list[dict], side_b: list[dict],
            my_needs: list[str] | None = None) -> dict:
    """Each player dict: {player_id, name, pos, vbd, market_value}. Side A is
    what I give, side B what I receive."""
    def side(rows: list[dict]) -> SideView:
        return SideView(
            player_ids=[r["player_id"] for r in rows],
            vbd_total=round(sum(r.get("vbd", 0.0) for r in rows), 1),
            market_total=round(sum(r.get("market_value", 0.0) for r in rows), 0),
            players=rows,
        )

    a, b = side(side_a), side(side_b)
    vbd_edge = round(b.vbd_total - a.vbd_total, 1)
    market_edge = round(b.market_total - a.market_total, 0)
    needs = set(my_needs or [])
    fills_need = sorted({r["pos"] for r in side_b if r["pos"] in needs})
    verdicts = []
    if vbd_edge > 5:
        verdicts.append(f"Projection math favors you by {vbd_edge:+.1f} ROS VBD.")
    elif vbd_edge < -5:
        verdicts.append(f"Projection math is against you by {vbd_edge:+.1f} ROS VBD.")
    else:
        verdicts.append("Projection math calls it roughly even.")
    if market_edge * vbd_edge < 0:
        verdicts.append("Market values disagree with the projection read — treat with suspicion.")
    elif abs(market_edge) > 0:
        side_name = "you" if market_edge > 0 else "them"
        verdicts.append(f"Market cross-check leans {side_name} ({market_edge:+.0f} FantasyCalc).")
    if fills_need:
        verdicts.append(f"Fills your open need at {', '.join(fills_need)}.")
    return {
        "give": a.__dict__,
        "receive": b.__dict__,
        "vbd_edge": vbd_edge,
        "market_edge": market_edge,
        "fills_needs": fills_need,
        "summary": " ".join(verdicts),
    }
