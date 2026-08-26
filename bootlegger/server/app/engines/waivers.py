"""Waiver/FAAB engine (design doc §4): FA score = ROS consensus value − worst
droppable roster value at the position; bid = league-history percentile for the
value tier (default P70), rounded to +$1 over round numbers; hard-confirm above
25% of remaining budget. Advisory only — there is no waiver actuation path in
this codebase, by design."""
from __future__ import annotations

import math
from dataclasses import dataclass

DEFAULT_PERCENTILE = 70.0
HARD_CONFIRM_FRACTION = 0.25


def percentile(values: list[float], pct: float) -> float:
    """Linear-interpolated percentile; empty history -> 0."""
    if not values:
        return 0.0
    vals = sorted(values)
    if len(vals) == 1:
        return vals[0]
    k = (pct / 100.0) * (len(vals) - 1)
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return vals[lo]
    return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)


def plus_one_over_round(bid: float) -> int:
    """League mates bid round numbers; be the +$1 over the round number."""
    if bid <= 0:
        return 1
    rounded = int(round(bid))
    if rounded % 5 == 0:
        return rounded + 1
    return max(1, rounded)


@dataclass
class BidAdvice:
    fa_score: float
    bid: int
    hard_confirm: bool
    history_n: int


# ---------------------------------------------------------------------------
# Continuous pricing
# ---------------------------------------------------------------------------
# The first version priced by TIER: hot / solid / dart, each band paying the
# P70 of the league's bids for that band. It inverted the point of the exercise.
# Observed on the live board: a free agent scoring 33.1 and one scoring 2.5 both
# came out at $6, while rank 1 to rank 2 fell $42 -> $17. Price has to track
# value, and a three-step staircase cannot.
#
# The fix keeps the same evidence — this league's own winning bids — and indexes
# it continuously. A man at the 90th percentile of this week's free agents is
# worth the 90th percentile of what this room has historically paid. That is
# monotone by construction, interpolates smoothly, and still says "$21" rather
# than "$20" because the room bids round numbers.
#
# One damper: a percentile is a statement about RANK, and in a barren week the
# best available man is still not worth a hot-add price. A target who does not
# crack your starting lineup is depth, and depth pays the depth price.
DEPTH_DISCOUNT = 0.5
MIN_LIVE_BID = 1


def price_at(value_pct: float, history: list[float], remaining_budget: int,
             starts: bool = True) -> int:
    """What to bid on a free agent sitting at `value_pct` of this week's pool.

    `value_pct` is a fraction in [0, 1] — 1.0 is the best man available. With
    no bid history the caller's fallback applies; here an empty book returns 0
    so the caller can tell "no evidence" from "worth nothing".
    """
    if not history:
        return 0
    pct = max(0.0, min(1.0, value_pct)) * 100.0
    raw = percentile(history, pct)
    if not starts:
        raw *= DEPTH_DISCOUNT
    bid = plus_one_over_round(raw)
    if remaining_budget:
        bid = min(bid, remaining_budget)
    return max(MIN_LIVE_BID, bid) if raw > 0 else 0


def enforce_ladder(bids: list[int]) -> list[int]:
    """Prices down a value-ranked list must never rise.

    Percentile interpolation is monotone, but the depth discount is not applied
    uniformly (a startable man ranked below a depth man legitimately pays more),
    and rounding to the +$1-over-round-number can push a lower-value target a
    dollar above the man above him. Clamping here means the printed ladder can
    always be read top to bottom as "most expensive first".
    """
    out: list[int] = []
    cap: int | None = None
    for b in bids:
        v = b if cap is None else min(b, cap)
        out.append(v)
        cap = v
    return out


def fa_score(fa_ros_value: float, worst_droppable_same_pos_value: float) -> float:
    return fa_ros_value - worst_droppable_same_pos_value


def size_bid(score: float, history_bids_for_tier: list[float],
             remaining_budget: int, pct: float = DEFAULT_PERCENTILE) -> BidAdvice:
    if score <= 0:
        return BidAdvice(fa_score=score, bid=0, hard_confirm=False,
                         history_n=len(history_bids_for_tier))
    p = percentile(history_bids_for_tier, pct)
    bid = plus_one_over_round(p) if history_bids_for_tier else max(1, int(round(score / 2)))
    bid = min(bid, remaining_budget)
    return BidAdvice(
        fa_score=score,
        bid=bid,
        hard_confirm=bid > HARD_CONFIRM_FRACTION * remaining_budget if remaining_budget else True,
        history_n=len(history_bids_for_tier),
    )
