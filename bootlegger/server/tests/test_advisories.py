"""The advisory layer: readings that never bid on a pick.

Everything here is read-only by design. The bye and same-team findings do not
touch the suggestion score (a bye penalty inside the score would trade real
season points for cosmetic tidiness — one bad week is streamable), and
position pressure does not touch survival until it has been calibrated on a
real room. These tests pin that boundary as much as the arithmetic.
"""
from app.config import DEMO_ROSTER_POSITIONS
from app.engines import advisories


def _slots(rp=None):
    return advisories.starting_slots(rp or DEMO_ROSTER_POSITIONS)


# --------------------------------------------------------------- bye findings

def test_bye_that_empties_a_starter_slot_is_flagged():
    """Two RB slots, three RBs, two of them out in week 9 -> one RB left for
    two slots. That is the week the shelf actually breaks."""
    roster = [
        {"pos": "RB", "name": "Jeanty", "team": "LV", "bye": 9},
        {"pos": "RB", "name": "Henry", "team": "BAL", "bye": 9},
        {"pos": "RB", "name": "Hall", "team": "NYJ", "bye": 4},
    ]
    out = advisories.shelf_findings(roster, _slots())
    byes = [f for f in out["findings"] if f.kind == "bye"]
    assert len(byes) == 1
    assert byes[0].level == "warn"
    assert "9" in byes[0].label
    assert "RB" in byes[0].detail


def test_bye_with_enough_cover_stays_quiet():
    """Three RBs, one out: two remain for two slots. Nothing to say."""
    roster = [
        {"pos": "RB", "name": "Jeanty", "team": "LV", "bye": 9},
        {"pos": "RB", "name": "Henry", "team": "BAL", "bye": 4},
        {"pos": "RB", "name": "Hall", "team": "NYJ", "bye": 12},
    ]
    out = advisories.shelf_findings(roster, _slots())
    assert [f for f in out["findings"] if f.kind == "bye"] == []


def test_a_crowded_bye_week_is_noted_even_when_no_slot_breaks():
    """Three men out in one week is worth knowing before it arrives, even if
    every starter slot can still be filled."""
    # Every position carries one man more than it starts, so a single bye
    # never empties a slot — week 7 is crowded, not broken.
    roster = [
        {"pos": "RB", "name": "Jeanty", "team": "LV", "bye": 7},
        {"pos": "WR", "name": "Worthy", "team": "KC", "bye": 7},
        {"pos": "TE", "name": "Bowers", "team": "LV", "bye": 7},
        {"pos": "RB", "name": "Henry", "team": "BAL", "bye": 4},
        {"pos": "RB", "name": "Hall", "team": "NYJ", "bye": 12},
        {"pos": "WR", "name": "Nabers", "team": "NYG", "bye": 11},
        {"pos": "WR", "name": "Chase", "team": "CIN", "bye": 5},
        {"pos": "TE", "name": "Kittle", "team": "SF", "bye": 9},
    ]
    out = advisories.shelf_findings(roster, _slots())
    byes = [f for f in out["findings"] if f.kind == "bye"]
    assert len(byes) == 1
    assert byes[0].level == "info"
    assert "7" in byes[0].label


def test_one_week_never_produces_two_findings():
    """A week that both empties a slot and is crowded says it once."""
    roster = [
        {"pos": "RB", "name": "Jeanty", "team": "LV", "bye": 7},
        {"pos": "RB", "name": "Henry", "team": "BAL", "bye": 7},
        {"pos": "WR", "name": "Worthy", "team": "KC", "bye": 7},
        {"pos": "TE", "name": "Bowers", "team": "LV", "bye": 7},
    ]
    out = advisories.shelf_findings(roster, _slots())
    weeks = [f.label for f in out["findings"] if f.kind == "bye"]
    assert len(weeks) == len(set(weeks)) == 1


def test_unknown_byes_are_declared_never_swallowed():
    """Sleeper ships no byes at all; they are backfilled from nfl_games. If
    that backfill has not run, the panel must say so rather than read clean —
    a silent all-clear is the one failure this house does not allow."""
    roster = [
        {"pos": "RB", "name": "Jeanty", "team": "LV", "bye": None},
        {"pos": "RB", "name": "Henry", "team": "BAL", "bye": 9},
    ]
    out = advisories.shelf_findings(roster, _slots())
    assert out["byes_known"] is False


def test_byes_known_is_true_when_every_man_has_one():
    roster = [{"pos": "RB", "name": "Henry", "team": "BAL", "bye": 9}]
    assert advisories.shelf_findings(roster, _slots())["byes_known"] is True


def test_empty_shelf_is_quiet_and_knows_nothing_is_missing():
    out = advisories.shelf_findings([], _slots())
    assert out["findings"] == []
    assert out["byes_known"] is True


# -------------------------------------------------------------- team findings

def test_same_backfield_is_flagged():
    roster = [
        {"pos": "RB", "name": "Irving", "team": "TB", "bye": 9},
        {"pos": "RB", "name": "Tucker", "team": "TB", "bye": 9},
    ]
    out = advisories.shelf_findings(roster, _slots())
    backfield = [f for f in out["findings"] if f.kind == "backfield"]
    assert len(backfield) == 1
    assert backfield[0].level == "warn"
    assert "TB" in backfield[0].label


def test_quarterback_with_his_own_receiver_reads_as_a_stack():
    """Correlation you chose on purpose — noted, never scolded."""
    roster = [
        {"pos": "QB", "name": "Allen", "team": "BUF", "bye": 7},
        {"pos": "WR", "name": "Coleman", "team": "BUF", "bye": 7},
    ]
    out = advisories.shelf_findings(roster, _slots())
    stacks = [f for f in out["findings"] if f.kind == "stack"]
    assert len(stacks) == 1
    assert stacks[0].level == "info"


def test_three_from_one_team_is_flagged():
    roster = [
        {"pos": "QB", "name": "Allen", "team": "BUF", "bye": 7},
        {"pos": "WR", "name": "Coleman", "team": "BUF", "bye": 7},
        {"pos": "TE", "name": "Kincaid", "team": "BUF", "bye": 7},
    ]
    out = advisories.shelf_findings(roster, _slots())
    assert [f for f in out["findings"] if f.kind == "team"]


def test_kicker_and_defense_are_not_team_exposure():
    """Rostering the BUF kicker and the BUF defense alongside one Bill is not
    a correlated bet worth a warning."""
    roster = [
        {"pos": "WR", "name": "Coleman", "team": "BUF", "bye": 7},
        {"pos": "K", "name": "Bass", "team": "BUF", "bye": 7},
        {"pos": "DEF", "name": "Bills D/ST", "team": "BUF", "bye": 7},
    ]
    out = advisories.shelf_findings(roster, _slots())
    assert [f for f in out["findings"] if f.kind in ("team", "stack", "backfield")] == []


def test_players_with_no_team_are_skipped():
    roster = [
        {"pos": "RB", "name": "Ghost", "team": None, "bye": 9},
        {"pos": "RB", "name": "Phantom", "team": None, "bye": 9},
    ]
    out = advisories.shelf_findings(roster, _slots())
    assert [f for f in out["findings"] if f.kind == "backfield"] == []


# ---------------------------------------------------------- position pressure

def test_pressure_is_silent_when_the_room_drafts_to_adp():
    """The whole point of the residual: a room following the market produces
    no signal. A raw count of positions cannot do this — on a draft generated
    by twelve independent ADP-followers, '4+ of the last 10' fired in 95.9%
    of windows."""
    out = advisories.position_pressure({"WR": 4, "RB": 3}, {"WR": 4.0, "RB": 3.0})
    assert out == []


def test_a_run_reads_as_positive_residual():
    out = advisories.position_pressure({"WR": 6, "RB": 1}, {"WR": 2.0, "RB": 3.0})
    wr = [p for p in out if p["pos"] == "WR"]
    assert len(wr) == 1
    assert wr[0]["direction"] == "run"
    assert wr[0]["residual"] == 4.0


def test_a_slide_reads_as_negative_residual():
    out = advisories.position_pressure({"RB": 0}, {"RB": 4.0})
    assert out[0]["direction"] == "slide"
    assert out[0]["residual"] == -4.0


def test_noise_below_the_threshold_stays_quiet():
    """Measured sd of the residual on a no-run draft was 1.02, so a 2-pick
    deviation is ordinary weather, not a run."""
    out = advisories.position_pressure({"WR": 5}, {"WR": 3.0})
    assert out == []


def test_the_loudest_position_is_reported_first():
    out = advisories.position_pressure(
        {"WR": 7, "RB": 0, "TE": 4}, {"WR": 3.0, "RB": 4.0, "TE": 0.0})
    assert [p["pos"] for p in out] == ["WR", "RB", "TE"]


# --------------------------------------------------------------- league priors

def test_full_ppr_is_called_out():
    lines = advisories.league_priors({"rec": 1.0}, DEMO_ROSTER_POSITIONS, 12)
    assert any("PPR" in ln for ln in lines)


def test_standard_scoring_reads_differently_from_ppr():
    ppr = advisories.league_priors({"rec": 1.0}, DEMO_ROSTER_POSITIONS, 12)
    std = advisories.league_priors({"rec": 0}, DEMO_ROSTER_POSITIONS, 12)
    assert ppr != std


def test_superflex_raises_quarterbacks():
    rp = DEMO_ROSTER_POSITIONS + ["SUPER_FLEX"]
    lines = advisories.league_priors({"rec": 1.0}, rp, 12)
    assert any("uperflex" in ln for ln in lines)


def test_a_deep_room_says_scarcity_arrives_early():
    lines = advisories.league_priors({"rec": 1.0}, DEMO_ROSTER_POSITIONS, 14)
    assert any("14" in ln for ln in lines)


def test_priors_stay_short_enough_to_read_on_the_clock():
    """The board is dense on purpose; three lines is the ceiling."""
    rp = DEMO_ROSTER_POSITIONS + ["SUPER_FLEX", "WR", "FLEX"]
    lines = advisories.league_priors({"rec": 1.0, "pass_td": 6.0}, rp, 16)
    assert 1 <= len(lines) <= 3


# ------------------------------------------------------------- starting slots

def test_starting_slots_counts_dedicated_and_flex_separately():
    slots = advisories.starting_slots(DEMO_ROSTER_POSITIONS)
    assert slots["RB"] == 2 and slots["WR"] == 2 and slots["QB"] == 1
    assert slots["FLEX"] == 2
    assert "BN" not in slots


# ------------------------------------------------------- wired into the board

def _run_draft(conn, upto):
    """Advance the demo draft to `upto` picks, CONTINUING from wherever it is.

    Restarting from pick 1 does not replay the same draft: _sim_pick_for_slot
    skips players already taken, so a second pass hands pick 1 a different
    player and the board fills with a scrambled order that is no longer an
    ADP draft. That bug quietly defused the pressure regression test below —
    it passed against garbage.
    """
    from app import demo
    start = conn.execute("SELECT COUNT(*) c FROM draft_picks").fetchone()["c"] + 1
    for i in range(start, upto + 1):
        slot = demo.slot_for_pick(i)
        pid = demo._sim_pick_for_slot(conn, slot, i)
        if pid is None:
            break
        demo.record_pick(conn, i, pid)


def test_board_carries_shelf_advisories(conn):
    from app import brain
    board = brain.get_board(conn)
    assert isinstance(board["shelf"]["findings"], list)
    assert board["shelf"]["byes_known"] in (True, False)


def test_board_carries_league_priors(conn):
    from app import brain
    priors = brain.get_board(conn)["priors"]
    assert priors and all(isinstance(p, str) for p in priors)


def test_my_roster_rows_carry_their_bye(conn):
    """The shelf panel cannot flag a bye it was never handed."""
    from app import brain
    _run_draft(conn, 40)
    board = brain.get_board(conn)
    assert board["my_roster"]
    assert all("bye" in p for p in board["my_roster"])


def test_pressure_rarely_cries_wolf_on_a_room_that_drafts_to_adp(conn):
    """The demo's twelve opponents are independent ADP-followers, so this
    draft holds no runs by construction and EVERY fire here is a false alarm.

    Swept over every window of a full draft, not sampled at a few pick counts
    — sampling is what let the earlier version of this test pass while the
    detector was firing 13.5% of the time. The calibrated threshold holds this
    under 5%; a raw position counter would sit at 95.9%.
    """
    from app import brain
    from app.config import RUN_WINDOW_PICKS
    fires = checked = 0
    for n in range(RUN_WINDOW_PICKS, 181):
        _run_draft(conn, n)
        if conn.execute("SELECT COUNT(*) c FROM draft_picks").fetchone()["c"] < n:
            break
        checked += 1
        fires += 1 if brain.get_board(conn)["pressure"] else 0
    assert checked > 150, f"only swept {checked} windows"
    assert fires / checked <= 0.05, f"{fires}/{checked} false alarms"


def test_byes_never_bid_on_a_pick(conn):
    """The boundary this whole module exists to hold. Align every available
    player onto one bye week — the worst possible bye picture — and the
    suggestion scores must not move by a thousandth."""
    from app import brain
    _run_draft(conn, 30)
    before = {s["id"]: s.get("score") for s in brain.get_board(conn)["suggestions"]}
    conn.execute("UPDATE players SET bye=9")
    conn.commit()
    after = {s["id"]: s.get("score") for s in brain.get_board(conn)["suggestions"]}
    assert before == after


def test_the_draft_engine_does_not_import_the_advisory_layer(conn):
    """A rule with no check gets violated: survival and the suggestion score
    live in draft.py, and nothing advisory may reach them."""
    from pathlib import Path
    from app.engines import draft as draft_engine
    src = Path(draft_engine.__file__).read_text(encoding="utf-8")
    assert "advisories" not in src
