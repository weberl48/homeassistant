"""The ESPN adapter, proven against the same etl functions the nightly runs.

The integration's whole claim is that EspnClient speaks SleeperClient's
dialect well enough that ingest.py cannot tell them apart. So these tests do
not assert adapter internals against adapter outputs — they feed canned ESPN
v3 payloads through the REAL etl_league / etl_rosters / etl_matchups /
etl_draft_picks / etl_draft_history, into a demo-seeded database, and assert
what lands in the tables every engine reads.
"""
from __future__ import annotations

import json

import pytest

from app import brain, db, ingest
from app.config import settings
from app.espn import EspnClient, PRO_TEAMS


# ---------------------------------------------------------------------------
# A canned league: 8 teams, H2H points, the shape from the League Info screen.


def _league_doc(season=2026, drafted=True, prev=(2024, 2025)):
    teams = []
    # Two real names per roster from the demo fixture, so the id map has
    # something true to land on; roster ids 1..8.
    names = [("Josh Allen", 1, 2), ("Bijan Robinson", 2, 1),
             ("Ja'Marr Chase", 3, 4), ("Brock Bowers", 4, 13),
             ("Jahmyr Gibbs", 2, 8), ("Puka Nacua", 3, 14),
             ("Lamar Jackson", 1, 33), ("Trey McBride", 4, 22)]
    for tid in range(1, 9):
        nm, posid, pro = names[tid - 1]
        entries = [{
            "lineupSlotId": 0 if posid == 1 else 2,
            "playerPoolEntry": {"player": {
                "id": 10000 + tid, "fullName": nm,
                "defaultPositionId": posid, "proTeamId": pro,
                "stats": [{"statSourceId": 0, "scoringPeriodId": 1,
                           "appliedTotal": 10.0 + tid}],
            }},
        }, {
            # Every roster also carries a defense, mapped by proTeamId.
            "lineupSlotId": 16,
            "playerPoolEntry": {"player": {
                "id": -16000 - tid, "fullName": "Some D/ST",
                "defaultPositionId": 16, "proTeamId": 30,
                "stats": [],
            }},
        }]
        teams.append({
            "id": tid, "name": f"Team {tid}" if tid != 2 else "Wolverines",
            "owners": [f"{{GUID-{tid}}}"],
            "record": {"overall": {"wins": tid % 3, "losses": 2, "ties": 0,
                                   "pointsFor": 100.0 + tid}},
            "roster": {"entries": entries},
        })
    picks = []
    order = list(range(1, 9))
    for rnd in range(1, 3):                      # two rounds, snake
        row = order if rnd % 2 else order[::-1]
        for i, tid in enumerate(row):
            n = (rnd - 1) * 8 + i + 1
            picks.append({"overallPickNumber": n, "roundId": rnd,
                          "teamId": tid,
                          "playerId": 10000 + tid if rnd == 1 else 99000 + n})
    return {
        "settings": {
            "name": "No Punts Intended League",
            "size": 8,
            "rosterSettings": {"lineupSlotCounts": {
                "0": 1, "2": 2, "4": 2, "6": 1, "23": 1, "16": 1, "17": 1,
                "20": 5, "21": 1,
                "10": 3,     # an IDP-ish slot the app has no word for: dropped
            }},
            "scoringSettings": {"scoringItems": [
                {"statId": 53, "points": 1.0},     # full PPR
                {"statId": 4, "points": 4.0},
                {"statId": 25, "points": 6.0},
                {"statId": 999, "points": 2.0},    # unknown stat: dropped
            ]},
        },
        "status": {"previousSeasons": list(prev)},
        "members": [{"id": f"{{GUID-{t}}}", "displayName": f"owner{t}"}
                    for t in range(1, 9)],
        "teams": teams,
        "draftDetail": {"drafted": drafted, "picks": picks},
    }


def _week_doc():
    doc = _league_doc()
    doc["schedule"] = [
        {"id": 1, "matchupPeriodId": 1,
         "home": {"teamId": 1, "totalPoints": 101.5},
         "away": {"teamId": 2, "totalPoints": 96.0}},
        {"id": 2, "matchupPeriodId": 1,
         "home": {"teamId": 3, "totalPoints": 88.0},
         "away": {"teamId": 4, "totalPoints": 90.0}},
        {"id": 3, "matchupPeriodId": 1,
         "home": {"teamId": 5, "totalPoints": 77.0},
         "away": {"teamId": 6, "totalPoints": 82.5}},
        {"id": 4, "matchupPeriodId": 1,
         "home": {"teamId": 7, "totalPoints": 91.0},
         "away": {"teamId": 8, "totalPoints": 70.0}},
        # a future week's pairing that must NOT bleed into week 1
        {"id": 9, "matchupPeriodId": 2,
         "home": {"teamId": 1, "totalPoints": 0},
         "away": {"teamId": 3, "totalPoints": 0}},
    ]
    return doc


@pytest.fixture()
def espn(conn, monkeypatch):
    """An EspnClient over the demo players table, transport replaced."""
    monkeypatch.setattr(settings, "platform", "espn")
    monkeypatch.setattr(settings, "league_id", "1435831655")
    monkeypatch.setattr(settings, "my_roster_id", 2)     # Wolverines
    # A real ESPN deployment runs in its own database; the demo seed's
    # league-shaped rows (12 demo rosters, a demo league row that LIMIT 1
    # would pick up) do not exist there and must not leak into these tests.
    for t in ("league", "rosters", "matchups"):
        conn.execute(f"DELETE FROM {t}")
    conn.commit()
    c = EspnClient(conn)
    docs = {2026: _league_doc(), 2025: _league_doc(season=2025, prev=(2024,)),
            2024: _league_doc(season=2024, prev=())}
    monkeypatch.setattr(c, "_league_doc", lambda season, fresh=False: docs[season])
    monkeypatch.setattr(c, "_week_doc", lambda week: _week_doc())
    return c


# ---------------------------------------------------------------------------
# The dialect: the real etls run unchanged


def test_league_shape_lands_in_the_league_row(espn, conn):
    ingest.etl_league(espn, conn)
    rp = brain.roster_positions(conn)
    assert rp.count("RB") == 2 and rp.count("WR") == 2
    assert rp.count("FLEX") == 1 and rp.count("BN") == 5
    assert "10" not in rp and len([s for s in rp if s == "IR"]) == 1
    row = json.loads(conn.execute("SELECT settings_json FROM league").fetchone()[0])
    assert row["name"] == "No Punts Intended League"
    assert row["settings"]["num_teams"] == 8


def test_scoring_translates_to_sleeper_keys(espn, conn):
    ingest.etl_league(espn, conn)
    scoring = json.loads(conn.execute("SELECT scoring_json FROM league").fetchone()[0])
    assert scoring["rec"] == 1.0, "statId 53 is the PPR signal _league_scoring reads"
    assert scoring["pass_td"] == 4.0
    assert "999" not in scoring and 999 not in scoring


def test_rosters_land_with_sleeper_ids_and_team_names(espn, conn):
    ingest.etl_rosters(espn, conn)
    rows = conn.execute("SELECT * FROM rosters ORDER BY roster_id").fetchall()
    assert len(rows) == 8
    wolverines = rows[1]
    assert wolverines["owner"] == "Wolverines"
    players = json.loads(wolverines["players_json"])
    # Bijan Robinson mapped to the demo table's sleeper id, not an espn id.
    demo_id = conn.execute("SELECT sleeper_id FROM players WHERE name='Bijan Robinson'").fetchone()[0]
    assert demo_id in players
    assert PRO_TEAMS[30] in players, "the defense maps straight to the team abbrev"
    assert wolverines["fpts"] == 102.0


def test_matchups_land_with_actuals(espn, conn):
    ingest.etl_matchups(conn, espn, week=1)
    m = conn.execute("SELECT * FROM matchups WHERE week=1 ORDER BY roster_id").fetchall()
    assert len(m) == 8, "four pairings, one row per roster; week 2 stayed out"
    me = next(r for r in m if r["roster_id"] == 2)
    assert me["opp_roster_id"] == 1
    assert me["points_for"] == 96.0
    n = conn.execute("SELECT COUNT(*) FROM player_week_actuals WHERE week=1").fetchone()[0]
    assert n >= 8, "weekly realized points feed calibration and the ledger"


def test_the_current_draft_flows_through_etl_draft_picks(espn, conn):
    [d] = espn.league_drafts(settings.league_id)
    ingest.etl_draft_picks(espn, conn, d["draft_id"])
    st = json.loads(conn.execute("SELECT settings_json FROM drafts WHERE draft_id=?",
                                 (d["draft_id"],)).fetchone()[0])
    assert st["teams"] == 8 and st["slot"] == 2, "my seat from round-1 order"
    assert st["slot_to_roster_id"]["1"] == 1
    picks = conn.execute("SELECT * FROM draft_picks WHERE draft_id=? ORDER BY pick_no",
                         (d["draft_id"],)).fetchall()
    assert len(picks) == 16
    # Snake: round 2 runs backwards, so pick 9 belongs to the round-1 last seat.
    assert picks[8]["draft_slot"] == 8 and picks[8]["round"] == 2


def test_history_walks_espn_seasons_through_the_sleeper_walker(espn, conn):
    """etl_draft_history follows previous_league_id; the adapter mints season
    tokens for exactly that chain. The walker runs UNMODIFIED."""
    out = ingest.etl_draft_history(conn, espn, seasons=3)
    assert out["leagues"] == 2, f"2025 and 2024, got {out}"
    assert out["drafts"] == 2 and out["picks"] == 32
    hist = conn.execute(
        "SELECT settings_json FROM drafts WHERE draft_id LIKE 'espn-%-2025'").fetchone()
    st = json.loads(hist[0])
    assert st["historical"] is True and st["teams"] == 8


def test_room_tendencies_get_position_from_the_pick(espn, conn):
    """Half the historical picks are men no players table carries (the 99xxx
    ids). Their position rides on the pick row — the same rule Sleeper history
    uses — so the curves are not bent by roster churn."""
    ingest.etl_draft_history(conn, espn, seasons=3)
    rows = conn.execute(
        "SELECT pos, player_id FROM draft_picks WHERE draft_id LIKE 'espn-%-2025' "
        "AND pick_no <= 8").fetchall()
    assert all(r["pos"] for r in rows), "round-1 picks all carry a position"


# ---------------------------------------------------------------------------
# Identity discipline


def test_two_men_one_name_refuses_to_guess(espn, conn):
    conn.execute("INSERT INTO players(sleeper_id,name,pos,team,updated_at) "
                 "VALUES('x1','John Doppel','WR','MIA','now')")
    conn.execute("INSERT INTO players(sleeper_id,name,pos,team,updated_at) "
                 "VALUES('x2','John Doppel','WR','NYJ','now')")
    conn.commit()
    got = espn.map_player({"id": 777, "fullName": "John Doppel",
                           "defaultPositionId": 3, "proTeamId": 15})
    assert got == "espn-777", "ambiguity must yield a synthetic id, not a coin flip"


def test_an_unknown_man_gets_a_synthetic_id(espn):
    got = espn.map_player({"id": 424242, "fullName": "Nobody Anyoneknows",
                           "defaultPositionId": 2, "proTeamId": 3})
    assert got == "espn-424242"


def test_washington_spelling_is_translated(espn):
    got = espn.map_player({"id": 1, "fullName": "Commanders D/ST",
                           "defaultPositionId": 16, "proTeamId": 28})
    assert got == "WAS", "ESPN writes WSH; Sleeper (and this app) write WAS"


# ---------------------------------------------------------------------------
# Auth honesty


def test_missing_cookies_fail_in_words(conn, monkeypatch):
    from app import espn as espn_mod
    monkeypatch.setattr(settings, "espn_swid", "")
    monkeypatch.setattr(settings, "espn_s2", "")
    monkeypatch.setattr(espn_mod, "COOKIE_FILE", __import__("pathlib").Path("/nope"))
    c = EspnClient(conn)
    with pytest.raises(espn_mod.EspnAuthError) as e:
        c._cookies()
    assert "BOOTLEGGER_ESPN_SWID" in str(e.value)


def test_swid_gets_its_braces_back(conn, monkeypatch):
    monkeypatch.setattr(settings, "espn_swid", "ABCD-1234")
    monkeypatch.setattr(settings, "espn_s2", "s2value")
    c = EspnClient(conn)
    assert c._cookies()["SWID"] == "{ABCD-1234}", "ESPN rejects a bare SWID"


def test_history_is_never_presented_as_the_draft(espn, conn):
    """The live ESPN stack, day one: the league had not drafted, the only rows
    in `drafts` were the ingested 2025 history, and every nightly re-touches
    their updated_at — so "newest draft wins" elected LAST YEAR'S draft and
    the board presented it as the draft. History is evidence for the room's
    tendencies, never the present."""
    conn.execute("DELETE FROM drafts")
    conn.execute("DELETE FROM draft_picks")
    conn.commit()
    ingest.etl_draft_history(conn, espn, seasons=3)   # 2025 + 2024, historical
    assert conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 2

    import app.config as cfg
    old_mode = cfg.settings.mode
    cfg.settings.mode = "live"        # demo mode pins the board to DEMO_DRAFT_ID
    try:
        board = brain.get_board(conn)
    finally:
        cfg.settings.mode = old_mode
    d = board["draft"]
    assert d["status"] == "pre_draft", f"presented {d['id']} as current"
    assert d["id"] is None or "espn-" not in str(d["id"]), (
        f"a historical draft ({d['id']}) is on the board")


# ---------------------------------------------------------------------------
# A draft in flight — the state the ESPN side had never been in


def _mid_draft_doc(made=11):
    """The grid half filled: ESPN publishes every slot up front with
    playerId -1, and fills them in as picks land."""
    doc = _league_doc(drafted=False)
    doc["draftDetail"]["inProgress"] = True
    doc["settings"]["draftSettings"] = {"type": "SNAKE", "date": 1788905700000,
                                        "timePerSelection": 90}
    # A unique man per pick: keying off teamId repeated the same eight players
    # in round two, and `picked` is keyed by player, so the board showed eight
    # crossed off out of eleven. No draft takes a man twice.
    for i, p in enumerate(doc["draftDetail"]["picks"]):
        p["playerId"] = (20000 + i) if i < made else -1
    # Mid-draft the ROSTERS are still empty — the whole reason picks cannot be
    # resolved from them.
    for t in doc["teams"]:
        t["roster"]["entries"] = []
    return doc


@pytest.fixture()
def drafting(conn, monkeypatch):
    monkeypatch.setattr(settings, "platform", "espn")
    monkeypatch.setattr(settings, "league_id", "1435831655")
    monkeypatch.setattr(settings, "my_roster_id", 3)
    for t in ("league", "rosters", "matchups"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM drafts")
    conn.execute("DELETE FROM draft_picks")
    conn.commit()
    c = EspnClient(conn)
    doc = _mid_draft_doc()
    monkeypatch.setattr(c, "_league_doc", lambda season, fresh=False: doc)
    ingest.etl_league(c, conn)      # the board needs to know it is 8 teams
    # The real _pool fetches player objects by id from ESPN. Stubbing it to
    # {} made every pick resolve to a synthetic id and cross nobody off the
    # board — which tested nothing. Stand in with sixteen DISTINCT real men
    # from the demo table: cycling eight names resolved three round-two picks
    # onto round-one players, and `picked` is keyed by player.
    POS_ID = {"QB": 1, "RB": 2, "WR": 3, "TE": 4, "K": 5, "DEF": 16}
    real = conn.execute(
        "SELECT name, pos FROM players WHERE pos IN ('QB','RB','WR','TE') "
        "ORDER BY sleeper_id LIMIT 16").fetchall()
    pool = {20000 + i: {"id": 20000 + i, "fullName": r["name"],
                        "defaultPositionId": POS_ID[r["pos"]], "proTeamId": 1}
            for i, r in enumerate(real)}
    monkeypatch.setattr(c, "_pool", lambda ids: {i: pool[i] for i in ids if i in pool})
    return c


def test_a_draft_in_progress_reads_as_drafting(drafting):
    [d] = drafting.league_drafts(settings.league_id)
    got = drafting.draft(d["draft_id"])
    assert got["status"] == "drafting", (
        "`drafted` alone cannot say 'happening right now', and a board that "
        "only knows finished-or-not is no use on the night it matters")
    assert got["settings"]["start_time"] == 1788905700000


def test_unfilled_slots_are_not_picks(drafting, conn):
    [d] = drafting.league_drafts(settings.league_id)
    n = ingest.etl_draft_picks(drafting, conn, d["draft_id"], fresh=True)
    assert n == 11, f"the -1 placeholder grid leaked in as picks ({n})"
    rows = conn.execute("SELECT pick_no FROM draft_picks ORDER BY pick_no").fetchall()
    assert [r["pick_no"] for r in rows] == list(range(1, 12))


def test_the_seating_plan_survives_an_empty_roster(drafting, conn):
    """The order is published BEFORE anyone picks, so the room strip can be
    drawn while the room is still filling."""
    [d] = drafting.league_drafts(settings.league_id)
    ingest.etl_draft_picks(drafting, conn, d["draft_id"], fresh=True)
    st = json.loads(conn.execute("SELECT settings_json FROM drafts WHERE draft_id=?",
                                 (d["draft_id"],)).fetchone()[0])
    assert st["slot_to_roster_id"]["1"] == 1
    assert st["slot"] == 3, "my seat, read off the published order"


def test_the_board_follows_a_live_espn_draft(drafting, conn, monkeypatch):
    """End to end: the ESPN board must show a draft in flight the way the
    Sleeper board did — status drafting, men crossed off, a seat on the
    clock."""
    import app.config as cfg
    [d] = drafting.league_drafts(settings.league_id)
    ingest.etl_draft_picks(drafting, conn, d["draft_id"], fresh=True)
    old = cfg.settings.mode
    cfg.settings.mode = "live"
    try:
        board = brain.get_board(conn)
    finally:
        cfg.settings.mode = old
    dd = board["draft"]
    assert dd["status"] == "drafting"
    assert dd["current_pick"] == 12, f"eleven gone, twelve on the clock ({dd})"
    assert dd["on_clock_slot"], "nobody is shown on the clock"
    assert len([p for p in board["players"] if p.get("pick_no")]) == 11
