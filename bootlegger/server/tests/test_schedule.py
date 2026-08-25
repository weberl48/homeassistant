"""The schedule layer: nflverse ETL, kickoff math, byes, weather, and the
league-derived VOLS baselines. All network calls are monkeypatched — the
fixture builds a full synthetic 272-game season so the credibility guard and
the bye derivation exercise the real shapes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import rules, schedule
from app.config import VOLS_BASELINES
from app.engines import vbd

TEAMS = sorted(schedule.STADIUM_COORDS)  # the real 32, Sleeper vocabulary


def synthetic_season(season: int = 2026) -> list[dict]:
    """32 teams, 18 weeks, one bye each in weeks 5-12 (byes in groups of
    four), 13:00 ET Sunday kickoffs — 272 REG games, like the real thing."""
    bye_of = {t: 5 + (i // 4) % 8 for i, t in enumerate(TEAMS)}
    games = []
    for week in range(1, 19):
        gameday = (datetime(2026, 9, 13) + timedelta(days=7 * (week - 1))).strftime("%Y-%m-%d")
        active = [t for t in TEAMS if bye_of[t] != week]
        for i in range(0, len(active), 2):
            home, away = active[i], active[i + 1]
            games.append({
                "week": week, "gameday": gameday, "gametime": "13:00",
                "away_team": away, "home_team": home,
                "roof": "dome" if home in ("MIN", "DET", "NO") else "outdoors",
                "stadium": f"{home} Field", "location": "Home",
                # Vegas: home favored by 3 in a 44.5 game for week 1; lines
                # unposted (None) beyond it, like the real file's tail weeks.
                "spread_line": 3.0 if week == 1 else None,
                "total_line": 44.5 if week == 1 else None,
            })
    return games


@pytest.fixture()
def sched_conn(conn, monkeypatch):
    monkeypatch.setattr(schedule, "fetch_nflverse_games", lambda season: synthetic_season(season))
    schedule.etl_schedule(conn, 2026)
    return conn


def test_etl_schedule_both_perspectives_and_utc(sched_conn):
    g = schedule.game_for(sched_conn, TEAMS[0], 1)
    assert g is not None and g["opponent"] in TEAMS
    mirror = schedule.game_for(sched_conn, g["opponent"], 1)
    assert mirror["opponent"] == TEAMS[0]
    assert g["is_home"] != mirror["is_home"]
    # 13:00 US/Eastern in September is EDT — 17:00 UTC. The tz conversion is
    # the whole reason kickoff rules can be trusted.
    assert g["kickoff_utc"].startswith("2026-09-13T17:00")
    n = sched_conn.execute("SELECT COUNT(*) c FROM nfl_games").fetchone()["c"]
    assert n == 272 * 2


def test_vegas_lines_team_perspective(sched_conn):
    """positive spread_line = HOME favored (nflverse convention); each team
    row carries its own side of the line and its implied total."""
    home = next(r for r in sched_conn.execute(
        "SELECT * FROM nfl_games WHERE week=1 AND is_home=1 LIMIT 1"))
    away = schedule.game_for(sched_conn, home["opponent"], 1)
    assert home["spread"] == 3.0 and away["spread"] == -3.0
    assert home["implied_total"] == pytest.approx(23.75, abs=0.06)  # (44.5+3)/2
    assert away["implied_total"] == pytest.approx(20.75, abs=0.06)
    # Unposted lines stay NULL, they don't become zeros.
    late = sched_conn.execute(
        "SELECT spread, implied_total FROM nfl_games WHERE week=15 LIMIT 1").fetchone()
    assert late["spread"] is None and late["implied_total"] is None


def test_migration_idempotent(sched_conn):
    """init_db (schema + guarded ALTERs) must be re-runnable on a DB that
    already carries every column — the live Pi runs it on every boot."""
    from app import db as db_mod
    db_mod.init_db(sched_conn)
    db_mod.init_db(sched_conn)


def test_short_read_refused(conn, monkeypatch):
    monkeypatch.setattr(schedule, "fetch_nflverse_games", lambda season: synthetic_season()[:50])
    with pytest.raises(RuntimeError):
        schedule.etl_schedule(conn, 2026)


def test_bye_weeks_and_backfill(sched_conn):
    byes = schedule.bye_weeks(sched_conn, 2026)
    assert set(byes) == set(TEAMS)
    assert all(5 <= w <= 12 for w in byes.values())
    sched_conn.execute(
        "INSERT OR REPLACE INTO players(sleeper_id,name,pos,team,bye) "
        "VALUES('sched_t1','Bye Guy','RB',?,NULL)", (TEAMS[0],))
    schedule.backfill_byes(sched_conn, 2026)
    row = sched_conn.execute(
        "SELECT bye FROM players WHERE sleeper_id='sched_t1'").fetchone()
    assert row["bye"] == byes[TEAMS[0]]


def test_kickoff_hours_away(sched_conn):
    now = datetime(2026, 9, 13, 11, 0, tzinfo=timezone.utc)  # 6h before kickoff
    hrs = schedule.kickoff_hours_away(sched_conn, TEAMS[0], 1, now=now)
    assert hrs == pytest.approx(6.0)
    assert schedule.kickoff_hours_away(sched_conn, None, 1) is None


def test_questionable_near_kickoff_rule(sched_conn):
    team = TEAMS[0]
    soon = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(timespec="seconds")
    sched_conn.execute(
        "UPDATE nfl_games SET kickoff_utc=? WHERE week=1 AND team=?", (soon, team))
    sched_conn.execute(
        "INSERT OR REPLACE INTO players(sleeper_id,name,pos,team,injury_status) "
        "VALUES('sched_q1','Game Time Decision','WR',?,'Questionable')", (team,))
    fired = rules.evaluate(sched_conn, [{"out_id": "sched_q1", "in_id": None}], week=1)
    assert "questionable_near_kickoff" in fired
    # Same player, kickoff far away: the rule stays quiet.
    far = (datetime.now(timezone.utc) + timedelta(hours=30)).isoformat(timespec="seconds")
    sched_conn.execute(
        "UPDATE nfl_games SET kickoff_utc=? WHERE week=1 AND team=?", (far, team))
    fired = rules.evaluate(sched_conn, [{"out_id": "sched_q1", "in_id": None}], week=1)
    assert "questionable_near_kickoff" not in fired


def test_weather_rule_trips_on_wind(sched_conn):
    team = TEAMS[1]
    sched_conn.execute(
        "UPDATE nfl_games SET wind_mph=24.0 WHERE week=1 AND team=?", (team,))
    sched_conn.execute(
        "INSERT OR REPLACE INTO players(sleeper_id,name,pos,team) "
        "VALUES('sched_w1','Deep Threat','WR',?)", (team,))
    fired = rules.evaluate(sched_conn, [{"in_id": "sched_w1", "out_id": None}], week=1)
    assert "weather_flag_on_game" in fired
    sched_conn.execute(
        "UPDATE nfl_games SET wind_mph=8.0 WHERE week=1 AND team=?", (team,))
    fired = rules.evaluate(sched_conn, [{"in_id": "sched_w1", "out_id": None}], week=1)
    assert "weather_flag_on_game" not in fired


def test_refresh_weather_outdoor_only_and_ttl(sched_conn, monkeypatch):
    calls = []

    def fake_wx(lat, lon, date, hour, timeout=15.0):
        calls.append((date, hour))
        return {"wind_mph": 12.0, "precip_prob": 10.0, "temp_f": 61.0}

    monkeypatch.setattr(schedule, "fetch_openmeteo_hour", fake_wx)
    now = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)  # 2 days out
    n = schedule.refresh_weather(sched_conn, 2026, 1, now=now)
    assert n == len(calls) and n > 0
    domes = sched_conn.execute(
        "SELECT COUNT(*) c FROM nfl_games WHERE week=1 AND roof='dome' "
        "AND wind_mph IS NOT NULL").fetchone()["c"]
    assert domes == 0  # the sky is not part of a dome game
    # Fresh rows are TTL-guarded: an immediate second pass fetches nothing.
    calls.clear()
    assert schedule.refresh_weather(sched_conn, 2026, 1, now=now) == 0
    assert calls == []


def test_derive_baselines_reproduces_design_doc_table():
    """The real league's shape (12-team, QB/2RB/2WR/TE/2FLEX/K/DEF) must land
    exactly on the hand-tuned constants — going data-driven changed nothing
    for the league that is live."""
    shape = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX", "K", "DEF"] + ["BN"] * 5
    assert vbd.derive_baselines(12, shape) == VOLS_BASELINES


def test_derive_baselines_scales_with_shape():
    ten_one_flex = vbd.derive_baselines(
        10, ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF"])
    assert ten_one_flex["RB"] == 23 and ten_one_flex["WR"] == 26  # 20 + share of 10
    sflex = vbd.derive_baselines(
        12, ["QB", "RB", "RB", "WR", "WR", "TE", "SUPER_FLEX", "K", "DEF"])
    assert sflex["QB"] == 12 + 9  # superflex feeds the QB pool
