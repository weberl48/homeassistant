"""Pins for the max-review findings: the card may only claim what its kept
swaps earn, a live dry-run parks honestly without touching the mirror, the
kickoff lock binds at execution, and a network blip never wipes practice
reports."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app import brain, db, recs, rules
from app.config import settings


def _week_scenario(conn, kickoff_out: str | None, week: int = 1):
    """My roster: QB starter 'out_qb' (proj 10), bench 'in_qb' (proj 15).
    The optimal diff proposes in_qb for out_qb (+5). kickoff_out sets the
    BENCH player's game clock — a locked candidate must suppress the swap."""
    now = db.utcnow()
    for pid, name, team, proj in (("out_qb", "Old Starter", "AAA", 10.0),
                                  ("in_qb", "Hot Hand", "BBB", 15.0)):
        conn.execute(
            "INSERT OR REPLACE INTO players(sleeper_id,name,pos,team,updated_at) "
            "VALUES(?,?,'QB',?,?)", (pid, name, team, now))
        conn.execute(
            "INSERT OR REPLACE INTO consensus(player_id,week,pts_robust) VALUES(?,?,?)",
            (pid, week, proj))
    conn.execute(
        "INSERT OR REPLACE INTO rosters(roster_id,owner,players_json,starters_json,updated_at) "
        "VALUES(?,?,?,?,?)",
        (settings.my_roster_id, "me", json.dumps(["out_qb", "in_qb"]),
         json.dumps(["out_qb"]), now))
    for team, ko in (("AAA", None), ("BBB", kickoff_out)):
        conn.execute(
            "INSERT OR REPLACE INTO nfl_games(season,week,team,opponent,is_home,kickoff_utc) "
            "VALUES(?,?,?,?,1,?)", (settings.season, week, team, "ZZZ", ko))
    conn.commit()


def test_locked_swap_cannot_inflate_delta(conn):
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
    _week_scenario(conn, kickoff_out=past)
    card = brain.get_week_card(conn, 1)
    assert card["swaps"] == []            # the candidate's game already started
    assert card["delta"] == 0.0           # a dropped swap's gain may not linger
    assert card["material"] is False      # nothing actionable → nothing to push


def test_unlocked_swap_still_proposes(conn):
    soon = (datetime.now(timezone.utc) + timedelta(hours=20)).isoformat(timespec="seconds")
    _week_scenario(conn, kickoff_out=soon)
    card = brain.get_week_card(conn, 1)
    assert len(card["swaps"]) == 1 and card["delta"] == pytest.approx(5.0)
    assert card["material"] is True


def test_kickoff_passed_rule_binds(conn):
    past = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(timespec="seconds")
    _week_scenario(conn, kickoff_out=past)
    fired = rules.evaluate(conn, [{"out_id": "out_qb", "in_id": "in_qb"}], week=1)
    assert "kickoff_passed" in fired      # the lock is binding, not cosmetic


def test_live_dry_run_parks_honestly(conn, monkeypatch):
    """Approve → worker in LIVE dry-run: rec terminal 'dry_run', mirror
    untouched, and the scanner does not re-propose the same set."""
    from hands import worker
    soon = (datetime.now(timezone.utc) + timedelta(hours=20)).isoformat(timespec="seconds")
    _week_scenario(conn, kickoff_out=soon)
    rec_id = recs.scan_lineup(conn, week=1)
    assert rec_id is not None
    recs.approve(conn, rec_id)
    monkeypatch.setattr(settings, "mode", "live")
    monkeypatch.setattr(settings, "hands_dry_run", True)
    # live read_starters would re-sync from the real Sleeper API; pin it to
    # the mirror so the test exercises the dry-run branch, not the network
    monkeypatch.setattr(worker, "read_starters",
                        lambda c: json.loads(brain.my_roster_row(c)["starters_json"]))
    assert worker.run_once(conn) is True
    row = conn.execute("SELECT state FROM recommendations WHERE rec_id=?",
                       (rec_id,)).fetchone()
    assert row["state"] == "dry_run"
    starters = json.loads(brain.my_roster_row(conn)["starters_json"])
    assert starters == ["out_qb"]         # the REAL mirror was never written
    assert recs.scan_lineup(conn, week=1) is None  # dedup: no push loop


def test_injuries_network_error_keeps_reports(conn, monkeypatch):
    from app import ingest
    conn.execute(
        "INSERT OR REPLACE INTO players(sleeper_id,name,pos,team,practice_status) "
        "VALUES('keepme','Held Report','WR','GB','DNP')")
    conn.commit()

    def boom(season):
        raise RuntimeError("nflverse unreachable")

    monkeypatch.setattr(ingest, "fetch_nflverse_injuries", boom)
    with pytest.raises(RuntimeError):
        ingest.etl_injuries(conn)
    row = conn.execute("SELECT practice_status FROM players "
                       "WHERE sleeper_id='keepme'").fetchone()
    assert row["practice_status"] == "DNP"  # a blip must not wipe the reports
