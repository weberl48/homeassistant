"""August is not September, and a regex that cannot fire is not a rule.

Two things live here.

The first is the seasonal downgrade. Before the season starts, a man "not
playing Friday" is missing an exhibition — the text alone cannot say so, and
the wire's first multi-source poll graded exactly that as OUT. With five feeds
running through August that is thirty times the false-alarm volume the single
feed produced.

The second is the enforcement this project has now needed three times: every
pattern in the module is exercised against a known positive. A rule that
matches nothing looks identical to a rule that found nothing, and both times it
happened here the cause was an escape mangled on the way into the file rather
than logic anybody got wrong.
"""
import re

import pytest

from app.engines import wire


# --- the seasonal downgrade -------------------------------------------------

@pytest.mark.parametrize("headline,in_season,want", [
    # Missing an exhibition is not a fantasy event.
    ('Sean Payton: Bo Nix "probably" won\'t play Friday', False, "info"),
    ("Ruled out for Sunday", False, "info"),
    ("Won't play in Friday's preseason game", False, "info"),
    # ...but the same words in September are exactly the alarm.
    ("Ruled out for Sunday", True, "out"),
    ("Won't play Sunday", True, "out"),
    # Things that matter whatever the month — August is when you most need to
    # hear these, not least.
    ("Placed on injured reserve", False, "out"),
    ("Undergoes surgery, out multiple weeks", False, "out"),
    ("Suspended six games", False, "out"),
    ("Carted off in practice", False, "out"),
    ("Torn ACL ends his season", False, "out"),
    ("Waived by the Panthers", False, "out"),
    # Below the alarm grades nothing is downgraded: a practice report in
    # August is still a practice report.
    ("Did not practice Wednesday", False, "practice"),
    ("Questionable for Sunday", False, "questionable"),
    ("Expected to start Sunday", False, "role"),
])
def test_seasonal_grading(headline, in_season, want):
    assert wire.severity(headline, "", in_season=in_season) == want


def test_in_season_is_the_default():
    """Every existing caller passes two arguments. The downgrade must be
    opt-in, or the whole wire goes quiet the moment this ships."""
    assert wire.severity("Ruled out for Sunday") == "out"


def test_preseason_clause_in_a_headline_is_discounted():
    """The discount used to apply only to the body. RotoWire's desk writes
    fantasy status in the headline; a newsroom writes football news there."""
    assert wire.severity("Won't play in Saturday's exhibition", "", in_season=True) == "info"


# --- no rule may be silently inert ------------------------------------------
# Each pattern with a string it MUST match. This is the check that would have
# caught a \b collapsing into a literal backspace byte — three times now.

_POSITIVES = {
    "_PRESEASON_MATERIAL": ["Placed on injured reserve", "Undergoes surgery",
                            "Suspended six games", "Carted off in practice",
                            "Waived by the Jets", "Torn ACL"],
    "DEPARTURE": ["Placed on injured reserve", "Torn ACL", "Released by the club",
                  "Suspended 6 games"],
    "_DISCOUNTED": ["won't play in Friday's preseason game",
                    "intends to be ready for Week 1", "joint practice"],
    "_READY_HEADLINE": ["Intends to be ready", "Expects to play", "Cleared",
                        "Avoids serious injury"],
    "_AILMENT": ["Chase Brown (knee) did not practice"],
}


@pytest.mark.parametrize("name,samples", sorted(_POSITIVES.items()))
def test_pattern_actually_fires(name, samples):
    pattern = getattr(wire, name)
    assert isinstance(pattern, re.Pattern), f"{name} is not a compiled pattern"
    for sample in samples:
        assert pattern.search(sample), f"{name} failed to match {sample!r}"


def test_no_control_characters_in_the_module():
    """The specific corruption, caught at the source rather than by symptom: a
    backspace byte inside a pattern makes it demand a character no headline
    contains, and everything downstream just quietly finds nothing."""
    import pathlib
    text = pathlib.Path(wire.__file__).read_text(encoding="utf-8")
    stray = {c for c in text if ord(c) < 32 and c not in "\n\t\r"}
    assert not stray, f"control characters in wire.py: {[hex(ord(c)) for c in stray]}"


def test_every_severity_grade_is_reachable():
    """A grade nothing can produce is dead vocabulary."""
    reachable = {
        wire.severity(h) for h in (
            "Ruled out for Sunday", "Doubtful for Sunday",
            "Questionable for Sunday", "Did not practice Wednesday",
            "Expected to start Sunday", "Signs a contract extension")
    }
    assert set(wire.SEVERITY_ORDER) <= reachable | {"info"}
    assert "out" in reachable and "practice" in reachable and "role" in reachable
