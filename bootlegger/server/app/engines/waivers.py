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
# it continuously, so the ladder interpolates smoothly and still says "$21"
# rather than "$20" because the room bids round numbers.
#
# What it must NOT index on is rank. A percentile of the shortlist is a
# statement about the shape of one week's street, and rank 1 is rank 1 every
# week — so the best available always drew the top of the book even in a week
# with nothing on it. `value_fraction` below supplies an absolute position
# instead, measured against the roster the man would join. A target who does
# not crack your starting lineup is depth on top of that, and depth pays the
# depth price.
DEPTH_DISCOUNT = 0.5
MIN_LIVE_BID = 1
# A one-week rental is not a rest-of-season asset. Streaming never pays more
# than the middle of this room's book, whatever the week's arithmetic says.
STREAM_CAP = 0.5


def value_fraction(over_drop: float, roster_span: float) -> float:
    """Where a free agent sits on YOUR OWN roster's scale, in [0, 1].

    The percentile handed to `price_at` used to be the target's RANK within
    the twenty men being priced, which made it a statement about the shape of
    one week's shortlist rather than about the man. Rank 1 is rank 1 every
    week, so the best available always priced at the 100th percentile of the
    book — the most this room has ever paid — in a barren week as readily as
    in a week somebody's season-winner hit the street. Depth pricing was the
    only damper, and it does not apply to a man who starts.

    An absolute anchor fixes it without inventing a scale: measure him against
    the width of the roster he would join. `over_drop` is how far he sits
    above the man you would actually cut; `roster_span` is how far your BEST
    man sits above that same body. A free agent as good as your best player is
    a max-price add. One a tenth of the way up that span pays a tenth of the
    book. Both terms are season points from the same table, so the ratio is
    scale-free and survives the season-vs-week change of units that has
    broken thresholds here before.
    """
    if roster_span <= 0:
        return 1.0 if over_drop > 0 else 0.0
    return max(0.0, min(1.0, over_drop / roster_span))


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
