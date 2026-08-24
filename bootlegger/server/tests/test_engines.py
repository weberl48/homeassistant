import math

from app.config import DEMO_ROSTER_POSITIONS
from app.engines import consensus, draft, tiers, vbd, waivers
from app.engines.draft import Candidate
from app.engines.lineup import PlayerProj, diff_lineup, optimize


# --- consensus -------------------------------------------------------------

def test_robust_mean_small_n():
    assert consensus.robust_mean([10.0]) == 10.0
    assert consensus.robust_mean([10.0, 20.0]) == 15.0
    assert consensus.robust_mean([10.0, 12.0, 100.0]) == 12.0  # median kills the outlier


def test_robust_mean_trims_extremes():
    # 4+ sources: drop one high and one low
    assert consensus.robust_mean([1.0, 10.0, 12.0, 100.0]) == 11.0


def test_disagreement_ratio():
    assert consensus.disagreement_ratio([100.0, 100.0]) == 0.0
    assert consensus.disagreement_ratio([100.0]) == 0.0
    assert consensus.disagreement_ratio([80.0, 120.0]) > 0.25


# --- tiers -----------------------------------------------------------------

def test_tiers_separate_obvious_clusters():
    pts = [300, 298, 296, 200, 198, 196, 100, 98, 96]
    t = tiers.fit_tiers([float(p) for p in pts])
    assert t[0] == t[1] == t[2] == 1
    assert t[3] == t[4] == t[5]
    assert t[6] == t[7] == t[8]
    assert t[0] < t[3] < t[6]


def test_tiers_tiny_pool():
    assert tiers.fit_tiers([10.0, 9.0]) == [1, 1]


def test_tiers_deterministic():
    pts = [float(300 - i * 7 + (i % 3)) for i in range(30)]
    assert tiers.fit_tiers(pts) == tiers.fit_tiers(pts)


# --- vbd -------------------------------------------------------------------

def test_vbd_baseline():
    rows = {"QB": [(f"q{i}", 400.0 - i * 10) for i in range(20)]}
    out = vbd.compute_vbd(rows)
    # QB12 is the baseline: his vbd is 0, QB1 is +110
    assert out["q11"] == 0.0
    assert out["q0"] == 110.0
    assert out["q19"] < 0


# --- draft engine ----------------------------------------------------------

def test_survival_directionality():
    # ADP 30 player, my pick 20 -> very likely to survive; my pick 40 -> unlikely
    assert draft.survival_prob(30, 3, 20) > 0.99
    assert draft.survival_prob(30, 3, 40) < 0.01
    assert abs(draft.survival_prob(30, 3, 30) - 0.5) < 1e-9


def test_snake_math():
    picks = draft.snake_pick_numbers(7, 12, 3)
    assert picks == [7, 18, 31]
    assert draft.next_pick_after(7, 7, 12, 3) == 18
    assert draft.next_pick_after(31, 7, 12, 3) is None


def test_expected_best_vbd_closed_form():
    pool = [Candidate("a", "RB", 100, 0.5), Candidate("b", "RB", 80, 1.0)]
    # E = 100*0.5 + 80*1.0*0.5 = 90
    assert math.isclose(draft.expected_best_vbd(pool), 90.0)


def test_suggestion_score_prefers_scarcity():
    # Two equal-VBD players; the one whose position collapses behind him scores higher.
    rb_pool = [Candidate("rb1", "RB", 100, 0.2), Candidate("rb2", "RB", 40, 0.9)]
    wr_pool = [Candidate("wr1", "WR", 100, 0.2), Candidate("wr2", "WR", 95, 0.9)]
    s_rb = draft.suggestion_score(rb_pool[0], rb_pool, 1.0)
    s_wr = draft.suggestion_score(wr_pool[0], wr_pool, 1.0)
    assert s_rb > s_wr


def test_need_multiplier():
    rp = DEMO_ROSTER_POSITIONS
    assert draft.roster_need_multiplier("RB", {}, rp) == 1.0
    assert draft.roster_need_multiplier("RB", {"RB": 2}, rp) == 0.85  # flex open
    assert draft.roster_need_multiplier("RB", {"RB": 3}, rp) == 0.55  # all full
    assert draft.roster_need_multiplier("K", {}, rp) == 0.25          # not yet
    assert draft.roster_need_multiplier(
        "K", {"QB": 1, "RB": 3, "WR": 2, "TE": 1}, rp) == 1.0          # now yes


# --- lineup ----------------------------------------------------------------

def _roster():
    return [
        PlayerProj("qb1", "QB", 20), PlayerProj("qb2", "QB", 15),
        PlayerProj("rb1", "RB", 18), PlayerProj("rb2", "RB", 14),
        PlayerProj("rb3", "RB", 12),
        PlayerProj("wr1", "WR", 17), PlayerProj("wr2", "WR", 13),
        PlayerProj("wr3", "WR", 11),
        PlayerProj("te1", "TE", 10), PlayerProj("k1", "K", 8),
        PlayerProj("def1", "DEF", 7),
    ]


def test_optimizer_uses_flex_for_best_leftover():
    lineup = optimize(_roster(), DEMO_ROSTER_POSITIONS)
    ids = lineup.starter_ids
    assert {"qb1", "rb1", "rb2", "wr1", "wr2", "te1", "k1", "def1"} <= ids
    assert "rb3" in ids  # rb3 (12) beats wr3 (11) for FLEX
    assert math.isclose(lineup.total, 20 + 18 + 14 + 17 + 13 + 10 + 12 + 8 + 7)


def test_out_player_is_routed_around():
    roster = _roster()
    roster[2] = PlayerProj("rb1", "RB", 18, injury_status="Out")
    lineup = optimize(roster, DEMO_ROSTER_POSITIONS)
    assert "rb1" not in lineup.starter_ids


def test_diff_flags_material_swap():
    actual = ["qb1", "rb2", "rb3", "wr1", "wr2", "te1", "rb1", "k1", "def1"]
    # starting rb2/rb3 with rb1 in flex is fine; bench nobody -> optimal identical
    d = diff_lineup(_roster(), actual, DEMO_ROSTER_POSITIONS)
    assert d.delta == 0.0 and not d.swaps
    # now bench the stud: qb2 starting over qb1
    actual2 = ["qb2", "rb1", "rb2", "wr1", "wr2", "te1", "rb3", "k1", "def1"]
    d2 = diff_lineup(_roster(), actual2, DEMO_ROSTER_POSITIONS)
    assert d2.delta == 5.0
    assert d2.material
    assert {"out_id": "qb2", "in_id": "qb1", "slot": "QB", "gain": 5.0} in d2.swaps


# --- waivers ---------------------------------------------------------------

def test_percentile_and_bid():
    hist = [1, 2, 3, 5, 8, 12, 15, 20, 25, 40]
    p70 = waivers.percentile([float(x) for x in hist], 70)
    assert 12 <= p70 <= 20
    advice = waivers.size_bid(10.0, [float(x) for x in hist], remaining_budget=100)
    assert advice.bid >= 1
    assert not advice.hard_confirm
    big = waivers.size_bid(50.0, [60.0] * 10, remaining_budget=100)
    assert big.hard_confirm


def test_plus_one_over_round():
    assert waivers.plus_one_over_round(20) == 21
    assert waivers.plus_one_over_round(14.6) == 16  # rounds to 15, +1 over the 5
    assert waivers.plus_one_over_round(13.2) == 13


def test_suggestion_score_zero_regret_when_he_survives():
    # regret semantics: the wait-pool INCLUDES the candidate, so a star who
    # will still be there at my next turn scores ~0 — "wait on him"
    pool = [Candidate("a", "QB", 80.0, 1.0), Candidate("b", "QB", 30.0, 1.0)]
    assert math.isclose(draft.suggestion_score(pool[0], pool, 1.0), 0.0, abs_tol=1e-9)


def test_suggestion_score_full_cliff_when_vanishing():
    # a vanishing candidate scores his value over the expected best survivor
    pool = [Candidate("a", "RB", 100.0, 0.0), Candidate("b", "RB", 40.0, 1.0)]
    assert math.isclose(draft.suggestion_score(pool[0], pool, 1.0), 60.0)


def test_suggestion_score_survival_scales_regret():
    # 50% survival: E[best at next turn] = .5*100 + .5*40 = 70 -> regret 30
    pool = [Candidate("a", "RB", 100.0, 0.5), Candidate("b", "RB", 40.0, 1.0)]
    assert math.isclose(draft.suggestion_score(pool[0], pool, 1.0), 30.0)


def test_consolidated_stud_premium():
    # one 100-worth stud beats two 50s: finite roster slots, KTC-school curve
    from app.engines import trades
    assert trades.consolidated([100.0]) > trades.consolidated([50.0, 50.0])
    # but not absurdly — the premium is a margin, not a cliff
    assert trades.consolidated([100.0]) < trades.consolidated([50.0, 50.0]) * 1.5


def test_consolidated_monotonic():
    from app.engines import trades
    assert trades.consolidated([60.0, 20.0]) > trades.consolidated([50.0, 20.0])
    assert trades.consolidated([]) == 0.0
