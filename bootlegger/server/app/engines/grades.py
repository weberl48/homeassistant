"""Draft report card — the league-curve grading math.

Industry graders (FantasyPros draft analyzer, Draft Sharks instant grade,
RotoWire's write-ups) all blend the same ingredients: projected points of the
drafted roster with the starting lineup weighted heaviest, market value
captured against ADP, and roster balance/depth. The standing criticism is
that grades are injury-blind preseason snapshots — we answer what we can by
folding capital-weighted injury risk into the composite when the sharks'
data is on the wire.

Grading is ON THE LEAGUE'S CURVE: every metric becomes a z-score across the
room's seats, so an A means "beat this room", not an arbitrary scale.
"""
from __future__ import annotations

from statistics import mean, pstdev
from typing import Any

# Composite weights; risk is dropped and the rest renormalized when no
# injury data is present (Draft Sharks down or pre-integration draft).
WEIGHTS = {"starters": 0.45, "vbd": 0.20, "surplus": 0.15, "depth": 0.10, "risk": 0.10}

# Letter cuts on the composite z. Roughly: A-range = top ~sixth of the room,
# B straddles the middle, C-range = bottom ~sixth, D = a clear outlier. In a
# 12-seat room that hands out a couple of As and a couple of Cs most drafts.
_CURVE = [(1.30, "A+"), (0.80, "A"), (0.40, "A-"), (0.15, "B+"), (-0.15, "B"),
          (-0.40, "B-"), (-0.80, "C+"), (-1.10, "C"), (-1.40, "C-")]


def letter(z: float) -> str:
    for cut, mark in _CURVE:
        if z >= cut:
            return mark
    return "D"


def _zscores(vals: list[float]) -> list[float]:
    m = mean(vals)
    sd = pstdev(vals)
    if sd < 1e-9:
        return [0.0 for _ in vals]
    return [(v - m) / sd for v in vals]


def compose(teams: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """teams: [{starters, vbd, surplus, depth, risk|None, ...}] -> adds
    z_* per metric, composite, grade, component letters, and rank."""
    has_risk = any(t.get("risk") is not None for t in teams)
    weights = dict(WEIGHTS)
    if not has_risk:
        del weights["risk"]
        total = sum(weights.values())
        weights = {k: v / total for k, v in weights.items()}

    cols = {}
    for k in weights:
        vals = [t.get(k) for t in teams]
        if k == "risk":
            # A seat the injury data doesn't cover must grade AVERAGE, not
            # sturdiest — treating missing as zero risk would reward drafting
            # players the sharks don't track. Impute the league mean.
            present = [float(v) for v in vals if v is not None]
            fill = mean(present)
            vals = [float(v) if v is not None else fill for v in vals]
        else:
            vals = [float(v or 0.0) for v in vals]
        cols[k] = _zscores(vals)
    if "risk" in cols:                       # lower risk is better — invert
        cols["risk"] = [-z for z in cols["risk"]]

    for i, t in enumerate(teams):
        comp = sum(w * cols[k][i] for k, w in weights.items())
        t["composite"] = round(comp, 3)
        t["grade"] = letter(comp)
        t["components"] = {k: {"z": round(cols[k][i], 2), "grade": letter(cols[k][i])}
                           for k in weights}
    teams.sort(key=lambda t: -t["composite"])
    for rank, t in enumerate(teams, 1):
        t["rank"] = rank
    return teams


def lines_strong_of(k: str) -> str:
    return {
        "starters": "the projections love this starting nine",
        "vbd": "stacked value over the last starters",
        "surplus": "shopped the discounts all night",
        "depth": "the deepest shelf in the room",
        "risk": "the sturdiest roster on the books",
    }[k]


def lines_weak_of(k: str) -> str:
    return {
        "starters": "the starting nine is light",
        "vbd": "thin on real value",
        "surplus": "paid retail all night",
        "depth": "one injury from trouble",
        "risk": "carrying the room's biggest injury bets",
    }[k]


def seat_note(t: dict[str, Any]) -> str:
    """One line in the room's voice, driven by the strongest signals.
    |z| >= 0.8 (about the top/bottom sixth of the room) is what earns a
    mention — anything milder reads as noise, not a story."""
    c = t["components"]
    strong = max(c, key=lambda k: c[k]["z"])
    weak = min(c, key=lambda k: c[k]["z"])
    if c[strong]["z"] >= 0.8 and c[weak]["z"] <= -0.8:
        return f"{lines_strong_of(strong).capitalize()}, but {lines_weak_of(weak)}."
    if c[strong]["z"] >= 0.8:
        return f"{lines_strong_of(strong).capitalize()}."
    if c[weak]["z"] <= -0.8:
        return f"{lines_weak_of(weak).capitalize()}."
    return "A straight pour — nothing flashy, nothing broken."


# The figure that makes each line true, for the seats that would otherwise
# repeat one. Naming the number is always honest and never guesses.
_EVIDENCE = {
    "starters": lambda t: f"{float(t.get('starters') or 0):.0f} projected",
    "vbd": lambda t: f"{float(t.get('vbd') or 0):.0f} over the last starters",
    "surplus": lambda t: f"{float(t.get('surplus') or 0):+.0f} picks of market value",
    "depth": lambda t: f"{int(t.get('depth') or 0)} startable spare"
                       f"{'' if int(t.get('depth') or 0) == 1 else 's'}",
    "risk": lambda t: f"injury risk {float(t.get('risk') or 0):.0f}",
}


def _candidate_lines(t: dict[str, Any]) -> list[tuple[str, str]]:
    """(component, sentence) for everything this seat could honestly say,
    most distinctive signal first."""
    c = t["components"]
    out: list[tuple[str, str]] = []
    for k in sorted(c, key=lambda k: -abs(c[k]["z"])):
        z = c[k]["z"]
        if z >= 0.8:
            out.append((k, f"{lines_strong_of(k).capitalize()}."))
        elif z <= -0.8:
            out.append((k, f"{lines_weak_of(k).capitalize()}."))
    return out


def seat_notes(teams: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give every seat a read that is ITS OWN.

    `seat_note` draws on a five-phrase vocabulary, so in a twelve-seat room
    the same sentence lands on several seats. The report card shipped with the
    top two rows both reading "The deepest shelf in the room.", three seats
    sharing "A straight pour", and three more sharing one "shopped the
    discounts" variant — which reads as a ranking that cannot tell its own
    rows apart, against this house's third product principle (a list of eight
    suggestions must be eight different suggestions).

    Resolution order, cheapest honest move first: the seat's default line; then
    its own next-most-distinctive signal that nobody upstream has claimed; then
    the line it wanted with the figure that makes it true appended. Nothing
    here invents a claim — every fallback is a number already on the seat.
    """
    used: set[str] = set()
    for t in sorted(teams, key=lambda t: -(t.get("composite") or 0.0)):
        note = seat_note(t)
        if note in used:
            note = next((s for _, s in _candidate_lines(t) if s not in used), note)
        if note in used:
            comp = t["components"]
            k = max(comp, key=lambda k: abs(comp[k]["z"]))
            note = f"{note[:-1]} — {_EVIDENCE[k](t)}."
        used.add(note)
        t["note"] = note
    return teams
