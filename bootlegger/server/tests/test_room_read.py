"""The shelf may only say out loud what the sample can pay for.

Measured on the live board the night before the 2026 draft, the room's six
tendencies came back:

    QB  +11.5  spread 2.6      RB  -0.3  spread 1.0
    WR   +4.3  spread 1.2      TE  +0.3  spread 3.7
    K    -2.9  spread 3.5      DEF -12.0 spread 9.1

read_out sorted by magnitude alone, so the FIRST line on the board was the
one the three past drafts agreed about least: DEF, pinned at the -12.0 cap
with a spread of 9.1 — the drafts disagreeing by nearly the width of the
effect. The sentence it produced ("you can wait longer than the sheet says")
counsels waiting, and the cost of being wrong about waiting is the player.

The arithmetic was never fooled: widen_sigma folds that same spread into the
survival curve, so DEF already got a flat curve rather than a confident
shift. Only the prose overclaimed. These tests pin the gap closed and pin the
math open — silencing a sentence must not silence a correction.
"""
from __future__ import annotations

from app.engines import room as room_engine
from app.engines.room import Tendency


def _live_six() -> dict[str, Tendency]:
    """The exact shape measured on the live board, 2026-08-30."""
    return {t.pos: t for t in [
        Tendency("QB", 11.5, 3, 2.6),
        Tendency("RB", -0.3, 3, 1.0),
        Tendency("WR", 4.3, 3, 1.2),
        Tendency("TE", 0.3, 3, 3.7),
        Tendency("K", -2.9, 3, 3.5),
        Tendency("DEF", -12.0, 3, 9.1),
    ]}


# ---------------------------------------------------------------------------
# The gate


def test_erratic_position_is_not_asserted():
    """DEF: -12.0 against a spread of 9.1 is a ratio of 1.3. Not sayable."""
    lines = room_engine.read_out(_live_six())
    assert not any("DEF" in ln for ln in lines)


def test_consistent_positions_survive():
    """QB (4.4) and WR (3.6) clear the bar on the same live sample."""
    lines = room_engine.read_out(_live_six())
    assert any("QBs" in ln for ln in lines)
    assert any("WRs" in ln for ln in lines)


def test_live_sample_yields_exactly_the_two_trustworthy_lines():
    lines = room_engine.read_out(_live_six())
    assert len(lines) == 2


def test_the_loudest_line_is_no_longer_the_least_reliable():
    """Before the gate, DEF sorted first because sorting was by size."""
    lines = room_engine.read_out(_live_six())
    assert lines[0].startswith("This room takes QBs")


def test_perfect_agreement_is_evidence_not_absence():
    """spread == 0 means the drafts agreed exactly; MIN_DRAFTS guarantees
    at least two stand behind it, so it must not be read as unmeasured."""
    tend = {"QB": Tendency("QB", 9.0, 3, 0.0)}
    assert room_engine.read_out(tend)


def test_small_offset_still_silent_however_tight():
    """The size bar and the confidence bar are independent; a 1-pick habit
    is not worth a sentence even measured perfectly."""
    tend = {"RB": Tendency("RB", 1.0, 3, 0.01)}
    assert room_engine.read_out(tend) == []


def test_gate_is_a_ratio_not_a_ceiling():
    """A wide spread is sayable if the effect is wider still — the claim is
    about signal against noise, not about noise alone."""
    loud = {"QB": Tendency("QB", 24.0, 3, 9.1)}
    quiet = {"QB": Tendency("QB", 11.0, 3, 9.1)}
    assert room_engine.read_out(loud)
    assert room_engine.read_out(quiet) == []


def test_threshold_is_tunable_by_caller():
    tend = _live_six()
    assert any("DEF" in ln for ln in room_engine.read_out(tend, min_signal=0))


def test_still_capped_at_three_lines():
    tend = {p: Tendency(p, 20.0, 3, 1.0)
            for p in ("QB", "RB", "WR", "TE", "K", "DEF")}
    assert len(room_engine.read_out(tend)) == 3


# ---------------------------------------------------------------------------
# The gate is on the arithmetic too — corrected 2026-08-31
#
# This section originally pinned the opposite rule: "suppressing a sentence
# must never suppress the number behind it", on the reasoning that widen_sigma
# already handled an inconsistent room. Draft night measured that reasoning
# and it was wrong. Widening flattens the curve; the shift still moves its
# CENTRE by the untrusted offset. Defenses got the full -12.0 at a signal of
# 1.32, the board expected them to slide twelve picks, the best available went
# eleven picks before the seat that wanted it, and the roster finished last in
# the league on defense — about 24.6 points, the largest measurable engine
# cost of the draft. The tests below now pin the corrected rule.


def test_erratic_position_does_not_shift_adp():
    """DEF: offset -12.0 against spread 9.1. Below the bar, so no shift."""
    tend = _live_six()
    assert room_engine.adjust_adp(100.0, "DEF", tend) == 100.0


def test_consistent_position_still_shifts():
    """QB: 11.5 against 2.6 clears the bar comfortably, so the correction
    stands. Gating everything would throw away the room read entirely."""
    tend = _live_six()
    assert room_engine.adjust_adp(100.0, "QB", tend) == 88.5


def test_the_shelf_and_the_math_now_agree():
    """The rule in one line: a number you would not say out loud is a number
    you should not draft on. Whatever read_out names, adjust_adp shifts."""
    tend = _live_six()
    spoken = {t.pos for t in tend.values()
              if any(t.pos + "s" in ln for ln in room_engine.read_out(tend))}
    for pos, t in tend.items():
        shifted = room_engine.adjust_adp(100.0, pos, tend) != 100.0
        if shifted:
            assert pos in spoken, f"{pos} shifts the board but is never said aloud"


def test_erratic_position_still_widens_its_curve():
    """Widening was always the honest treatment of noise and keeps its job:
    the room is unpredictable at DEF, so the curve stays flat there even
    though the centre no longer moves."""
    tend = _live_six()
    assert room_engine.widen_sigma(6.0, "DEF", tend) > 10.0


def test_a_perfectly_agreed_offset_still_shifts():
    """spread == 0 is agreement, not absent evidence — the ratio is infinite
    and must not be read as a divide-by-zero that gates the shift away."""
    tend = {"QB": room_engine.Tendency("QB", 9.0, 3, 0.0)}
    assert room_engine.adjust_adp(100.0, "QB", tend) == 91.0
