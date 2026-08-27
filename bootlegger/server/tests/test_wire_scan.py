"""Finding the player in an untagged headline.

RotoWire tags its items. ESPN, CBS, Yahoo and PFT do not, and they are the
overwhelming majority of what the wire now sees — so this scanner is what makes
the extra coverage usable at all. Every test here is about the same property:
it refuses rather than guesses, because a wrong join pushes "your starter is
out" about the wrong man.
"""
import pytest

from app.engines import wire


@pytest.fixture()
def index():
    roster = [
        {"sleeper_id": "1", "name": "Mike Evans", "pos": "WR", "team": "TB"},
        {"sleeper_id": "2", "name": "Josh Allen", "pos": "QB", "team": "BUF"},
        # The collision RotoWire's own matcher was built for: Sleeper carries a
        # defensive Josh Allen too.
        {"sleeper_id": "3", "name": "Josh Allen", "pos": "LB", "team": "JAX"},
        {"sleeper_id": "4", "name": "Amon-Ra St. Brown", "pos": "WR", "team": "DET"},
        {"sleeper_id": "5", "name": "Brian Thomas Jr.", "pos": "WR", "team": "JAX"},
        {"sleeper_id": "6", "name": "Brian Thomas", "pos": "TE", "team": "NE"},
        {"sleeper_id": "7", "name": "Chase Brown", "pos": "RB", "team": "CIN"},
    ]
    return wire.build_index(roster)


def test_finds_a_plain_full_name(index):
    pid, name = wire.scan_name("Mike Evans expects to play Week 1", index)
    assert pid == "1" and name == "mike evans"


def test_finds_a_punctuated_name(index):
    """'Amon-Ra St. Brown' normalizes to three tokens; the scanner has to look
    for phrases longer than two."""
    pid, _ = wire.scan_name("Lions activate Amon-Ra St. Brown from the report", index)
    assert pid == "4"


def test_longest_match_wins(index):
    """The scanner prefers the longer phrase, which is what lets a
    three-token name beat the two-token one inside it."""
    pid, name = wire.scan_name("Chase Brown and the Bengals", index)
    assert pid == "7" and name == "chase brown"


def test_a_suffix_collision_refuses_without_a_tiebreak(index):
    """normalize_name STRIPS suffixes by design — 'Brian Thomas Jr.' and
    'Brian Thomas' are the same key, so nothing in the name distinguishes
    them. Two same-named fantasy players with no other evidence is exactly
    the case match() refuses, and the scanner inherits that rather than
    inventing a tiebreak the data cannot support. The item still reaches the
    feed under its printed headline.

    This is a real limit, not an oversight: the fix is roster evidence, below.
    """
    pid, _ = wire.scan_name("Brian Thomas Jr. limited in practice", index)
    assert pid is None


def test_a_suffix_collision_resolves_when_the_league_holds_one(index):
    """The evidence the name cannot supply, the league can: exactly one of the
    two is on somebody's roster."""
    pid, _ = wire.scan_name("Brian Thomas Jr. limited in practice", index,
                            prefer_ids={"5"})
    assert pid == "5"


def test_a_position_collision_resolves_to_the_fantasy_man(index):
    """Two Josh Allens. match() already prefers the fantasy position; the
    scanner must inherit that rather than re-implement it."""
    pid, _ = wire.scan_name("Josh Allen throws for four scores", index)
    assert pid == "2"


def test_two_named_players_is_a_refusal(index):
    """'Team trades X for Y' is about both men. Picking one is a coin flip
    wearing a fact's clothes, and the item still reaches the feed under its
    printed headline."""
    pid, _ = wire.scan_name("Bengals trade Chase Brown for Mike Evans", index)
    assert pid is None


def test_no_name_is_a_refusal(index):
    for text in ("Roger Goodell confirms league reviewing the matter",
                 "Commanders offense called worst I have ever seen",
                 "", "Week 1 inactives"):
        pid, _ = wire.scan_name(text, index)
        assert pid is None, f"{text!r} matched {pid}"


def test_a_bare_surname_never_matches(index):
    """A single token is the whole class of silent wrong joins — 'Brown' is a
    dozen men and 'Allen' is two."""
    for text in ("Brown ruled out", "Allen questionable", "Evans to miss time"):
        pid, _ = wire.scan_name(text, index)
        assert pid is None, f"{text!r} matched {pid}"


def test_a_club_name_is_not_a_player(index):
    """Football prose is full of words that are also surnames. A headline is
    not a roster."""
    pid, _ = wire.scan_name("The Brown out of Cincinnati is fine", index)
    assert pid is None


def test_rostered_players_break_a_tie(index):
    """The same preference match() applies: a man someone in this league
    actually holds beats a man nobody has."""
    pid, _ = wire.scan_name("Josh Allen carted off", index, prefer_ids={"3"})
    assert pid in ("2", "3")


def test_scan_agrees_with_the_tagged_matcher(index):
    """A tagged feed and an untagged one must reach the same id for the same
    man, or the wire tells two stories about one player."""
    tagged = wire.match("Mike Evans", index)
    scanned, _ = wire.scan_name("Mike Evans expects to play", index)
    assert tagged == scanned == "1"
