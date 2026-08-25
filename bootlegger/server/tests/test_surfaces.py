"""Behavior tests for the read surfaces added late in the build: the Scout's
File dossier, the Parlor trade suggester, and brain-level waiver targets.
World = the demo seed (full rosters, tiered FAAB history, complete draft)."""
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


def test_waiver_targets_shape(world):
    out = brain.waiver_targets(world, heat={"demo-heat": 3})
    assert out["targets"], "demo street must have targets"
    bids = {t["bid"] for t in out["targets"]}
    assert len(bids) > 1, "tier-bucketed bids must vary, not flat-line"
    for t in out["targets"]:
        assert t["bid"] >= 0 and t["fa_score"] > 0
        assert "lineup_gain" in t
