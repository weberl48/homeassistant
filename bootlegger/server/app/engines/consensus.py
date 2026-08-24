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
