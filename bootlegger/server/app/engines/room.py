"""Reading THIS room, not the average room.

Survival probability is the load-bearing number on draft night: pass on a man
and the whole question is whether he comes back around. It is computed from
national ADP — the average of thousands of drafts full of strangers. But this
board is pointed at one twelve-seat room that has drafted together before, and
that room has habits. A room that always takes quarterbacks two rounds early
makes national QB ADP a systematically wrong answer to "will he last?".

The correction has to come from something the past actually recorded. Historical
ADP does not exist here — nobody stored what a 2024 player's ADP was — so the
comparison is made on POSITIONAL DEMAND CURVES instead, which need no history
beyond the picks themselves:

- **The room's curve.** Across this league's past drafts, at what overall pick
  did the k-th quarterback come off the board? The k-th running back?
- **The market's curve.** Sorting today's players by ADP, where does the market
  expect the k-th quarterback to go?

The gap between the two, per position, is this room's tendency, in picks. A
positive offset means the room drafts that position EARLIER than the market,
which lowers every survival estimate at that position — exactly the correction
the board needs.

Three guards, because a wrong correction is worse than none:

1. **Shape must match.** A 10-team draft's curve says nothing about a 12-team
   room. Past drafts with a different team count are dropped.
2. **The sample must be real.** One past draft is an anecdote. Below the
   minimum, offsets stay zero and the board says it is running on market ADP.
3. **The correction is capped.** However emphatic the history, no position is
   shifted more than a round — beyond that the model is fitting noise, and the
   cost of being wrong compounds across every survival number on the board.
4. **The offsets are centred.** A room cannot draft every position earlier than
   the market, nor every one later; both curves describe the same 180 picks. A
   constant shift across all six is therefore an artifact of curve
   construction, not a habit, and is subtracted. This guard earned its place
   immediately: the first live run reported all six positions sliding, which
   turned out to be a duplicated ADP row inflating the market curve's density.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass

# Past drafts of this league needed before its habits are trusted at all.
MIN_DRAFTS = 2
# How deep into each position the curve is measured. The first dozen at a
# position are the ones a draft-day decision is ever about; the tail is
# where rooms differ for reasons that are not tendencies.
CURVE_DEPTH = 12
# Positions with fewer than this many observed picks in a season are skipped
# for that season rather than extrapolated.
MIN_PICKS_PER_POS = 3
# Hard cap on the correction, in picks. One round.
MAX_OFFSET = 12.0
# Confidence bar for trusting a tendency at all, as offset over spread.
#
# Both the words and the arithmetic answer to this. The first version of this
# comment argued the numbers needed no such bar — widen_sigma already folds the
# room's spread into the survival curve, so an erratic position gets a flatter
# curve rather than a confident shift, and that was called "the honest
# treatment, available to arithmetic".
#
# Half right, and the half that was wrong cost the 2026 draft its defense.
# Widening flattens a curve; the SHIFT still moves its centre by the untrusted
# number. Defenses measured -12.0 at a signal of 1.32 — pinned to the cap by
# three drafts that disagreed across nearly its whole width — and the board
# duly expected them to slide twelve picks. The best one went eleven picks
# before the seat that wanted it. About 24.6 points, the largest measurable
# engine cost of that night.
#
# So: a number you would not say out loud is a number you should not draft on.
# read_out gates the sentence, adjust_adp gates the shift, both on this.
MIN_SIGNAL = 2.0

_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")


@dataclass(frozen=True)
class Tendency:
    pos: str
    offset: float      # picks EARLIER than the market (positive = reaches)
    drafts: int
    spread: float      # how consistent the room is about it

    @property
    def direction(self) -> str:
        return "early" if self.offset > 0 else "late"

    def as_dict(self) -> dict:
        return {"pos": self.pos, "offset": round(self.offset, 1),
                "drafts": self.drafts, "spread": round(self.spread, 1),
                "direction": self.direction}


def room_curve(picks: list[dict], depth: int = CURVE_DEPTH) -> dict[str, list[float]]:
    """{position: [overall pick of the 1st, 2nd, ... at that position]}.

    `picks` are one draft's rows, each {pick_no, pos}, in any order.
    """
    out: dict[str, list[float]] = {}
    for p in sorted(picks, key=lambda r: r["pick_no"]):
        pos = (p.get("pos") or "").upper()
        if pos not in _POSITIONS:
            continue
        seq = out.setdefault(pos, [])
        if len(seq) < depth:
            seq.append(float(p["pick_no"]))
    return out


def market_curve(adp_rows: list[dict], depth: int = CURVE_DEPTH) -> dict[str, list[float]]:
    """The same curve as the market expects it: {position: [ADP of the k-th]}.

    `adp_rows` are {pos, adp} for every player carrying an ADP.
    """
    out: dict[str, list[float]] = {}
    for r in sorted(adp_rows, key=lambda r: r["adp"]):
        pos = (r.get("pos") or "").upper()
        if pos not in _POSITIONS:
            continue
        seq = out.setdefault(pos, [])
        if len(seq) < depth:
            seq.append(float(r["adp"]))
    return out


def tendencies(room_curves: list[dict[str, list[float]]],
               market: dict[str, list[float]],
               min_drafts: int = MIN_DRAFTS) -> dict[str, Tendency]:
    """This room's per-position offset against the market, in picks.

    Positive = the room takes that position earlier than the market does, so a
    player there is LESS likely to survive than national ADP suggests.
    """
    if len(room_curves) < min_drafts:
        return {}
    out: dict[str, Tendency] = {}
    raw: dict[str, tuple[float, float, int]] = {}
    for pos in _POSITIONS:
        want = market.get(pos) or []
        if len(want) < MIN_PICKS_PER_POS:
            continue
        per_draft: list[float] = []
        for curve in room_curves:
            got = curve.get(pos) or []
            n = min(len(got), len(want))
            if n < MIN_PICKS_PER_POS:
                continue
            # Positive when the room's k-th pick lands EARLIER (lower pick no).
            per_draft.append(statistics.mean(want[i] - got[i] for i in range(n)))
        if len(per_draft) < min_drafts:
            continue
        raw[pos] = (statistics.median(per_draft),
                    statistics.pstdev(per_draft) if len(per_draft) > 1 else 0.0,
                    len(per_draft))

    # CENTRE the offsets. Both curves describe the same draft, so a tendency is
    # necessarily RELATIVE — a room cannot take every position earlier than the
    # market, and cannot take every one later either. A constant shift across
    # all six positions is therefore an artifact of how the two curves were
    # built (a duplicated ADP row, a market list of different depth), never a
    # habit. Subtracting the common component leaves only what actually
    # differs between positions, and makes the measure robust to that whole
    # class of error instead of reporting it as a finding.
    if not raw:
        return {}
    centre = statistics.median(v[0] for v in raw.values())
    for pos, (offset, spread, n) in raw.items():
        centred = offset - centre
        out[pos] = Tendency(pos, max(-MAX_OFFSET, min(MAX_OFFSET, centred)), n, spread)
    return out


def adjust_adp(adp: float, pos: str, tend: dict[str, Tendency]) -> float:
    """National ADP, corrected for what this room actually does.

    A room that takes quarterbacks eight picks early makes every quarterback's
    effective ADP eight picks earlier — which is what survival should be asked
    about. Never returns less than 1: a pick number below the first pick is not
    a thing.
    """
    t = tend.get((pos or "").upper())
    if not t:
        return adp
    # The same confidence bar the shelf uses for its sentences. This gate was
    # added to read_out first and deliberately NOT to the arithmetic, on the
    # reasoning that widen_sigma already folds spread into the curve. That was
    # half right and it cost a draft: widening flattens the curve, but the
    # SHIFT still moves its centre by the untrusted number. Defenses measured
    # -12.0 at a signal of 1.32 — a correction pinned to the cap, from three
    # drafts that disagreed by nearly its whole width — and the board duly
    # expected them to slide twelve picks. The best one available went at 142,
    # eleven picks before the seat that wanted it, and the roster finished
    # last in the league on defense.
    #
    # A number you would not say out loud is a number you should not draft on.
    if t.spread > 0 and abs(t.offset) / t.spread < MIN_SIGNAL:
        return adp
    return max(1.0, adp - t.offset)


def widen_sigma(sigma: float, pos: str, tend: dict[str, Tendency]) -> float:
    """A room that is INCONSISTENT about a position is less predictable there,
    whichever way it leans. Its own draft-to-draft spread is added in quadrature
    to the market's, so the survival curve flattens rather than shifting."""
    t = tend.get((pos or "").upper())
    if not t or t.spread <= 0:
        return sigma
    return (sigma ** 2 + t.spread ** 2) ** 0.5


def read_out(tend: dict[str, Tendency], min_offset: float = 3.0,
             min_signal: float = MIN_SIGNAL) -> list[str]:
    """What the room does, in words, for the shelf. Silent when the room drafts
    to the market — which is the honest answer most of the time — and silent
    about a position the room is not CONSISTENT about, however wide the gap.
    Sorting by size alone puts the least reliable number first whenever the
    room is erratic, which is the opposite of what the shelf is for."""
    lines = []
    for t in sorted(tend.values(), key=lambda t: -abs(t.offset)):
        if abs(t.offset) < min_offset:
            continue
        # spread == 0 is perfect agreement, not absent evidence: MIN_DRAFTS
        # guarantees at least two drafts stand behind every tendency here.
        if t.spread > 0 and abs(t.offset) / t.spread < min_signal:
            continue
        if t.offset > 0:
            lines.append(f"This room takes {t.pos}s about {t.offset:.0f} picks "
                         f"early — don't plan on waiting one out.")
        else:
            lines.append(f"{t.pos}s slide here, about {abs(t.offset):.0f} picks "
                         f"past the market. You can wait longer than the sheet says.")
    return lines[:3]
