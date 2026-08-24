"""End-to-end over the seeded demo DB: board math, the lineup card, and the
whole proposed → approved → executed → verified loop through hands."""
import json

from app import brain, demo, recs
from app.config import settings
from hands import worker


def test_board_shape(conn):
    board = brain.get_board(conn)
    assert board["draft"]["status"] == "drafting"
    assert board["draft"]["my_slot"] == settings.my_roster_id
    assert len(board["players"]) > 150
    assert board["suggestions"], "suggestions must exist pre-draft"
    for s in board["suggestions"]:
        assert 0.0 <= s["survival"] <= 1.0
        assert s["reason"]
    # tiers exist for the draftable pool
    tiers = {p["tier"] for p in board["players"] if p["tier"]}
    assert len(tiers) >= 3


def test_draft_sim_progresses(conn):
    import time

    from app import db
    db.meta_set(conn, "demo_draft_next_tick", "0")  # make it due now
    assert demo.tick(conn, lambda: brain.suggest_my_pick(conn))
    board = brain.get_board(conn)
    assert board["draft"]["current_pick"] == 2
    assert board["recent_picks"][0]["pick_no"] == 1
    picked = [p for p in board["players"] if p.get("pick_no") == 1]
    assert len(picked) == 1


def test_my_pick_uses_suggestion(conn):
    from app import db
    # fast-forward to my first pick (slot 7 -> pick 7)
    for pick_no in range(1, settings.my_roster_id):
        db.meta_set(conn, "demo_draft_next_tick", "0")
        assert demo.tick(conn, lambda: brain.suggest_my_pick(conn))
    suggestion_before = brain.suggest_my_pick(conn)
    db.meta_set(conn, "demo_draft_next_tick", "0")
    assert demo.tick(conn, lambda: brain.suggest_my_pick(conn))
    board = brain.get_board(conn)
    mine = [p for p in board["players"] if p.get("mine")]
    assert len(mine) == 1
    assert mine[0]["id"] == suggestion_before


def test_week_card_is_material(conn):
    card = brain.get_week_card(conn, 1)
    assert card["ready"]
    assert card["material"], "the demo spoils the lineup on purpose"
    assert card["swaps"]
    assert card["injury_flag"], "one starter is ruled Out in the fixture"
    assert card["optimal_total"] >= card["actual_total"]


def test_full_actuation_loop(conn):
    rec_id = recs.scan_lineup(conn, week=1)
    assert rec_id is not None
    row = conn.execute("SELECT state FROM recommendations WHERE rec_id=?", (rec_id,)).fetchone()
    assert row["state"] == "notified"

    # duplicate scan must not double-propose
    assert recs.scan_lineup(conn, week=1) is None

    job_id = recs.approve(conn, rec_id)
    assert job_id

    assert worker.run_once(conn)  # consumes the job
    row = conn.execute("SELECT state FROM recommendations WHERE rec_id=?", (rec_id,)).fetchone()
    assert row["state"] == "verified"
    job = conn.execute("SELECT state FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    assert job["state"] == "done"

    # the lineup is now optimal: no new material diff
    card = brain.get_week_card(conn, 1)
    assert not card["material"]
    # audit trail exists for every step
    steps = [r["step"] for r in conn.execute(
        "SELECT step FROM actions_log WHERE rec_id=? ORDER BY action_id", (rec_id,))]
    assert "pre_verify_ok" in steps
    assert "post_verify_ok" in steps


def test_scope_lock_rejects_foreign_payloads(conn):
    rec_id = recs.scan_lineup(conn, week=1) or conn.execute(
        "SELECT rec_id FROM recommendations ORDER BY rec_id DESC LIMIT 1").fetchone()["rec_id"]
    import pytest
    with pytest.raises(worker.ScopeViolation):
        worker.validate_job({"rec_id": rec_id, "week": 1, "swaps": [],
                             "lineup_hash_expected": "x", "expires_at": "2099-01-01T00:00:00+00:00"})
    with pytest.raises(worker.ScopeViolation):
        worker.validate_job({"rec_id": rec_id, "week": 1,
                             "swaps": [{"out_id": "a", "in_id": "b", "slot": "RB",
                                        "faab": 30}],
                             "lineup_hash_expected": "x", "expires_at": "2099-01-01T00:00:00+00:00"})
    with pytest.raises(worker.ScopeViolation):
        worker.validate_job({"rec_id": rec_id, "week": 1, "swaps": [
            {"out_id": "a", "in_id": "b", "slot": "RB"}],
            "lineup_hash_expected": "x", "expires_at": "2099-01-01T00:00:00+00:00",
            "waiver_add": "nope"})


def test_pre_verify_aborts_on_changed_world(conn):
    # fresh spoiled state: force reseed
    demo.seed(conn, force=True)
    rec_id = recs.scan_lineup(conn, week=1)
    job_id = recs.approve(conn, rec_id)
    # world changes after approval: someone edits the lineup in Sleeper
    roster = brain.my_roster_row(conn)
    starters = json.loads(roster["starters_json"])
    starters[0], starters[1] = starters[1], starters[0]
    conn.execute("UPDATE rosters SET starters_json=? WHERE roster_id=?",
                 (json.dumps(starters), settings.my_roster_id))
    conn.commit()
    assert worker.run_once(conn)
    job = conn.execute("SELECT state FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    assert job["state"] == "aborted"
    rec = conn.execute("SELECT state FROM recommendations WHERE rec_id=?", (rec_id,)).fetchone()
    assert rec["state"] == "failed"


def test_waiver_and_trade_engines_have_no_actuation(conn):
    """Structural containment: nothing in the waiver/trade modules can touch a
    roster, enqueue a job, or import the hands package."""
    import inspect

    from app.engines import trades, waivers
    for mod in (waivers, trades):
        src = inspect.getsource(mod)
        assert "hands" not in src
        assert "INSERT INTO jobs" not in src
        assert "UPDATE rosters" not in src
