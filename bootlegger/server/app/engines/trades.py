"""Trade analyzer (design doc §4): side sums of ROS VBD + FantasyCalc redraft
values as the market sanity check + a positional-need overlay. Output is
analysis only — trades have no actuation path, structurally."""
from __future__ import annotations

from dataclasses import dataclass, field

# Package math, KTC-school: roster slots are finite, so one stud beats the sum
# of mid pieces — every serious calculator credits the side receiving fewer
# players (KeepTradeCut's raw-adjustment curve, FantasyCalc's fewer-players
# credit). A superlinear exponent is the simplest faithful version: at 1.35,
# one 100-worth player beats two 50s by ~28%.
CONSOLIDATION_GAMMA = 1.35


def consolidated(values: list[float]) -> float:
    """Package-adjusted side total over per-player worth values."""
    return sum(max(0.0, v) ** CONSOLIDATION_GAMMA for v in values)


# ---------------------------------------------------------------------------
# Shortlisting: why a list of eight deals must not be one deal eight times
# ---------------------------------------------------------------------------
# The generator enumerates packages; the lineup optimizer scores them. Those two
# facts collide badly: adding a bench piece that cracks NEITHER starting lineup
# leaves both sides' lineup deltas exactly where they were, so one real move
# reappears under every combination of throw-ins. Measured on the demo league
# before this pass: 20 proposals, 14 with the same partner, six distinct
# outcomes between them, and a variant whose own summary read "projection math
# is against you by -33.5 ROS VBD" sitting fourth. Every one of those extra
# rows costs the reader time and buys nothing.
#
# Three filters, in order, each answering a different failure:
#   dominance  — the extra men change no outcome, so the smaller deal wins
#   outcomes   — genuinely different packages that land on the same number
#   partners   — one seat's surplus should not eat the whole shortlist

# Lineup deltas are rounded to a tenth downstream; anything inside this is the
# same outcome wearing different clothes.
_EPS = 0.05


def dominates(a: dict, b: dict) -> bool:
    """True when deal `a` makes deal `b` pointless.

    `a` dominates `b` when it is a strict SUB-package of it — every man `a`
    sends is also sent in `b`, every man `a` receives is also received in `b`,
    and `b` moves at least one extra body — while doing at least as well for
    both sides. The extra bodies are then pure cost: more names to negotiate,
    more roster churn, no gain.
    """
    ga, gb = set(a["give_ids"]), set(b["give_ids"])
    ra, rb = set(a["receive_ids"]), set(b["receive_ids"])
    if not (ga <= gb and ra <= rb):
        return False
    if len(ga) + len(ra) >= len(gb) + len(rb):
        return False
    return (a["my_gain"] >= b["my_gain"] - _EPS
            and a["their_gain"] >= b["their_gain"] - _EPS)


def shortlist(proposals: list[dict], limit: int = 8,
              per_outcome: int = 1) -> list[dict]:
    """The deals actually worth showing, best first.

    Ranking is the caller's `score`; this only decides what survives. Partner
    spread is a ROUND-ROBIN rather than a hard cap: every seat's best deal
    goes in before anyone's second. The best deal in the room is therefore
    never dropped for diversity's sake — it just doesn't get to bring seven
    cousins — and a thin room still fills the list, it just fills it with the
    same seats' deeper offers.
    """
    ranked = sorted(proposals, key=lambda t: -t["score"])

    kept: list[dict] = []
    for p in ranked:
        if any(dominates(q, p) for q in ranked if q is not p):
            continue
        kept.append(p)

    # Same partner, same pair of numbers — different names on the paperwork.
    # Keep the highest-scoring one; the rest are noise.
    seen: dict[tuple, int] = {}
    deduped: list[dict] = []
    for p in kept:
        key = (p["partner_roster_id"], round(p["my_gain"], 1), round(p["their_gain"], 1))
        if seen.get(key, 0) >= per_outcome:
            continue
        seen[key] = seen.get(key, 0) + 1
        deduped.append(p)

    # Round-robin by partner: everyone's best deal first, then seconds, and so
    # on. A room with one obvious trade partner still shows him twice at the
    # top — it just doesn't show him eight times.
    by_partner: dict[int, list[dict]] = {}
    for p in deduped:
        by_partner.setdefault(p["partner_roster_id"], []).append(p)
    order = sorted(by_partner, key=lambda k: -by_partner[k][0]["score"])
    depth = max((len(g) for g in by_partner.values()), default=0)
    out: list[dict] = []
    # Later rounds only run when the room ran out of distinct partners before
    # the list ran out of room, and they keep round-robining — so a thin room
    # degrades to "the deepest seats' second offers", never to "one seat's
    # entire surplus".
    for rank in range(depth):
        if len(out) >= limit:
            break
        for pid in order:
            if len(out) >= limit:
                break
            group = by_partner[pid]
            if rank < len(group):
                out.append(group[rank])
    return sorted(out, key=lambda t: -t["score"])[:limit]


# Thresholds. The value edge is in season points, so a handful of them is
# noise next to a lineup gain measured in the same unit. The market edge is in
# FantasyCalc units, which run into the thousands — the first version tripped
# its warning on a -2 edge, which is a rounding error wearing a siren.
VALUE_NOISE = 10.0          # ROS VBD points
MARKET_NOISE = 150.0        # FantasyCalc units, floor
MARKET_NOISE_SHARE = 0.05   # ...or this share of the deal's own market volume


def value_verdict(my_gain: float, vbd_edge: float, market_edge: float,
                  give_n: int, receive_n: int,
                  market_volume: float | None = None) -> dict:
    """Reconcile the two numbers a trade card shows, because on their face they
    contradict each other.

    The headline is a LINEUP delta — what this week's starting nine gains. The
    value edge is a SEASON-ASSET delta — what the roster is worth afterwards.
    A deal can genuinely win one and lose the other: cashing a backup
    quarterback nobody starts for a startable back gains points today and loses
    paper value. That is a real trade-off and worth taking. What is NOT worth
    taking is losing both, and a card that shows "+33.4 you" above a sentence
    reading "projection math is against you by -33.5" has told the reader
    nothing about which it is.
    """
    market_floor = max(MARKET_NOISE, MARKET_NOISE_SHARE * abs(market_volume or 0.0))
    losing_value = vbd_edge < -VALUE_NOISE
    losing_market = market_edge < -market_floor
    if not losing_value and not losing_market:
        return {"level": "good",
                "line": "Lineup and value both move your way."}
    # Losing on both counts is only a WARNING when the week you win is smaller
    # than the season value you hand over. Gaining thirty points of starting
    # lineup for nine points of paper is a trade worth making, and calling it a
    # warning teaches the reader to ignore warnings.
    if losing_value and losing_market and my_gain < abs(vbd_edge):
        return {"level": "warn",
                "line": ("You lose more season value than the week gains you — "
                         "both the projections and the market say you're "
                         "paying over the odds.")}
    thin = "give" if give_n > receive_n else "receive"
    return {"level": "note",
            "line": ("Points today, paper value away: you're converting depth "
                     f"into starters. Fair when the {thin} side is the one you "
                     "can't start anyway.")}


@dataclass
class SideView:
    player_ids: list[str]
    vbd_total: float = 0.0
    market_total: float = 0.0
    players: list[dict] = field(default_factory=list)


def analyze(side_a: list[dict], side_b: list[dict],
            my_needs: list[str] | None = None,
            vbd_scale: float | None = None,
            market_scale: float | None = None) -> dict:
    """Each player dict: {player_id, name, pos, vbd, market_value}. Side A is
    what I give, side B what I receive. When the caller supplies the league's
    worth scales (max VBD / max market value), sides also get a
    package-adjusted comparison via consolidated()."""
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

    consolidation_edge = None
    if vbd_scale and market_scale:
        def worths(rows: list[dict]) -> list[float]:
            return [50.0 * max(0.0, r.get("vbd", 0.0)) / vbd_scale
                    + 50.0 * max(0.0, r.get("market_value", 0.0)) / market_scale
                    for r in rows]
        ca, cb = consolidated(worths(side_a)), consolidated(worths(side_b))
        consolidation_edge = round(cb - ca, 1)
        if len(side_a) != len(side_b) and vbd_edge * consolidation_edge < 0:
            heavier = "give" if len(side_a) > len(side_b) else "receive"
            verdicts.append(
                "The thin side wins on package math — depth pieces don't start; "
                f"the {heavier}-more-players side is paying the consolidation premium.")

    return {
        "give": a.__dict__,
        "receive": b.__dict__,
        "vbd_edge": vbd_edge,
        "market_edge": market_edge,
        "consolidation_edge": consolidation_edge,
        "fills_needs": fills_need,
        "summary": " ".join(verdicts),
    }
