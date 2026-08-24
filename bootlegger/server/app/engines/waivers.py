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
