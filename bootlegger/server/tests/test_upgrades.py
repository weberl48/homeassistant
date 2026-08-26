"""The A+ pass: trade shortlisting, matchup strategy, and continuous FAAB.

Each test here pins a defect that was observed on a running board, not a
hypothetical. The docstrings name the observation so a future change that
re-breaks one of them fails loudly instead of quietly regressing to the
behaviour the report card marked down.
"""
from __future__ import annotations

import json

from app import brain
from app.engines import lineup as lineup_engine
from app.engines import matchup as matchup_engine
from app.engines import trades as trades_engine
from app.engines import waivers as waivers_engine


# ---------------------------------------------------------------------------
# Trades: one deal, listed once
# ---------------------------------------------------------------------------

def _deal(give, receive, my_gain, their_gain, partner=1, score=None, vbd=0.0):
    return {"give_ids": list(give), "receive_ids": list(receive),
            "my_gain": my_gain, "their_gain": their_gain,
            "partner_roster_id": partner, "partner": f"seat {partner}",
            "score": my_gain if score is None else score,
            "vbd_edge": vbd, "market_edge": 0.0}


def test_a_throw_in_that_changes_nothing_is_dominated():
    """Observed: seven of eight suggestions were the same Herbert-for-Montgomery
    trade with different bench men attached, every one showing the identical
    +33.4 / +17.7 — because a piece that cracks neither lineup moves neither
    number."""
    base = _deal(["A"], ["X"], 33.4, 17.7)
    padded = _deal(["A", "B"], ["X", "Y"], 33.4, 17.7)
    assert trades_engine.dominates(base, padded)
    assert not trades_engine.dominates(padded, base)
    out = trades_engine.shortlist([base, padded], limit=8)
    assert [d["give_ids"] for d in out] == [["A"]]


def test_extra_pieces_that_actually_buy_something_survive():
    base = _deal(["A"], ["X"], 10.0, 5.0)
    better = _deal(["A", "B"], ["X", "Y"], 22.0, 5.0)
    assert not trades_engine.dominates(base, better)
    assert len(trades_engine.shortlist([base, better], limit=8)) == 2


def test_unrelated_packages_are_never_dominated():
    a = _deal(["A"], ["X"], 10.0, 5.0)
    b = _deal(["C"], ["Z"], 10.0, 5.0, partner=2)
    assert not trades_engine.dominates(a, b) and not trades_engine.dominates(b, a)


def test_one_partner_cannot_eat_the_whole_shortlist():
    """Observed: 14 of 20 proposals named the same seat. A room has eleven
    other managers and a list that shows one of them is not a list."""
    # One seat with six offers, plus four other seats with one each.
    deals = [_deal([f"m{i}"], [f"t{i}"], 30.0 - i, 5.0, partner=1, score=30.0 - i)
             for i in range(6)]
    deals += [_deal([f"q{k}"], [f"r{k}"], 12.0 - k, 4.0, partner=k, score=12.0 - k)
              for k in (2, 3, 4, 5)]
    out = trades_engine.shortlist(deals, limit=6)
    partners = [d["partner_roster_id"] for d in out]
    assert partners.count(1) <= 2, "every seat's best offer goes in before anyone's third"
    assert set(partners) == {1, 2, 3, 4, 5}
    assert out[0]["score"] == 30.0, "the best deal in the room still leads"


def test_a_thin_room_still_fills_the_list():
    """Diversity is a preference, not a quota: when only two seats have deals,
    showing four rows beats showing two."""
    deals = [_deal([f"a{i}"], [f"b{i}"], 20.0 - i, 5.0, partner=1, score=20.0 - i)
             for i in range(3)]
    deals += [_deal([f"c{i}"], [f"d{i}"], 15.0 - i, 5.0, partner=2, score=15.0 - i)
              for i in range(3)]
    out = trades_engine.shortlist(deals, limit=6)
    assert len(out) == 6


def test_identical_outcomes_from_one_partner_collapse_to_one():
    a = _deal(["A"], ["X"], 8.0, 3.0, partner=1)
    b = _deal(["B"], ["Y"], 8.0, 3.0, partner=1)
    out = trades_engine.shortlist([a, b], limit=8)
    assert len(out) == 1


def test_the_verdict_reconciles_the_two_numbers_a_card_shows():
    """Observed: a card headlined '+33.4 you' above a summary reading
    'projection math is against you by -33.5 ROS VBD'. Both were true and the
    card explained neither."""
    good = trades_engine.value_verdict(20.0, 8.0, 200.0, 1, 1)
    assert good["level"] == "good"
    trade_off = trades_engine.value_verdict(33.4, -20.0, 150.0, 2, 1)
    assert trade_off["level"] == "note" and "depth into starters" in trade_off["line"]
    bad = trades_engine.value_verdict(2.0, -30.0, -300.0, 1, 1)
    assert bad["level"] == "warn"


def test_a_rounding_error_does_not_get_a_siren():
    """Observed: a card warned 'the market says you are paying for it' off a
    -2 FantasyCalc edge, on a scale that runs into the thousands."""
    v = trades_engine.value_verdict(2.2, -17.7, -2.0, 1, 1, market_volume=4000.0)
    assert v["level"] == "note", "a -2 edge is not the market disagreeing"


def test_paying_a_little_season_value_for_a_lot_of_week_is_not_a_warning():
    """Gaining 30 points of starting lineup for 9 points of paper is a trade
    worth making. Calling it a warning teaches the reader to ignore warnings."""
    v = trades_engine.value_verdict(29.7, -19.1, -900.0, 2, 2, market_volume=4000.0)
    assert v["level"] == "note"
    worse = trades_engine.value_verdict(6.6, -19.1, -900.0, 2, 2, market_volume=4000.0)
    assert worse["level"] == "warn"


def test_the_live_suggester_returns_a_readable_list(conn):
    d = brain.suggest_trades(conn, limit=8)
    trades = d["trades"]
    assert trades, "the demo league has deals in it"
    assert d["considered"] >= len(trades)
    outcomes = {(t["partner_roster_id"], t["my_gain"], t["their_gain"]) for t in trades}
    assert len(outcomes) == len(trades), "no two rows may be the same deal"
    counts = {}
    for t in trades:
        counts[t["partner"]] = counts.get(t["partner"], 0) + 1
    assert max(counts.values()) <= 2
    assert all("verdict" in t for t in trades)


# ---------------------------------------------------------------------------
# Matchup: the opponent decides which lineup is right
# ---------------------------------------------------------------------------

def test_win_probability_moves_the_right_way():
    even = matchup_engine.win_probability(110.0, 110.0)
    assert abs(even - 0.5) < 1e-9
    assert matchup_engine.win_probability(140.0, 110.0) > 0.7
    assert matchup_engine.win_probability(80.0, 110.0) < 0.3


def test_strategy_flips_at_both_ends_and_not_in_the_middle():
    assert matchup_engine.strategy(0.85).key == "floor"
    assert matchup_engine.strategy(0.50).key == "balanced"
    assert matchup_engine.strategy(0.15).key == "ceiling"


def test_sigma_prefers_the_leagues_own_weeks_but_refuses_a_thin_book():
    default, note = matchup_engine.sigma_from_history([1.0, -2.0, 3.0])
    assert default == matchup_engine.DEFAULT_TEAM_SIGMA and "only 3 of this league" in note
    residuals = [(-1) ** i * 20.0 + i * 0.1 for i in range(40)]
    sigma, note = matchup_engine.sigma_from_history(residuals)
    assert sigma != matchup_engine.DEFAULT_TEAM_SIGMA
    assert "measured on 40" in note


def test_an_implausible_measurement_is_refused_not_used():
    """A sigma of 2 points would quote 99% win probabilities off a 6-point
    projected margin. Measurement that lands outside the plausible band is a
    bad measurement, not a discovery."""
    sigma, note = matchup_engine.sigma_from_history([0.001 * i for i in range(40)])
    assert sigma == matchup_engine.DEFAULT_TEAM_SIGMA and "outside the plausible" in note


def test_ceiling_and_floor_lineups_differ_from_the_expected_points_one():
    """The whole point: maximising expected points is the wrong objective at
    both ends of the win-probability scale."""
    rp = ["RB", "BN"]
    steady = lineup_engine.PlayerProj("steady", "RB", 12.0, "Steady",
                                      floor=11.0, ceiling=13.0)
    boom = lineup_engine.PlayerProj("boom", "RB", 11.5, "Boom",
                                    floor=2.0, ceiling=28.0)
    pool = [steady, boom]
    assert lineup_engine.optimize(pool, rp, "proj").starter_ids == {"steady"}
    assert lineup_engine.optimize(pool, rp, "floor").starter_ids == {"steady"}
    assert lineup_engine.optimize(pool, rp, "ceiling").starter_ids == {"boom"}


def test_a_lineup_reports_expected_points_whatever_it_optimised_for():
    rp = ["RB", "BN"]
    boom = lineup_engine.PlayerProj("boom", "RB", 11.5, "Boom",
                                    floor=2.0, ceiling=28.0)
    lu = lineup_engine.optimize([boom], rp, "ceiling")
    assert lu.total == 11.5 and lu.objective_total == 28.0
    assert lu.floor_total == 2.0 and lu.ceiling_total == 28.0


def test_an_unavailable_man_has_no_upside_either():
    out = lineup_engine.PlayerProj("x", "RB", 18.0, "Hurt",
                                   injury_status="Out", floor=6.0, ceiling=30.0)
    assert out.startable_proj == 0.0
    assert out.startable_ceiling == 0.0 and out.startable_floor == 0.0


def test_a_source_band_is_clamped_around_the_projection_it_brackets():
    p = lineup_engine.PlayerProj("x", "RB", 12.0, "Odd", floor=15.0, ceiling=9.0)
    assert p.startable_floor <= 12.0 <= p.startable_ceiling


def test_the_week_card_carries_the_matchup(conn):
    card = brain.get_week_card(conn, 1)
    m = card["matchup"]
    assert m and m["opponent"]
    assert 0.0 <= m["win_prob"] <= 1.0
    assert m["bands"]["floor"] <= m["bands"]["expected"] <= m["bands"]["ceiling"]
    assert m["strategy"]["key"] in ("floor", "balanced", "ceiling")
    assert m["sigma_note"], "a win probability must say where its spread came from"


def test_no_pairing_means_no_invented_opponent(conn):
    conn.execute("DELETE FROM matchups")
    conn.commit()
    assert brain.get_week_card(conn, 1)["matchup"] is None


# ---------------------------------------------------------------------------
# Waivers: price tracks value
# ---------------------------------------------------------------------------

BOOK = [1, 2, 2, 3, 5, 5, 8, 10, 12, 15, 18, 22, 26, 33, 41, 52]


def test_price_is_monotone_in_value():
    """Observed: a 33.1 and a 2.5 both priced at $6, and rank 1 to rank 2 fell
    $42 to $17. Three bands cannot track a continuum."""
    prices = [waivers_engine.price_at(p / 10, BOOK, 100)
              for p in range(10, -1, -1)]
    assert prices == sorted(prices, reverse=True)
    assert len(set(prices)) >= 6, "a continuous price must not collapse to bands"


def test_depth_pays_the_depth_price():
    starter = waivers_engine.price_at(0.9, BOOK, 100, starts=True)
    depth = waivers_engine.price_at(0.9, BOOK, 100, starts=False)
    assert depth < starter


def test_the_bid_never_exceeds_the_remaining_budget():
    assert waivers_engine.price_at(1.0, BOOK, 7) <= 7


def test_an_empty_book_says_so_rather_than_guessing():
    assert waivers_engine.price_at(1.0, [], 100) == 0


def test_the_printed_ladder_never_rises():
    assert waivers_engine.enforce_ladder([20, 25, 12, 14, 3]) == [20, 20, 12, 12, 3]


def test_the_live_waiver_board_prices_continuously(conn):
    w = brain.waiver_targets(conn)
    targets = w["targets"]
    assert targets
    bids = [t["bid"] for t in targets]
    assert bids == sorted(bids, reverse=True), "the ladder must read top-down"
    assert targets[0]["drop"] is None or "name" in targets[0]["drop"]
    assert all("lineup_gain" in t for t in targets)


def test_a_man_who_starts_outranks_a_higher_scoring_bench_body(conn):
    """fa_score finds candidates by measuring them against the worst body at
    their position; that is the wrong way to price them. A receiver who beats
    your WR5 but never starts is worth less than a back who starts Sunday."""
    targets = brain.waiver_targets(conn)["targets"]
    starters = [t for t in targets if (t["lineup_gain"] or 0) > 0]
    depth = [t for t in targets if (t["lineup_gain"] or 0) <= 0]
    if starters and depth:
        assert min(t["bid"] for t in starters) >= max(t["bid"] for t in depth) or \
            targets.index(starters[0]) < targets.index(depth[0])


def test_a_departure_opens_a_job_at_that_club_and_position(conn):
    """The waiver week that matters is the one where somebody's starter went on
    IR. Value ranking alone cannot see that; the wire can."""
    rostered = set()
    for r in conn.execute("SELECT players_json FROM rosters"):
        rostered |= set(json.loads(r["players_json"]))
    victim = conn.execute(
        "SELECT sleeper_id, team, pos FROM players WHERE team IS NOT NULL "
        f"AND sleeper_id IN ({','.join('?' * len(rostered))}) LIMIT 1",
        list(rostered)).fetchone()
    conn.execute(
        "INSERT OR REPLACE INTO news(guid,seq,source,player_id,name_raw,headline,"
        "body,link,severity,ailment,departure,published_at,fetched_at) "
        "VALUES('job-1',1,'test',?,'Someone','Placed on injured reserve','','',"
        "'out',NULL,1,datetime('now'),datetime('now'))", (victim["sleeper_id"],))
    conn.commit()
    openings = brain.job_openings(conn, rostered)
    assert (victim["team"], victim["pos"]) in openings


# ---------------------------------------------------------------------------
# Source calibration: sources earn their weight
# ---------------------------------------------------------------------------

from app.engines import calibration as cal  # noqa: E402


def _score(source, n, mae):
    return cal.SourceScore(source, n, mae)


def test_no_evidence_means_equal_weight():
    """The honest prior on day one. A source is not penalised for being new,
    only for being wrong."""
    w, note = cal.weights([], ["a", "b", "c"])
    assert w == {"a": 1 / 3, "b": 1 / 3, "c": 1 / 3}
    assert "equal weight" in note


def test_a_thin_sample_does_not_move_the_vote():
    w, note = cal.weights([_score("a", 10, 3.0), _score("b", 10, 9.0)], ["a", "b"])
    assert w["a"] == w["b"]


def test_the_more_accurate_source_gets_more_vote():
    w, note = cal.weights(
        [_score("a", 500, 3.0), _score("b", 500, 6.0), _score("c", 500, 6.0)],
        ["a", "b", "c"])
    assert w["a"] > w["b"]
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert "a leads at 3.00 MAE" in note


def test_weights_are_shrunk_and_clamped_so_one_source_cannot_take_over():
    """A source ten times more accurate over half a season has not earned ten
    times the vote — and a source that dies mid-season keeps reporting its last
    good numbers forever."""
    w, _ = cal.weights(
        [_score("a", 900, 0.5), _score("b", 900, 20.0), _score("c", 900, 20.0)],
        ["a", "b", "c"])
    assert w["a"] <= cal.MAX_SHARE + 1e-9, "clamp-then-renormalise puts it back over the cap"
    assert min(w.values()) >= cal.MIN_SHARE - 1e-9
    assert abs(sum(w.values()) - 1.0) < 1e-9


def test_an_unscored_source_sits_at_the_average_not_at_zero():
    w, _ = cal.weights(
        [_score("a", 500, 3.0), _score("b", 500, 5.0)], ["a", "b", "newcomer"])
    assert cal.MIN_SHARE <= w["newcomer"] <= cal.MAX_SHARE


def test_the_weighted_mean_renormalises_over_who_actually_answered():
    """A player only two sources carry must not be dragged toward zero by four
    absent votes."""
    wmap = {"a": 0.5, "b": 0.3, "c": 0.2}
    assert abs(cal.weighted_mean({"a": 10.0}, wmap) - 10.0) < 1e-9
    got = cal.weighted_mean({"a": 10.0, "b": 20.0}, wmap)
    assert abs(got - (10 * 0.5 + 20 * 0.3) / 0.8) < 1e-9


def test_scoring_charges_a_miss_in_either_direction(conn):
    """A source that projected 2 for a man who scored 24 must be charged, and
    so must one that projected 24 for a man who scored 2. Filtering on only one
    side of the pair forgives half the errors."""
    conn.execute("INSERT OR REPLACE INTO player_week_actuals(season,week,player_id,pts,updated_at) "
                 "VALUES(2026,1,'p1',24.0,'now')")
    conn.execute("INSERT OR REPLACE INTO player_week_actuals(season,week,player_id,pts,updated_at) "
                 "VALUES(2026,1,'p2',2.0,'now')")
    conn.execute("INSERT OR REPLACE INTO players(sleeper_id,name,pos) VALUES('p1','A','RB')")
    conn.execute("INSERT OR REPLACE INTO players(sleeper_id,name,pos) VALUES('p2','B','RB')")
    conn.execute("INSERT OR REPLACE INTO projections(player_id,week,source,pts) VALUES('p1',1,'lowball',2.0)")
    # p2: projected 24, scored 2 — the opposite miss, and it must cost the same.
    conn.execute("INSERT OR REPLACE INTO projections(player_id,week,source,pts) VALUES('p2',1,'lowball',24.0)")
    conn.commit()
    scores = {s.source: s for s in cal.score_sources(conn, 2026)}
    assert scores["lowball"].n == 2, "both rows are fantasy-relevant"
    assert scores["lowball"].mae == 22.0, "both misses are 22 points"


# ---------------------------------------------------------------------------
# Reading THIS room, not the average room
# ---------------------------------------------------------------------------

from app.engines import room as room_engine  # noqa: E402


def _draft(order):
    """order: list of positions in pick order, 1-indexed pick numbers."""
    return [{"pick_no": i + 1, "pos": p} for i, p in enumerate(order)]


# Six positions so the centring median has something honest to sit on: five
# positions drafted exactly to market, one that isn't.
_FLAT = {"RB": [1.0, 2.0, 3.0, 4.0], "WR": [5.0, 6.0, 7.0, 8.0],
         "TE": [60.0, 70.0, 80.0, 90.0], "K": [150.0, 155.0, 160.0, 165.0],
         "DEF": [140.0, 145.0, 150.0, 155.0]}
MARKET = {"QB": [20.0, 30.0, 40.0, 50.0], **_FLAT}


def _room(qb):
    return {"QB": list(qb), **{k: list(v) for k, v in _FLAT.items()}}


def test_a_room_that_drafts_to_the_market_reads_as_silent():
    room = [_room(MARKET["QB"])] * 2
    t = room_engine.tendencies(room, MARKET)
    assert abs(t["QB"].offset) < 1e-9
    assert room_engine.read_out(t) == []


def test_a_room_that_reaches_for_quarterbacks_is_measured_as_reaching():
    """Positive offset = earlier than the market, which must LOWER survival."""
    early = _room([12.0, 22.0, 32.0, 42.0])          # eight picks ahead
    t = room_engine.tendencies([early, early], MARKET)
    assert t["QB"].offset == 8.0 and t["QB"].direction == "early"
    assert room_engine.adjust_adp(50.0, "QB", t) == 42.0
    assert "takes QBs about 8 picks early" in room_engine.read_out(t)[0]


def test_a_position_that_slides_lets_you_wait_longer():
    late = _room([30.0, 40.0, 50.0, 60.0])           # ten picks behind
    t = room_engine.tendencies([late, late], MARKET)
    assert t["QB"].offset == -10.0
    assert room_engine.adjust_adp(50.0, "QB", t) == 60.0
    assert "slide here" in room_engine.read_out(t)[0]


def test_a_uniform_shift_across_every_position_is_an_artifact_not_a_habit():
    """Observed on the first live run: all six positions read as sliding, which
    is impossible — the same 180 picks cannot all go later than the market. The
    cause was a duplicated ADP row inflating the market curve's density.
    Centring makes the measure immune to that whole class of error."""
    shifted = {pos: [v + 25.0 for v in vals] for pos, vals in MARKET.items()}
    t = room_engine.tendencies([shifted, shifted], MARKET)
    assert all(abs(x.offset) < 1e-9 for x in t.values())
    assert room_engine.read_out(t) == []
    # ...and a genuine difference still survives the same centring.
    one_off = dict(shifted, QB=[v + 25.0 - 8.0 for v in MARKET["QB"]])
    t2 = room_engine.tendencies([one_off, one_off], MARKET)
    assert t2["QB"].offset > 5.0


def test_one_past_draft_is_an_anecdote_not_a_tendency():
    assert room_engine.tendencies([_room([12.0, 22.0, 32.0, 42.0])], MARKET) == {}


def test_no_history_leaves_every_number_exactly_as_the_market_had_it():
    assert room_engine.adjust_adp(50.0, "QB", {}) == 50.0
    assert room_engine.widen_sigma(4.0, "QB", {}) == 4.0


def test_the_correction_is_capped_at_a_round():
    wild = _room([1.0, 2.0, 3.0, 4.0])
    t = room_engine.tendencies([wild, wild], MARKET)
    assert t["QB"].offset == room_engine.MAX_OFFSET


def test_an_inconsistent_room_flattens_the_curve_rather_than_shifting_it():
    """A room that is erratic about a position is less predictable there,
    whichever way it leans — sigma widens, the centre barely moves."""
    a = _room([10.0, 20.0, 30.0, 40.0])
    b = _room([30.0, 40.0, 50.0, 60.0])
    t = room_engine.tendencies([a, b], MARKET)
    assert t["QB"].spread > 0
    assert room_engine.widen_sigma(4.0, "QB", t) > 4.0


def test_the_curve_only_counts_the_first_men_at_each_position():
    picks = _draft(["RB"] * 30)
    curve = room_engine.room_curve(picks, depth=5)
    assert curve["RB"] == [1.0, 2.0, 3.0, 4.0, 5.0]


def test_the_board_reports_no_tendencies_without_history(conn):
    """The demo league has one draft and no past seasons; the board must say so
    rather than implying a calibration it doesn't have."""
    board = brain.get_board(conn)
    assert board["room"]["tendencies"] == []
    assert board["room"]["read"] == []


def test_a_season_scale_batch_can_never_enter_a_weekly_consensus():
    """Probed 2026-08-26: CBS served SEASON totals from its week-3 URL. A
    weekly consensus that swallowed those would put every projection an order
    of magnitude high and quietly wreck every lineup call for the week."""
    from app import ingest
    season_scale = [("p", 120.0 + i) for i in range(200)]
    weekly_scale = [("p", 9.0 + (i % 7)) for i in range(200)]
    assert ingest._batch_ok(season_scale, week=0) is True
    assert ingest._batch_ok(season_scale, week=3) is False
    assert ingest._batch_ok(weekly_scale, week=3) is True
