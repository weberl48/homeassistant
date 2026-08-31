"""The wire: turning a news feed into decisions.

Two jobs live here, and both are about not crying wolf.

**Severity.** RotoWire's headlines are written to a house style, which is what
makes them classifiable without a language model: "Ruled out for Sunday",
"Won't practice Wednesday", "Placed on injured reserve", "Expected to start".
The classifier maps a headline to one of six grades, and the grade — not the
fact that news arrived — decides whether a phone buzzes through Do Not Disturb.
An `info` item about a man on your bench is a line on a feed, not an alarm.

**Matching.** The feed names players; the league speaks in Sleeper ids. Names
collide (Sleeper carries two Josh Allens), so a match that cannot be made
confidently stores NULL rather than guessing — the item still reaches the
feed under its printed name. A wrong join here would push "your starter is
out" about a linebacker, which costs more trust than a missing chip.

Nothing in this module writes; `ingest.etl_news` owns persistence.
"""
from __future__ import annotations

import re
from typing import Any

from ..sources import normalize_name

# Severity grades, most urgent first. The order IS the comparison — callers
# rank with SEVERITY_RANK rather than hardcoding string sets.
SEVERITY_ORDER = ("out", "doubtful", "questionable", "practice", "role", "info")
SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITY_ORDER)}

# Grades that mean "this changes a lineup you have already set". These wake a
# phone; nothing else does.
ALARM = {"out", "doubtful"}
# Grades worth a normal-channel notification when the man is yours.
NOTIFY = {"out", "doubtful", "questionable", "practice", "role"}
# Season-ending / long-term departures: these also open a waiver window on the
# man behind them.
DEPARTURE = re.compile(
    r"\b(injured reserve|placed on ir\b|season-ending|out for the (season|year)"
    # "suspended \d" wanted a game count, so "suspended indefinitely" — the
    # worse news — opened no waiver window at all. Stem it and drop the digit.
    r"|torn (acl|achilles|pcl)|suspen(ded|sion)|waived|released|cut by|traded to"
    # A man on the exempt list is off the field for an unknown number of weeks
    # while still on his team's roster: nothing in the injury vocabulary
    # describes it, and until 2026-08-30 nothing in this file did either.
    r"|commissioner['’]?s? exempt|exempt list|paid leave|administrative leave"
    r"|physically unable|reserve/pup|non-football injury)\b", re.I)

# Ordered because the first hit wins: "ruled out" must beat the bare word
# "questionable" when a headline carries both ("Questionable tag lifted —
# ruled out").
_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("out", re.compile(
        r"\b(ruled out|won'?t play|will not play|out (for|indefinitely|multiple)"
        r"|inactive (for|sunday|monday|thursday)|injured reserve|placed on ir\b"
        r"|season-ending|out for the (season|year)|carted off|suspen(ded|sion)"
        # The 2026 draft: the wire carried nine headlines putting a first-round
        # back on the Commissioner's Exempt List and every one graded INFO,
        # because no table in this file had ever heard of it. The board went on
        # recommending him at three times the runner-up. A man on the exempt
        # list is not playing; that is the whole of what a grade has to know.
        r"|commissioner['’]?s? exempt|exempt list|paid leave|administrative leave"
        r"|physically unable|reserve/pup|non-football injury|undergo(es|ing)? surgery"
        # A torn ligament is the most severe thing this feed ever carries and it
        # graded INFO: DEPARTURE knew about it, the grader did not, so "Torn ACL
        # ends his season" reached the board as a footnote. The word "torn" is
        # doing all the work — a desk that writes it is not hedging.
        r"|torn (acl|achilles|pcl|mcl|patellar)|tears? (his |an |the )?(acl|achilles|pcl)"
        r"|ruptured achilles|out for the year"
        r"|to miss|sidelined|waived|released|cut by)\b", re.I)),
    ("doubtful", re.compile(r"\bdoubtful\b", re.I)),
    # A charge is not an absence. Men play through arrests and charges every
    # season, and the league office — not the police blotter — decides whether
    # anyone misses a snap. So legal trouble raises a flag without claiming he
    # is out; the exempt-list and suspension language above, which DOES mean he
    # is out, is matched earlier and wins on order. Anchored to whole phrases:
    # a bare "charged" grades a crowd charged up by a touchdown.
    ("questionable", re.compile(
        r"\b(questionable|game-time decision|true toss-?up"
        r"|arrested|charged with|facing (felony|misdemeanor|domestic)"
        r"|under investigation|pleads? (guilty|not guilty))\b", re.I)),
    ("practice", re.compile(
        r"\b(did not practice|won'?t practice|not practicing|absent (for|from)"
        r"|limited (in|at|participant|practice)|dnp\b|misses practice"
        r"|held out of practice|full (participant|practice)|returns to practice"
        r"|practicing|participat(es|ing) in)\b", re.I)),
    ("role", re.compile(
        r"\b(expected to start|will start|named (the )?starter|starting|promoted"
        r"|takes over|leads? the backfield|first-team|top of the depth chart"
        r"|signs?\b|activated|returns? (sunday|monday|thursday|to action)"
        r"|cleared|elevated|claimed)\b", re.I)),
]

# The body part, when the feed puts it in the usual parenthetical: "Chase
# (knee) is not participating…". Display only — it never changes a grade.
_AILMENT = re.compile(r"^[A-Z][\w'.-]+(?:\s+[A-Z][\w'.-]+)?\s*\(([a-z/ ]{3,24})\)")


# Clauses the body may NOT escalate on. Two kinds, both seen live:
#
#   preseason — "won't play in Friday's preseason game" is not a fantasy status.
#     Caught 2026-08-26: a RotoWire item headlined "Intends to be ready Week 1"
#     graded OUT because the body mentioned sitting a preseason game. Good news
#     read as an injury.
#   readiness — "he believes he'll be available for Week 1". The desk puts the
#     decision in the headline; a hopeful clause underneath it is context, not a
#     ruling.
#
# Clauses are split on the connectives RotoWire actually writes with, so a
# discounted half of a sentence cannot poison the other half.
_CLAUSE_SPLIT = re.compile(r",\s+(?:but|though|although)\s+|;\s+|(?<=[.!?])\s+", re.I)
_DISCOUNTED = re.compile(
    r"\b(pre-?season|exhibition|joint practice"
    r"|intends? to (be ready|play)|expects? to (be ready|play)|believes? he'?ll"
    r"|should be (ready|available)|on track (for|to)|hopes? to (be ready|play))\b",
    re.I)
# A headline that leads with readiness is the desk saying the news is good. The
# body cannot outvote it.
_READY_HEADLINE = re.compile(
    r"\b(intends? to be ready|expects? to (be ready|play|return)|should be (ready|available)"
    r"|on track|targeting|aiming for|cleared|good to go|no (structural )?damage"
    r"|appears? minor|avoids? (serious|major))\b", re.I)


def escalatable(body: str) -> str:
    """The part of the body that may raise a grade — preseason and
    forward-looking clauses removed."""
    keep = [c for c in _CLAUSE_SPLIT.split(body or "") if c and not _DISCOUNTED.search(c)]
    return " ".join(keep)


# The body may only ESCALATE, never assign a soft grade. RotoWire's desk puts
# the decision in the headline and the reporting underneath it, so body text
# routinely contains practice and role words about things that did not happen
# ("believes he will resume practicing soon" — a reassuring note, not a
# practice report). Reading those out of the body invented status changes.
_BODY_GRADES = {"out", "doubtful", "questionable"}


# What still matters in August. Before the season starts, a man "not playing
# Friday" is missing an exhibition and is not a fantasy event — but a man on
# injured reserve, suspended, or under the knife absolutely is, and August is
# exactly when you most need to hear it. So the out-of-season downgrade applies
# to mere game-absence and never to these.
_PRESEASON_MATERIAL = re.compile(
    # Stems, not whole words: "suspend\b" never matches "suspended", which is
    # the only form anyone writes. Verified against known positives by
    # tests/test_wire_seasonal.py — a pattern that cannot fire is
    # indistinguishable from one that found nothing.
    r"\b(injured reserve|placed on ir|season-ending|out for the (season|year)"
    r"|torn (acl|achilles|pcl)|suspend\w*|waiv\w*|releas\w*|cut by"
    # August is exactly when a legal matter lands, and the downgrade to INFO
    # here is what buried the exempt-list story on draft day: graded `out` by
    # _RULES, then handed straight back to INFO by _seasonal because this
    # table had never heard of it either. All three tables or none.
    r"|commissioner['’]?s? exempt|exempt list|paid leave|administrative leave"
    r"|physically unable|reserve/pup|non-football injury|surger\w*"
    r"|carted off|to miss \d|multiple (weeks|months)"
    r"|out (indefinitely|multiple))", re.I)


def preseason_material(headline: str, body: str = "") -> bool:
    """True when an out-of-season item is still a fantasy event."""
    return bool(_PRESEASON_MATERIAL.search(f"{headline} {body}"))


def severity(headline: str, body: str = "", in_season: bool = True) -> str:
    """The grade for one wire item.

    The preseason discount applies to the HEADLINE too, which it did not until
    the wire went multi-source. RotoWire's desk writes fantasy status; the
    newsrooms now on the wire write football news, and in August that is full
    of "won't play Friday" about exhibition games. Two live items on the first
    multi-source poll — Bo Nix "probably won't play Friday" and a preseason
    finale note — graded OUT, which at five sources is thirty times the
    false-alarm volume the single feed produced.

    A headline that is ENTIRELY a preseason clause has nothing left to grade
    and reads as info; one that carries a real ruling alongside a preseason
    mention keeps the ruling.
    """
    scannable_head = escalatable(headline or "") or ""
    for grade, pattern in _RULES:
        if pattern.search(scannable_head):
            return _seasonal(grade, headline, body, in_season)
    # A headline about being ready is the desk's verdict; nothing under it
    # outranks that.
    if _READY_HEADLINE.search(headline or ""):
        return "info"
    scannable = escalatable(body)
    grade = "info"
    for g, pattern in _RULES:
        if g in _BODY_GRADES and pattern.search(scannable):
            grade = g
            break
    return _seasonal(grade, headline, body, in_season)


def _seasonal(grade: str, headline: str, body: str, in_season: bool) -> str:
    """Out of season, missing a game is missing an exhibition.

    "Won't play Friday" is only preseason news because it is August — the text
    alone cannot say so, and the first multi-source poll graded exactly that as
    OUT. The calendar is context the board already has, so it supplies it
    rather than asking a regex to infer it. An IR placement or a surgery keeps
    its grade whatever the month.
    """
    if in_season or grade not in ALARM:
        return grade
    return grade if preseason_material(headline, body) else "info"


def ailment(body: str) -> str | None:
    m = _AILMENT.match((body or "").strip())
    return m.group(1).strip() if m else None


def is_departure(headline: str, body: str = "") -> bool:
    """Long-term absence — the kind that opens a job behind him."""
    return bool(DEPARTURE.search(f"{headline} {body}"))


def worse_than(a: str, b: str) -> bool:
    """True when grade `a` is more urgent than grade `b`."""
    return SEVERITY_RANK.get(a, 99) < SEVERITY_RANK.get(b, 99)


# ---------------------------------------------------------------------------
# Matching the feed's names onto Sleeper ids
# ---------------------------------------------------------------------------
# Fantasy-relevant positions. A name that collides across positions resolves to
# the fantasy one — the wire is read by a fantasy manager, and Sleeper's player
# table carries every linebacker in the league.
_FANTASY_POS = ("QB", "RB", "WR", "TE", "K", "DEF")


def build_index(players: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """{normalized name: [player rows]} — built once per ETL pass, not per item."""
    idx: dict[str, list[dict[str, Any]]] = {}
    for p in players:
        idx.setdefault(normalize_name(p["name"]), []).append(p)
    return idx


def match(name: str, index: dict[str, list[dict[str, Any]]],
          prefer_ids: set[str] | None = None) -> str | None:
    """The Sleeper id for a wire item's player, or None when the join cannot be
    made confidently.

    Tie-breaks, in order: a fantasy position beats a defensive one; a man
    someone in this league actually rosters beats a man nobody has; anything
    still tied is refused. `prefer_ids` is the league's full rostered set.
    """
    rows = index.get(normalize_name(name)) or []
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]["sleeper_id"]
    fantasy = [r for r in rows if (r.get("pos") or "").upper() in _FANTASY_POS]
    if len(fantasy) == 1:
        return fantasy[0]["sleeper_id"]
    pool = fantasy or rows
    if prefer_ids:
        rostered = [r for r in pool if r["sleeper_id"] in prefer_ids]
        if len(rostered) == 1:
            return rostered[0]["sleeper_id"]
        pool = rostered or pool
    return pool[0]["sleeper_id"] if len(pool) == 1 else None


# ---------------------------------------------------------------------------
# Finding the player in an untagged headline
# ---------------------------------------------------------------------------
# RotoWire tags every item with the player it is about. Nobody else does: ESPN,
# CBS, Yahoo and PFT write "Mike Evans expects to play Week 1" and leave the
# join to the reader. Those four are 141 of the 146 items a poll now sees, so
# without this scanner the extra coverage would be unusable — a feed of stories
# nobody's roster can be matched against.
#
# The rule is the same one `match()` already follows: refuse rather than guess.
# A wrong join here pushes "your starter is out" about the wrong man, which
# costs more trust than a missing chip.

# Full names only. A single token would match "Josh" against two Josh Allens
# and "Brown" against a dozen men — and the failure would be silent, because a
# plausible wrong match looks exactly like a right one.
_MIN_NAME_TOKENS = 2
# Longest normalized player name worth scanning for, in tokens ("amonra st
# brown" is three; a fourth covers the long ones without scanning sentences).
_MAX_NAME_TOKENS = 4

# Words that are also surnames. A headline is not a roster, and these appear in
# football prose constantly — "Bills Rally", "Chiefs Land", "Giants Sign".
_STOP_STARTS = frozenset({
    "the", "a", "an", "his", "her", "their", "this", "that", "one", "two",
    "no", "new", "report", "sources", "week", "sunday", "monday", "thursday",
})


def _tokens(text: str) -> list[str]:
    return [t for t in normalize_name(text).split(" ") if t]


def scan_name(text: str, index: dict[str, list[dict[str, Any]]],
              prefer_ids: set[str] | None = None) -> tuple[str | None, str | None]:
    """The one player an untagged headline is about, or (None, None).

    Returns (sleeper_id, matched_name). Longest match wins, because "Brian
    Thomas Jr." must not resolve as "Brian Thomas" if both exist. A headline
    naming TWO rostered players is refused outright: "Team trades X for Y" is
    about both, and picking one is a coin flip dressed as a fact.
    """
    toks = _tokens(text)
    if len(toks) < _MIN_NAME_TOKENS:
        return None, None
    hits: dict[str, str] = {}
    n = len(toks)
    for size in range(_MAX_NAME_TOKENS, _MIN_NAME_TOKENS - 1, -1):
        for i in range(n - size + 1):
            if toks[i] in _STOP_STARTS:
                continue
            phrase = " ".join(toks[i:i + size])
            rows = index.get(phrase)
            if not rows:
                continue
            pid = match(phrase, index, prefer_ids)
            if pid and pid not in hits:
                # A longer match subsumes a shorter one that overlaps it.
                if any(phrase in seen or seen in phrase for seen in hits.values()):
                    continue
                hits[pid] = phrase
    if len(hits) != 1:
        return None, None
    pid, phrase = next(iter(hits.items()))
    return pid, phrase


# ---------------------------------------------------------------------------
# Gap detection
# ---------------------------------------------------------------------------
# RotoWire serves exactly five items whatever you ask it for, and stamps each
# with a monotonic id. So the feed can prove it dropped news on the floor: if
# the oldest item in this poll is more than one id past the newest item of the
# last poll, the items in between were published and never seen. Silence about
# that would be exactly the failure this house doesn't allow.

def gap_since(last_seq: int | None, seqs: list[int]) -> int:
    """How many wire items were published and missed between polls. 0 when the
    polls overlap (or on the first poll, which has no baseline to miss from)."""
    if last_seq is None or not seqs:
        return 0
    oldest = min(seqs)
    return max(0, oldest - last_seq - 1)


def poll_interval_seconds(hours_to_kickoff: float | None, in_season: bool) -> float:
    """How hard to lean on the feed right now.

    The wire only holds five items, so cadence is coverage: inside the hours
    before kickoff a missed item is a lost week, and overnight in August it is
    a headline you can read at breakfast.
    """
    if not in_season:
        return 1800.0                      # offseason / pre-draft: twice an hour
    if hours_to_kickoff is None:
        return 900.0                       # in season, no game in view
    if -3.0 <= hours_to_kickoff <= 4.0:
        return 120.0                       # inactives window and live games
    if hours_to_kickoff <= 30.0:
        return 300.0                       # game day minus one
    return 900.0
