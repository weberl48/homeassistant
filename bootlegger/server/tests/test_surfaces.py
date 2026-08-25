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


def test_waiver_targets_shape(world):
    out = brain.waiver_targets(world, heat={"demo-heat": 3})
    assert out["targets"], "demo street must have targets"
    bids = {t["bid"] for t in out["targets"]}
    assert len(bids) > 1, "tier-bucketed bids must vary, not flat-line"
    for t in out["targets"]:
        assert t["bid"] >= 0 and t["fa_score"] > 0
        assert "lineup_gain" in t
