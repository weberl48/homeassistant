"""Consensus projections: robust (median-trimmed) average across sources.
Simple beats clever (design doc §4)."""
from __future__ import annotations

import statistics


def robust_mean(values: list[float]) -> float:
    """n<=2: plain mean. n==3: median. n>=4: trimmed mean dropping one high
    and one low — the 'median-trimmed robust average'."""
    vals = sorted(values)
    n = len(vals)
    if n == 0:
        raise ValueError("no values")
    if n <= 2:
        return sum(vals) / n
    if n == 3:
        return vals[1]
    trimmed = vals[1:-1]
    return sum(trimmed) / len(trimmed)


def source_spread(values: list[float]) -> float | None:
    """Sample stdev across sources; None when a single source (caller decides
    the fallback). Used both for display and the disagreement don't-act rule."""
    if len(values) < 2:
        return None
    return statistics.stdev(values)


def disagreement_ratio(values: list[float]) -> float:
    """Relative spread, guarded against tiny means; feeds the 25% don't-act rule."""
    if len(values) < 2:
        return 0.0
    m = robust_mean(values)
    if abs(m) < 1.0:
        return 0.0
    sd = source_spread(values) or 0.0
    return sd / abs(m)


def trim_extremes(by_source: dict[str, float]) -> dict[str, float]:
    """The same drop-one-high, drop-one-low the robust mean applies, but
    keeping the source labels so a weighted average can follow.

    This exists because calibration removed the protection by accident. Once
    six sources have earned distinct weights, the consensus takes the weighted
    mean — which has no trimming at all — and it takes it at n>=4, exactly
    where the trimmed mean was the only thing standing between the board and a
    broken feed. ESPN's keyless endpoint returning SEASON totals for a week is
    the real case: Josh Allen at 419 against five sources saying 13-15 pulls a
    14.4 consensus to 81.6, and inverse-MAE weights cannot save it because a
    source that was fine all season still carries a full vote on the day it
    breaks. Even clamped to calibration's 6% floor it lands at 38.

    A weight says how accurate a source usually is. Trimming says how wrong
    this ONE number looks against its peers right now. They answer different
    questions and the consensus wants both.

    Below four sources nothing is trimmed — with three, dropping two leaves a
    single unchecked vote, which is worse than the disagreement it removes.
    """
    if len(by_source) < 4:
        return dict(by_source)
    ordered = sorted(by_source.items(), key=lambda kv: kv[1])
    return dict(ordered[1:-1])
