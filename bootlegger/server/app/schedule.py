"""The NFL schedule layer (the long-deferred "Phase 2"): kickoff times, byes,
and game-day weather. One nflverse fetch a night populates nfl_games; the
time-based don't-act rules, per-player locks, opponent context, and the
weather flag all read from it. Weather is fetched keyless from Open-Meteo for
outdoor home games inside the forecast window and refreshed on a TTL by the
season loop — never on the request path."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import db
from .sources import fetch_nflverse_games, fetch_openmeteo_hour

EASTERN = ZoneInfo("America/New_York")

# Stadium coordinates by HOME team (close enough for point weather). Neutral-
# site games (internationals) skip weather — coords would be wrong.
STADIUM_COORDS: dict[str, tuple[float, float]] = {
    "ARI": (33.5276, -112.2626), "ATL": (33.7554, -84.4009),
    "BAL": (39.2780, -76.6227),  "BUF": (42.7738, -78.7870),
    "CAR": (35.2258, -80.8528),  "CHI": (41.8623, -87.6167),
    "CIN": (39.0955, -84.5161),  "CLE": (41.5061, -81.6995),
    "DAL": (32.7473, -97.0945),  "DEN": (39.7439, -105.0201),
    "DET": (42.3400, -83.0456),  "GB":  (44.5013, -88.0622),
    "HOU": (29.6847, -95.4107),  "IND": (39.7601, -86.1639),
    "JAX": (30.3239, -81.6373),  "KC":  (39.0489, -94.4839),
    "LAR": (33.9535, -118.3392), "LAC": (33.9535, -118.3392),
    "LV":  (36.0909, -115.1833), "MIA": (25.9580, -80.2389),
    "MIN": (44.9735, -93.2575),  "NE":  (42.0909, -71.2643),
    "NO":  (29.9511, -90.0812),  "NYG": (40.8128, -74.0742),
    "NYJ": (40.8128, -74.0742),  "PHI": (39.9008, -75.1675),
    "PIT": (40.4468, -80.0158),  "SEA": (47.5952, -122.3316),
    "SF":  (37.4030, -121.9700), "TB":  (27.9759, -82.5033),
    "TEN": (36.1665, -86.7713),  "WAS": (38.9078, -76.8645),
}

# roof values in games.csv: outdoors | open | closed | dome. Weather matters
# only where the sky is part of the game.
WEATHER_ROOFS = {"outdoors", "open"}
WEATHER_TTL_H = 3.0        # refresh cadence inside game week
WEATHER_HORIZON_DAYS = 10  # Open-Meteo forecasts ~16 days; stay well inside


def _kickoff_utc(gameday: str, gametime: str) -> str | None:
    """games.csv times are US/Eastern; store ISO UTC. Missing time = None."""
    if not gameday or not gametime:
        return None
    try:
        local = datetime.strptime(f"{gameday} {gametime}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return local.replace(tzinfo=EASTERN).astimezone(timezone.utc).isoformat(timespec="seconds")


def etl_schedule(conn: sqlite3.Connection, season: int) -> int:
    """Write two team-perspective rows per game. Weather columns are preserved
    across refreshes (same-key UPSERT keeps them)."""
    games = fetch_nflverse_games(season)
    if len(games) < 200:  # a real season is 272; a short read is a bad read
        raise RuntimeError(f"nflverse returned {len(games)} REG games; refusing")
    rows = []
    for g in games:
        ko = _kickoff_utc(g["gameday"], g["gametime"])
        neutral = 1 if g["location"] != "Home" else 0
        # Vegas, from each team's own side of the line: positive spread = this
        # team favored; implied_total = (total + spread)/2 is the market's
        # expectation of THIS team's score — the opponent-strength signal.
        sl, tl = g.get("spread_line"), g.get("total_line")
        for team, opp, home in ((g["home_team"], g["away_team"], 1),
                                (g["away_team"], g["home_team"], 0)):
            spread = sl if home else (-sl if sl is not None else None)
            implied = round((tl + spread) / 2, 1) if (tl is not None and spread is not None) else None
            rows.append((season, g["week"], team, opp, home, ko, g["roof"],
                         g["stadium"], neutral, spread, tl, implied))
    # Delete-then-write, not upsert: a postponed game moves to another week's
    # key, and a stale row under the old (season, week, team) key would hide
    # the real off-week and mark its players locked forever. The credibility
    # guard above means we never trade good rows for a bad read; the weather
    # columns this drops are refilled by the very next refresh_weather.
    conn.execute("DELETE FROM nfl_games WHERE season=?", (season,))
    conn.executemany(
        "INSERT INTO nfl_games(season,week,team,opponent,is_home,kickoff_utc,roof,stadium,"
        "neutral_site,spread,total_line,implied_total) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(season,week,team) DO UPDATE SET opponent=excluded.opponent,"
        "is_home=excluded.is_home,kickoff_utc=excluded.kickoff_utc,"
        "roof=excluded.roof,stadium=excluded.stadium,neutral_site=excluded.neutral_site,"
        "spread=excluded.spread,total_line=excluded.total_line,"
        "implied_total=excluded.implied_total",
        rows,
    )
    conn.commit()
    return len(games)


def bye_weeks(conn: sqlite3.Connection, season: int) -> dict[str, int]:
    """{team: bye_week} — the regular-season week a team has no game."""
    weeks = [r["week"] for r in conn.execute(
        "SELECT DISTINCT week FROM nfl_games WHERE season=? ORDER BY week", (season,))]
    out: dict[str, int] = {}
    for r in conn.execute(
            "SELECT team, GROUP_CONCAT(week) gw FROM nfl_games WHERE season=? GROUP BY team",
            (season,)):
        have = {int(w) for w in (r["gw"] or "").split(",") if w}
        missing = [w for w in weeks if w not in have]
        if len(missing) == 1:  # anything else means a half-loaded schedule
            out[r["team"]] = missing[0]
    return out


def backfill_byes(conn: sqlite3.Connection, season: int) -> int:
    """Schedule-derived byes onto players.bye — authoritative over the FP-ECR
    scrape (which stays as the fallback for anything the schedule misses)."""
    byes = bye_weeks(conn, season)
    n = 0
    for team, wk in byes.items():
        n += conn.execute(
            "UPDATE players SET bye=? WHERE team=? AND (bye IS NULL OR bye<>?)",
            (wk, team, wk)).rowcount
    conn.commit()
    return n


def game_for(conn: sqlite3.Connection, team: str | None, week: int,
             season: int | None = None) -> sqlite3.Row | None:
    if not team:
        return None
    q = "SELECT * FROM nfl_games WHERE team=? AND week=?"
    args: list = [team, week]
    if season is not None:
        q += " AND season=?"
        args.append(season)
    return conn.execute(q + " ORDER BY season DESC LIMIT 1", args).fetchone()


def kickoff_hours_away(conn: sqlite3.Connection, team: str | None, week: int,
                       now: datetime | None = None,
                       season: int | None = None) -> float | None:
    """Hours until the team's kickoff; negative once the game is underway.
    None when the schedule has no timed game (rules then fail toward the
    human-in-the-loop path, as before)."""
    g = game_for(conn, team, week, season=season)
    if not g or not g["kickoff_utc"]:
        return None
    ko = datetime.fromisoformat(g["kickoff_utc"])
    now = now or datetime.now(timezone.utc)
    return (ko - now).total_seconds() / 3600.0


def refresh_weather(conn: sqlite3.Connection, season: int, week: int,
                    now: datetime | None = None, ttl_h: float = WEATHER_TTL_H) -> int:
    """Fill wind/precip/temp for this week's outdoor home games within the
    forecast horizon. TTL-guarded and failure-tolerant per game — a dead
    weather API must never take the season loop down with it."""
    now = now or datetime.now(timezone.utc)
    n = 0
    rows = conn.execute(
        "SELECT * FROM nfl_games WHERE season=? AND week=? AND is_home=1 "
        "AND neutral_site=0 AND kickoff_utc IS NOT NULL", (season, week)).fetchall()
    for g in rows:
        if g["roof"] not in WEATHER_ROOFS or g["team"] not in STADIUM_COORDS:
            continue
        ko = datetime.fromisoformat(g["kickoff_utc"])
        hours_out = (ko - now).total_seconds() / 3600.0
        if hours_out < -4 or hours_out > WEATHER_HORIZON_DAYS * 24:
            continue
        if g["weather_at"]:
            age_h = (now - datetime.fromisoformat(g["weather_at"])).total_seconds() / 3600.0
            if age_h < ttl_h:
                continue
        lat, lon = STADIUM_COORDS[g["team"]]
        try:
            wx = fetch_openmeteo_hour(lat, lon, ko.strftime("%Y-%m-%d"), ko.hour)
        except Exception:
            # Stamp the attempt (values stay NULL) so a struggling weather API
            # is retried on the TTL, not on every scan tick of game day.
            conn.execute(
                "UPDATE nfl_games SET weather_at=? WHERE season=? AND week=? AND team=?",
                (now.isoformat(timespec="seconds"), season, week, g["team"]))
            continue
        conn.execute(
            "UPDATE nfl_games SET wind_mph=?, precip_prob=?, temp_f=?, weather_at=? "
            "WHERE season=? AND week=? AND team IN (?,?)",
            # stamped with the caller's clock, not the wall clock — the TTL
            # comparison must be against the same `now` it will be read with
            (wx["wind_mph"], wx["precip_prob"], wx["temp_f"],
             now.isoformat(timespec="seconds"),
             season, week, g["team"], g["opponent"]))
        n += 1
    conn.commit()
    return n


def weather_flags(g: sqlite3.Row | None, wind_min: float = 20.0,
                  precip_min: float = 70.0) -> list[str]:
    """Human-readable weather concerns for a game row ([] = no flag)."""
    if not g:
        return []
    out = []
    if g["wind_mph"] is not None and g["wind_mph"] >= wind_min:
        out.append(f"{round(g['wind_mph'])} mph wind")
    if g["precip_prob"] is not None and g["precip_prob"] >= precip_min:
        out.append(f"{round(g['precip_prob'])}% precip")
    return out
