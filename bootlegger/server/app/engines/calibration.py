"""Source calibration: letting the sources earn their weight.

Six projection sets go into every consensus, and until now each carried exactly
the same vote. That is the defensible choice on day one — with no evidence, an
equal weight is the honest prior, and a median-trimmed mean already protects
against one source being wild. It is the wrong choice by December, because by
then there IS evidence: every weekly projection this board stored can be laid
against what the player actually scored under this league's own scoring.

So the consensus becomes self-calibrating. Each source is scored on mean
absolute error over the players who mattered, its weight is set inverse to that
error, and the whole apparatus stays dormant behind a sample-size gate until it
has enough weeks to mean anything. Three deliberate conservatisms:

1. **Only fantasy-relevant players count.** Scoring every projection would let
   a source win by correctly predicting that the league's 400th receiver scores
   nothing. Accuracy contests filter the same way, and so does this.
2. **Weights are shrunk toward equal.** A source 10% more accurate over half a
   season has not earned three times the vote. The blend keeps the equal-weight
   prior in the mix so one hot stretch cannot capture the consensus.
3. **Weights are clamped.** No source may fall below a floor share or rise
   above a ceiling share, so a source that dies mid-season (and reports its
   last good numbers forever) cannot quietly take over.

None of this changes week 0 — a season-long projection has nothing realized to
be scored against until the season is over, which is exactly when it stops
mattering. Draft-day math keeps the equal-weight robust mean.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

# Below this projection a player is not a lineup decision, and predicting his
# zero correctly is not a skill worth weighting.
RELEVANT_PTS = 6.0
# Player-weeks per source before its error is worth acting on. Roughly two
# weeks of a full slate — enough that one blow-up Sunday cannot set the weights.
MIN_SAMPLE = 150
# How far toward the measured weights we move from equal. 0 = never calibrate,
# 1 = trust the measurement completely. Half keeps the prior honest.
SHRINK = 0.5
# No source may hold less or more than this share of the total vote.
MIN_SHARE = 0.06
MAX_SHARE = 0.35


@dataclass(frozen=True)
class SourceScore:
    source: str
    n: int
    mae: float

    def as_dict(self) -> dict:
        return {"source": self.source, "n": self.n, "mae": round(self.mae, 2)}


def score_sources(conn: sqlite3.Connection, season: int,
                  relevant_pts: float = RELEVANT_PTS) -> list[SourceScore]:
    """Mean absolute error per source over every scored player-week on the books.

    Relevance is judged by the ACTUAL score or the projection, whichever is
    higher: a source that projected 2 for a man who scored 24 must be charged
    for the miss, and one that projected 24 for a man who scored 2 must be
    charged too. Filtering on the projection alone would forgive the first;
    filtering on the actual alone would forgive the second.
    """
    rows = conn.execute(
        "SELECT p.source, COUNT(*) n, AVG(ABS(p.pts - a.pts)) mae "
        "FROM projections p JOIN player_week_actuals a "
        "  ON a.player_id = p.player_id AND a.week = p.week AND a.season = ? "
        "WHERE p.week > 0 AND (a.pts >= ? OR p.pts >= ?) "
        "GROUP BY p.source", (season, relevant_pts, relevant_pts)).fetchall()
    return [SourceScore(r["source"], r["n"], r["mae"]) for r in rows if r["mae"] is not None]


def weights(scores: list[SourceScore], sources: list[str],
            min_sample: int = MIN_SAMPLE) -> tuple[dict[str, float], str]:
    """{source: weight} summing to 1, plus a one-line provenance.

    Any source without enough scored player-weeks keeps the equal weight — it
    is not penalised for being new, only for being wrong.
    """
    equal = {s: 1.0 / len(sources) for s in sources} if sources else {}
    scored = {s.source: s for s in scores if s.n >= min_sample and s.mae > 0}
    if len(scored) < 2:
        return equal, (f"equal weight — {len(scored)} of {len(sources)} sources have "
                       f"{min_sample}+ scored player-weeks")

    # Inverse error: a source with half the MAE gets twice the raw vote.
    raw = {s: 1.0 / scored[s].mae if s in scored else None for s in sources}
    known = [v for v in raw.values() if v is not None]
    fill = sum(known) / len(known)          # unscored sources sit at the average
    raw = {s: (v if v is not None else fill) for s, v in raw.items()}
    total = sum(raw.values())
    measured = {s: v / total for s, v in raw.items()}

    # Shrink toward equal, then project onto the share band.
    blended = {s: SHRINK * measured[s] + (1 - SHRINK) * equal[s] for s in sources}
    final = clamp_to_band(blended)
    best = min(scored.values(), key=lambda s: s.mae)
    return final, (f"calibrated on {sum(s.n for s in scored.values())} scored "
                   f"player-weeks; {best.source} leads at {best.mae:.2f} MAE")


def clamp_to_band(raw: dict[str, float], lo: float = MIN_SHARE,
                  hi: float = MAX_SHARE, iterations: int = 200) -> dict[str, float]:
    """Weights inside [lo, hi] that still sum to 1.

    Clamping and THEN renormalising does not work — rescaling pushes the
    clamped source straight back over its ceiling, which is exactly how a
    "capped" weight came out at 0.49 against a 0.35 cap. Alternating clamp and
    rescale converges instead: each pass moves mass off the pegged sources onto
    the free ones and never back. Falls back to equal weight if the band cannot
    hold every source (n*hi < 1 or n*lo > 1), which is a configuration error,
    not a runtime one.
    """
    n = len(raw)
    if not n:
        return {}
    if n * hi < 1.0 - 1e-9 or n * lo > 1.0 + 1e-9:
        return {k: 1.0 / n for k in raw}
    w = {k: min(hi, max(lo, v)) for k, v in raw.items()}
    for _ in range(iterations):
        total = sum(w.values())
        if abs(total - 1.0) < 1e-12:
            break
        w = {k: min(hi, max(lo, v / total)) for k, v in w.items()}
    return w


def weighted_mean(values: dict[str, float], weights_map: dict[str, float]) -> float:
    """Weighted average over whatever sources answered for THIS player.

    Renormalising over the present sources is the point: a player only CBS and
    ESPN carry must not be dragged toward zero by four absent votes.
    """
    if not values:
        raise ValueError("no values")
    total = sum(weights_map.get(s, 0.0) for s in values)
    if total <= 0:
        return sum(values.values()) / len(values)
    return sum(v * weights_map.get(s, 0.0) for s, v in values.items()) / total
