"""Behavior tests for the read surfaces added late in the build: the Scout's
File dossier, the Parlor trade suggester, and brain-level waiver targets.
World = the demo seed (full rosters, tiered FAAB history, complete draft)."""
import json
import sqlite3

import pytest

from app import brain, db, demo


@pytest.fixture(scope="module")
def world():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    demo.seed(conn)
    return conn


def test_dossier_shape_and_balance(world):
    pid = world.execute(
        "SELECT player_id FROM consensus WHERE week=0 "
        "ORDER BY pts_robust DESC LIMIT 1").fetchone()["player_id"]
    d = brain.player_dossier(world, pid)
    assert d and d["name"]
    assert d["sources"], "per-source figures must be present"
    assert d["consensus"] and d["consensus"] > 0
    assert isinstance(d["insights"], list)
    poss = {b["pos"] for b in d["balance"]}
    assert {"QB", "RB", "WR", "TE", "K", "DEF"} <= poss
    for b in d["balance"]:
        assert 0 <= b["before"] <= 160 and 0 <= b["after"] <= 160
        assert b["after"] >= b["before"]  # adding a player never weakens a slot


def test_dossier_unknown_player(world):
    assert brain.player_dossier(world, "nope-123") is None


def test_suggest_trades_mutual_benefit(world):
    out = brain.suggest_trades(world, limit=6)
    assert "trades" in out
    for t in out["trades"]:
        assert t["my_gain"] >= 2.0, "a suggested deal must improve my lineup"
        assert t["their_gain"] >= -3.0, "the other side must not be gutted"
        give = {p["id"] for p in t["give"]}
        get = {p["id"] for p in t["receive"]}
        assert give and get and not give & get


@pytest.fixture()
def predraft_world():
    """The demo world before anyone owns a player — every roster emptied."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    demo.seed(conn)
    conn.execute("UPDATE rosters SET players_json='[]', starters_json='[]'")
    return conn


def test_week_card_refuses_predraft(predraft_world):
    """Empty roster must NOT read 'lineup optimal, projected 0.0'."""
    card = brain.get_week_card(predraft_world)
    assert card["ready"] is False
    assert card.get("note"), "the refusal must explain when the room opens"


def test_waivers_refuse_predraft(predraft_world):
    """With nobody rostered, 'top free agents' is just the player pool —
    the street must decline instead of bidding $100 on Josh Allen."""
    out = brain.waiver_targets(predraft_world)
    assert out["targets"] == []
    assert "draft" in out["note"].lower()


@pytest.fixture()
def graded_world():
    """Demo world with a fabricated COMPLETE draft: each roster's players are
    laid into snake picks (the demo seed itself is mid-draft with no picks)."""
    import json

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    demo.seed(conn)
    rosters = conn.execute(
        "SELECT roster_id, players_json FROM rosters ORDER BY roster_id").fetchall()
    lists = [json.loads(r["players_json"]) for r in rosters]
    teams, rounds = len(rosters), min(len(li) for li in lists)
    pick_no = 0
    for rnd in range(rounds):
        order = range(teams) if rnd % 2 == 0 else reversed(range(teams))
        for idx in order:
            pick_no += 1
            conn.execute(
                "INSERT INTO draft_picks(draft_id,pick_no,round,draft_slot,roster_id,"
                "player_id,ts) VALUES(?,?,?,?,?,?,?)",
                (demo.DEMO_DRAFT_ID, pick_no, rnd + 1, idx + 1,
                 rosters[idx]["roster_id"], lists[idx][rnd], db.utcnow()))
    conn.execute("UPDATE drafts SET status='complete'")
    conn.commit()
    return conn


def test_draft_grades(graded_world):
    """The Report Card: every seat graded on the league curve, one seat mine,
    ranks ordered by composite, notes written."""
    g = brain.draft_grades(graded_world)
    assert g["ready"]
    teams = g["teams"]
    assert len(teams) >= 8
    assert sum(1 for t in teams if t["mine"]) == 1
    assert len({t["grade"] for t in teams}) > 1, "a curve must spread grades"
    assert [t["rank"] for t in teams] == list(range(1, len(teams) + 1))
    comps = [t["composite"] for t in teams]
    assert comps == sorted(comps, reverse=True)
    for t in teams:
        assert t["starters"] > 0
        assert t["note"]
        assert set(t["components"]) >= {"starters", "vbd", "surplus", "depth"}


def test_grades_refuse_mid_draft(predraft_world):
    """Half a draft gets no report card — completion is derived from the
    pick count, not just the status label."""
    predraft_world.execute("UPDATE drafts SET status='drafting'")
    predraft_world.execute("DELETE FROM draft_picks WHERE pick_no > 40")
    g = brain.draft_grades(predraft_world)
    assert g["ready"] is False and g["note"]


def test_grade_letter_curve():
    from app.engines import grades as ge
    assert ge.letter(2.0) == "A+"
    assert ge.letter(0.0) == "B"
    assert ge.letter(-2.0) == "D"


def test_live_league_draft_guard(world):
    """C1 pin: a live NON-practice draft blocks scrimmage binds; the same
    draft bound AS the scrimmage does not; a finished draft never does."""
    assert brain.live_league_draft(world) is True   # demo seed is 'drafting'
    did = world.execute("SELECT draft_id FROM drafts").fetchone()["draft_id"]
    db.meta_set(world, "practice_draft_id", did)    # it IS the scrimmage
    assert brain.live_league_draft(world) is False
    world.execute("DELETE FROM meta WHERE key='practice_draft_id'")
    world.execute("UPDATE drafts SET status='complete'")
    assert brain.live_league_draft(world) is False
    world.execute("UPDATE drafts SET status='drafting'")  # restore for others
    world.commit()


def test_practice_lifecycle(predraft_world):
    """set_practice sweeps the previous room's rows; clear removes everything."""
    conn = predraft_world
    conn.execute("INSERT INTO drafts(draft_id,status,settings_json,updated_at) "
                 "VALUES('old-room','complete','{}','2026-01-01')")
    conn.execute("INSERT INTO draft_picks(draft_id,pick_no,round,draft_slot,"
                 "roster_id,player_id,ts) VALUES('old-room',1,1,1,1,'x','2026-01-01')")
    db.meta_set(conn, "practice_draft_id", "old-room")
    brain.set_practice(conn, "new-room")
    assert db.meta_get(conn, "practice_draft_id") == "new-room"
    assert conn.execute("SELECT COUNT(*) c FROM drafts WHERE draft_id='old-room'"
                        ).fetchone()["c"] == 0, "old scrimmage rows must be swept"
    cleared = brain.clear_practice(conn)
    assert cleared == "new-room"
    assert db.meta_get(conn, "practice_draft_id") is None


def test_grades_refuse_empty_complete(predraft_world):
    """W2 pin: a 'complete' label with zero picks (cancelled room) must not
    reach the curve math."""
    predraft_world.execute("UPDATE drafts SET status='complete'")
    predraft_world.execute("DELETE FROM draft_picks")
    g = brain.draft_grades(predraft_world)
    assert g["ready"] is False


def test_grades_missing_risk_imputed():
    """W1 pin: a seat the injury data doesn't cover grades AVERAGE sturdiness,
    never sturdiest."""
    from app.engines import grades as ge
    base = {"starters": 100.0, "vbd": 10.0, "surplus": 0.0, "depth": 1}
    teams = [{**base, "slot": 1, "risk": 10.0},
             {**base, "slot": 2, "risk": 30.0},
             {**base, "slot": 3, "risk": None}]
    out = ge.compose(teams)
    by_slot = {t["slot"]: t for t in out}
    assert by_slot[3]["components"]["risk"]["z"] == 0.0, "imputed to the mean"
    assert by_slot[1]["components"]["risk"]["z"] > by_slot[3]["components"]["risk"]["z"], \
        "the genuinely sturdy seat must out-grade the unknown one"


def test_slip_roundtrip(world):
    """set_queue drops unknowns and dupes, preserves order; get_queue resolves
    display rows in that order."""
    pids = [r["player_id"] for r in world.execute(
        "SELECT player_id FROM consensus WHERE week=0 "
        "ORDER BY pts_robust DESC LIMIT 3")]
    n = brain.set_queue(world, [pids[0], "nope-999", pids[1], pids[0], pids[2]])
    assert n == 3
    q = brain.get_queue(world)
    assert [p["id"] for p in q["queue"]] == pids
    assert q["pilot_armed"] is False


def test_resolve_pilot_pick():
    """Slip first while anyone on it survives; The Call when it runs dry;
    None when the world is empty."""
    sugg = [{"id": "c1"}, {"id": "c2"}]
    assert brain.resolve_pilot_pick(["a", "b"], set(), sugg) == ("a", "slip")
    assert brain.resolve_pilot_pick(["a", "b"], {"a"}, sugg) == ("b", "slip")
    assert brain.resolve_pilot_pick(["a", "b"], {"a", "b"}, sugg) == ("c1", "call")
    assert brain.resolve_pilot_pick(["a"], {"a", "c1"}, sugg) == ("c2", "call")
    assert brain.resolve_pilot_pick([], {"c1", "c2"}, sugg) is None


def test_ros_status_separates_weekly_from_season():
    """A one-week Out/Doubtful tag must NOT zero a player for rest-of-season
    math (the season sim caught the Parlor valuing a dump of a healthy
    starter at +49.5 because of this); IR/PUP/Sus must still carry."""
    from app.engines import lineup as le
    assert le.ros_status("Out") is None
    assert le.ros_status("Doubtful") is None
    assert le.ros_status("Questionable") is None
    assert le.ros_status(None) is None
    for long_term in ("IR", "PUP", "Sus", "NA"):
        assert le.ros_status(long_term) == long_term
    # and the weekly optimizer still zeroes a weekly Out
    p = le.PlayerProj("x", "RB", 20.0, "Guy", "Out")
    assert p.startable_proj == 0.0
    assert le.PlayerProj("x", "RB", 20.0, "Guy",
                         le.ros_status("Out")).startable_proj == 20.0


def test_trades_ignore_weekly_injury(world):
    """Tagging a rostered starter Doubtful must not make trading him away
    look like a windfall."""
    my = brain.my_roster_row(world)
    pid = json.loads(my["players_json"])[0] if my else None
    assert pid
    before = brain.suggest_trades(world, limit=8)["trades"]
    world.execute("UPDATE players SET injury_status='Doubtful' WHERE sleeper_id=?", (pid,))
    world.commit()
    after = brain.suggest_trades(world, limit=8)["trades"]
    world.execute("UPDATE players SET injury_status=NULL WHERE sleeper_id=?", (pid,))
    world.commit()
    gain = lambda ts: max((t["my_gain"] for t in ts
                           if any(p["id"] == pid for p in t["give"])), default=0.0)
    assert gain(after) <= gain(before) + 5.0, \
        "a weekly injury tag must not inflate the value of dumping him"


def test_waiver_bids_spread_across_bands(world):
    """Bids must differentiate. Fixed point thresholds flat-lined every bid
    at one dollar once fa_score ran on season-scale value; banding is by rank
    in the pool now, which is scale-free."""
    out = brain.waiver_targets(world)
    bids = [t["bid"] for t in out["targets"]]
    assert len(bids) >= 4
    assert len(set(bids)) > 1, "tier-bucketed bids must vary, not flat-line"
    # the ladder must never invert: bids are non-increasing down the ranking
    # (a thin hot band falling back to the whole book used to price the best
    # target BELOW the second-best)
    assert bids == sorted(bids, reverse=True), f"bid ladder inverted: {bids}"


def test_parse_draft_id():
    """Scrimmage paste box: room URLs, bare ids, and junk."""
    good = "1397719078969278464"
    assert brain.parse_draft_id(f"https://sleeper.com/draft/nfl/{good}") == good
    assert brain.parse_draft_id(f"https://sleeper.com/draft/nfl/{good}?ftue=commish") == good
    assert brain.parse_draft_id(good) == good
    assert brain.parse_draft_id("  " + good + "  ") == good
    assert brain.parse_draft_id("https://sleeper.com/leagues/settings") is None
    assert brain.parse_draft_id("draft 1234") is None   # too short to be a snowflake
    assert brain.parse_draft_id("") is None


def test_league_rosters_shape(world):
    """The deal checker's picker: one roster flagged mine, owners named,
    every pool sorted like a depth chart."""
    rs = brain.league_rosters(world)["rosters"]
    assert len(rs) >= 2
    assert sum(1 for r in rs if r["mine"]) == 1
    for r in rs:
        assert r["owner"]
        assert r["players"], "demo rosters are drafted"
        pts = [p["pts"] for p in r["players"]]
        assert pts == sorted(pts, reverse=True)


@pytest.fixture()
def deep_street(world):
    """The demo world with a REAL free-agent pool bolted on.

    The shipped demo fixture is 182 players and 168 of them end up rostered,
    so the street holds five men. Every bug that only appears when the pool is
    large is therefore invisible to it — including the one this fixture exists
    to catch. Three hundred unrostered receivers, spread across the value
    range so the ranking has something to rank.
    """
    rows = []
    for i in range(300):
        pid = f"fa-{i:03d}"
        rows.append((pid, f"Streeter {i:03d}", "WR", "FA"))
    world.executemany(
        "INSERT INTO players(sleeper_id,name,pos,team,status,updated_at) "
        "VALUES(?,?,?,?, 'Active', '2026-08-26T00:00:00+00:00')", rows)
    # Geometric decay from a genuine league-winner down to waiver chaff, which
    # is the shape a real street has. The original fixture stepped down 0.55 a
    # man, so the top twenty were within six points of each other — flat enough
    # that no pricing rule could differentiate them, which was fine when price
    # came from RANK and is the whole question now that it comes from value.
    world.executemany(
        "INSERT INTO consensus(player_id,week,pts_mean,pts_robust,stdev,tier,vbd) "
        "VALUES(?,0,?,?,1.0,3,?)",
        [(f"fa-{i:03d}", 300.0 * (0.93 ** i), 300.0 * (0.93 ** i),
          300.0 * (0.93 ** i) - 150.0) for i in range(300)])
    world.commit()
    return world


def test_faab_ladder_survives_a_real_pool(deep_street):
    """The priced ladder must behave whatever the pool size.

    History: value_pct used to be the target's RANK, first against the whole
    scored pool (which squeezed every visible row into the top 20/n of the
    book) and then against the shortlist (which fixed that and introduced a
    worse one — rank 1 exists every week, so the best available always drew
    the 100th percentile). Pricing is now absolute: a fraction of the width of
    the roster the man would join. See `engines.waivers.value_fraction` and
    `tests/test_waiver_horizon.py` for the value-response property itself.

    The spread assertion this test used to carry is deliberately GONE. It
    asserted that the shown percentiles span half the book, which under an
    absolute anchor is a statement about the pool rather than the pricing —
    twenty men within six points of each other SHOULD price within a dollar
    of each other, and asserting otherwise would pin the very cry-wolf
    behaviour the rest of this test forbids. What survives is everything that
    is still a property of the ladder rather than of the fixture.
    """
    out = brain.waiver_targets(deep_street)
    targets = out["targets"]
    assert len(targets) > 5, "the deep fixture must actually widen the street"
    assert out["pool"] > 250, "the whole pool must still be counted and reported"

    # A warning that fires on every row has stopped being a warning.
    flagged = sum(1 for t in targets if t["hard_confirm"])
    assert flagged < len(targets), (
        f"all {len(targets)} targets tripped the big-swing confirm — "
        "the whole ladder is priced at the top of the book")

    bids = [t["bid"] for t in targets]
    assert bids == sorted(bids, reverse=True), f"bid ladder inverted: {bids}"
    assert len(set(bids)) > 1, f"bids did not differentiate at all: {bids}"
    assert min(bids) * 2 < max(bids), (
        f"bids barely differentiate across a pool spanning a league-winner "
        f"down to chaff: {bids}")


def test_waiver_targets_shape(world):
    out = brain.waiver_targets(world, heat={"demo-heat": 3})
    assert out["targets"], "demo street must have targets"
    bids = {t["bid"] for t in out["targets"]}
    assert len(bids) > 1, "tier-bucketed bids must vary, not flat-line"
    for t in out["targets"]:
        assert t["bid"] >= 0 and t["fa_score"] > 0
        assert "lineup_gain" in t
