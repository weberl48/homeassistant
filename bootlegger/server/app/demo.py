"""Demo mode: seeds the DB with a labeled synthetic league and runs a simulated
snake draft so every surface can be rehearsed locally with no network and no
real league. Player names are real NFL players (2025-season knowledge — the
fixture is rehearsal scenery, not a projection product; live mode replaces all
of this from the Sleeper API)."""
from __future__ import annotations

import hashlib
import json
import random
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from . import db
from .config import DEMO_ROSTER_POSITIONS, DEMO_SCORING, settings
from .ingest import compute_consensus

# (name, pos, team) in rough positional rank order. Points come from the
# anchor curves below, per rank — labeled synthetic throughout the UI.
FIXTURE: list[tuple[str, str, str]] = [
    # --- QB ---
    ("Josh Allen", "QB", "BUF"), ("Lamar Jackson", "QB", "BAL"),
    ("Jayden Daniels", "QB", "WAS"), ("Jalen Hurts", "QB", "PHI"),
    ("Joe Burrow", "QB", "CIN"), ("Patrick Mahomes", "QB", "KC"),
    ("Baker Mayfield", "QB", "TB"), ("Bo Nix", "QB", "DEN"),
    ("Kyler Murray", "QB", "ARI"), ("C.J. Stroud", "QB", "HOU"),
    ("Justin Herbert", "QB", "LAC"), ("Caleb Williams", "QB", "CHI"),
    ("Jared Goff", "QB", "DET"), ("Brock Purdy", "QB", "SF"),
    ("Dak Prescott", "QB", "DAL"), ("Jordan Love", "QB", "GB"),
    ("Drake Maye", "QB", "NE"), ("Tua Tagovailoa", "QB", "MIA"),
    # --- RB ---
    ("Bijan Robinson", "RB", "ATL"), ("Saquon Barkley", "RB", "PHI"),
    ("Jahmyr Gibbs", "RB", "DET"), ("Christian McCaffrey", "RB", "SF"),
    ("De'Von Achane", "RB", "MIA"), ("Ashton Jeanty", "RB", "LV"),
    ("Derrick Henry", "RB", "BAL"), ("Josh Jacobs", "RB", "GB"),
    ("Jonathan Taylor", "RB", "IND"), ("Chase Brown", "RB", "CIN"),
    ("Kyren Williams", "RB", "LAR"), ("James Cook", "RB", "BUF"),
    ("Kenneth Walker", "RB", "SEA"), ("Breece Hall", "RB", "NYJ"),
    ("Chuba Hubbard", "RB", "CAR"), ("Alvin Kamara", "RB", "NO"),
    ("James Conner", "RB", "ARI"), ("Omarion Hampton", "RB", "LAC"),
    ("Bucky Irving", "RB", "TB"), ("Joe Mixon", "RB", "HOU"),
    ("Aaron Jones", "RB", "MIN"), ("RJ Harvey", "RB", "DEN"),
    ("D'Andre Swift", "RB", "CHI"), ("Tony Pollard", "RB", "TEN"),
    ("Isiah Pacheco", "RB", "KC"), ("David Montgomery", "RB", "DET"),
    ("TreVeyon Henderson", "RB", "NE"), ("Kaleb Johnson", "RB", "PIT"),
    ("Tyrone Tracy", "RB", "NYG"), ("Javonte Williams", "RB", "DAL"),
    ("Brian Robinson", "RB", "WAS"), ("Rhamondre Stevenson", "RB", "NE"),
    ("Quinshon Judkins", "RB", "CLE"), ("Jaylen Warren", "RB", "PIT"),
    ("Zach Charbonnet", "RB", "SEA"), ("Austin Ekeler", "RB", "WAS"),
    ("Najee Harris", "RB", "LAC"), ("Rachaad White", "RB", "TB"),
    ("Jordan Mason", "RB", "MIN"), ("Tyjae Spears", "RB", "TEN"),
    ("Braelon Allen", "RB", "NYJ"), ("Ray Davis", "RB", "BUF"),
    ("Jerome Ford", "RB", "CLE"), ("Nick Chubb", "RB", "HOU"),
    ("J.K. Dobbins", "RB", "DEN"), ("MarShawn Lloyd", "RB", "GB"),
    ("Cam Akers", "RB", "NO"), ("Roschon Johnson", "RB", "CHI"),
    ("Justice Hill", "RB", "BAL"), ("Kendre Miller", "RB", "NO"),
    ("Kimani Vidal", "RB", "LAC"), ("Tank Bigsby", "RB", "JAX"),
    ("Ty Chandler", "RB", "MIN"), ("Trey Benson", "RB", "ARI"),
    # --- WR ---
    ("Ja'Marr Chase", "WR", "CIN"), ("Justin Jefferson", "WR", "MIN"),
    ("CeeDee Lamb", "WR", "DAL"), ("Puka Nacua", "WR", "LAR"),
    ("Malik Nabers", "WR", "NYG"), ("Amon-Ra St. Brown", "WR", "DET"),
    ("Nico Collins", "WR", "HOU"), ("Brian Thomas Jr.", "WR", "JAX"),
    ("A.J. Brown", "WR", "PHI"), ("Drake London", "WR", "ATL"),
    ("Ladd McConkey", "WR", "LAC"), ("Jaxon Smith-Njigba", "WR", "SEA"),
    ("Tee Higgins", "WR", "CIN"), ("Tyreek Hill", "WR", "MIA"),
    ("Davante Adams", "WR", "LAR"), ("Terry McLaurin", "WR", "WAS"),
    ("Garrett Wilson", "WR", "NYJ"), ("Mike Evans", "WR", "TB"),
    ("Marvin Harrison Jr.", "WR", "ARI"), ("Rashee Rice", "WR", "KC"),
    ("Xavier Worthy", "WR", "KC"), ("DK Metcalf", "WR", "PIT"),
    ("Zay Flowers", "WR", "BAL"), ("DJ Moore", "WR", "CHI"),
    ("Courtland Sutton", "WR", "DEN"), ("Calvin Ridley", "WR", "TEN"),
    ("Jaylen Waddle", "WR", "MIA"), ("Jameson Williams", "WR", "DET"),
    ("George Pickens", "WR", "DAL"), ("Tetairoa McMillan", "WR", "CAR"),
    ("Travis Hunter", "WR", "JAX"), ("Rome Odunze", "WR", "CHI"),
    ("Jordan Addison", "WR", "MIN"), ("Jakobi Meyers", "WR", "LV"),
    ("Chris Godwin", "WR", "TB"), ("Stefon Diggs", "WR", "NE"),
    ("Khalil Shakir", "WR", "BUF"), ("Jerry Jeudy", "WR", "CLE"),
    ("Ricky Pearsall", "WR", "SF"), ("Jauan Jennings", "WR", "SF"),
    ("Matthew Golden", "WR", "GB"), ("Chris Olave", "WR", "NO"),
    ("Rashid Shaheed", "WR", "NO"), ("Cooper Kupp", "WR", "SEA"),
    ("Deebo Samuel", "WR", "WAS"), ("Keon Coleman", "WR", "BUF"),
    ("Luther Burden", "WR", "CHI"), ("Emeka Egbuka", "WR", "TB"),
    ("Jayden Reed", "WR", "GB"), ("Michael Pittman", "WR", "IND"),
    ("Hollywood Brown", "WR", "KC"), ("Darnell Mooney", "WR", "ATL"),
    ("Brandin Cooks", "WR", "NO"), ("Adam Thielen", "WR", "CAR"),
    ("Wan'Dale Robinson", "WR", "NYG"), ("Josh Downs", "WR", "IND"),
    ("Romeo Doubs", "WR", "GB"), ("Christian Kirk", "WR", "HOU"),
    ("Cedric Tillman", "WR", "CLE"), ("Marvin Mims", "WR", "DEN"),
    ("Demario Douglas", "WR", "NE"), ("Quentin Johnston", "WR", "LAC"),
    ("Xavier Legette", "WR", "CAR"), ("Dontayvion Wicks", "WR", "GB"),
    # --- TE ---
    ("Brock Bowers", "TE", "LV"), ("Trey McBride", "TE", "ARI"),
    ("George Kittle", "TE", "SF"), ("Sam LaPorta", "TE", "DET"),
    ("T.J. Hockenson", "TE", "MIN"), ("David Njoku", "TE", "CLE"),
    ("Mark Andrews", "TE", "BAL"), ("Evan Engram", "TE", "DEN"),
    ("Travis Kelce", "TE", "KC"), ("Tucker Kraft", "TE", "GB"),
    ("Dalton Kincaid", "TE", "BUF"), ("Colston Loveland", "TE", "CHI"),
    ("Tyler Warren", "TE", "IND"), ("Jake Ferguson", "TE", "DAL"),
    ("Dallas Goedert", "TE", "PHI"), ("Hunter Henry", "TE", "NE"),
    ("Zach Ertz", "TE", "WAS"), ("Pat Freiermuth", "TE", "PIT"),
    ("Isaiah Likely", "TE", "BAL"), ("Cade Otton", "TE", "TB"),
    # --- K ---
    ("Brandon Aubrey", "K", "DAL"), ("Jake Bates", "K", "DET"),
    ("Chris Boswell", "K", "PIT"), ("Cameron Dicker", "K", "LAC"),
    ("Ka'imi Fairbairn", "K", "HOU"), ("Tyler Bass", "K", "BUF"),
    ("Jake Elliott", "K", "PHI"), ("Harrison Butker", "K", "KC"),
    ("Younghoe Koo", "K", "ATL"), ("Evan McPherson", "K", "CIN"),
    ("Chase McLaughlin", "K", "TB"), ("Jason Sanders", "K", "MIA"),
    ("Cairo Santos", "K", "CHI"), ("Wil Lutz", "K", "DEN"),
    # --- DEF ---
    ("Ravens D/ST", "DEF", "BAL"), ("Broncos D/ST", "DEF", "DEN"),
    ("Steelers D/ST", "DEF", "PIT"), ("Eagles D/ST", "DEF", "PHI"),
    ("Texans D/ST", "DEF", "HOU"), ("Vikings D/ST", "DEF", "MIN"),
    ("Packers D/ST", "DEF", "GB"), ("Chiefs D/ST", "DEF", "KC"),
    ("Lions D/ST", "DEF", "DET"), ("Seahawks D/ST", "DEF", "SEA"),
    ("49ers D/ST", "DEF", "SF"), ("Jets D/ST", "DEF", "NYJ"),
]

# Season-points anchor curves (rank -> full-PPR pts), interpolated linearly.
CURVES: dict[str, list[tuple[int, float]]] = {
    "QB": [(1, 400), (6, 358), (12, 322), (18, 296)],
    "RB": [(1, 340), (5, 290), (12, 240), (24, 185), (36, 140), (48, 105)],
    "WR": [(1, 350), (5, 305), (12, 265), (24, 220), (40, 175), (56, 140)],
    "TE": [(1, 250), (3, 200), (6, 165), (12, 130), (20, 100)],
    "K": [(1, 155), (14, 115)],
    "DEF": [(1, 130), (12, 88)],
}

SOURCES = [("ledger", 6.0), ("wire", 9.0), ("gut", 12.0)]  # name, noise sigma
DEMO_DRAFT_ID = "demo-draft-1"
WEEKS = 17


def _interp(anchors: list[tuple[int, float]], rank: int) -> float:
    if rank <= anchors[0][0]:
        return anchors[0][1]
    for (r0, p0), (r1, p1) in zip(anchors, anchors[1:]):
        if rank <= r1:
            return p0 + (p1 - p0) * (rank - r0) / (r1 - r0)
    return anchors[-1][1] - 2.0 * (rank - anchors[-1][0])


def _rng(*key: object) -> random.Random:
    seed = int(hashlib.sha256("|".join(map(str, key)).encode()).hexdigest()[:12], 16)
    return random.Random(seed)


def _pid(name: str) -> str:
    return "demo_" + hashlib.sha256(name.encode()).hexdigest()[:8]


def seed(conn: sqlite3.Connection, force: bool = False) -> bool:
    """Idempotent full seed. Returns True when it (re)seeded."""
    if not force and db.meta_get(conn, "demo_seeded") == "1":
        return False

    now = db.utcnow()
    pos_rank: dict[str, int] = {}
    players_rows, true_pts = [], {}
    for name, pos, team in FIXTURE:
        pos_rank[pos] = pos_rank.get(pos, 0) + 1
        rank = pos_rank[pos]
        pid = _pid(name)
        base = _interp(CURVES[pos], rank)
        true_pts[pid] = base
        players_rows.append({
            "sleeper_id": pid, "name": name, "pos": pos, "team": team,
            "bye": 5 + (_rng("bye", team).randrange(10)),
            "status": "Active", "injury_status": None, "updated_at": now,
        })
    db.upsert_players(conn, players_rows)

    # Per-source season (week 0) and weekly projections, deterministic noise.
    conn.execute("DELETE FROM projections")
    proj_rows = []
    for pid, base in true_pts.items():
        for src, sigma in SOURCES:
            pts = max(5.0, base + _rng("proj", pid, src).gauss(0, sigma))
            proj_rows.append((pid, 0, src, round(pts, 1), round(pts * 0.82, 1), round(pts * 1.22, 1)))
        for week in range(1, WEEKS + 1):
            wk_base = base / WEEKS
            for src, sigma in SOURCES:
                pts = max(0.0, wk_base + _rng("wk", pid, week, src).gauss(0, sigma / 6.5))
                proj_rows.append((pid, week, src, round(pts, 1), round(pts * 0.7, 1), round(pts * 1.35, 1)))
    conn.executemany(
        "INSERT INTO projections(player_id,week,source,pts,floor,ceiling) VALUES(?,?,?,?,?,?)",
        proj_rows,
    )
    conn.commit()

    compute_consensus(conn, week=0)
    compute_consensus(conn, week=1)

    # ADP from draft value ordering (VBD), K/DEF pushed to the tail like real rooms.
    rows = conn.execute(
        "SELECT c.player_id, c.vbd, p.pos FROM consensus c "
        "JOIN players p ON p.sleeper_id=c.player_id WHERE c.week=0"
    ).fetchall()
    def draft_value(r) -> float:
        v = r["vbd"] or 0.0
        return v - 900 if r["pos"] in ("K", "DEF") else v
    ordered = sorted(rows, key=draft_value, reverse=True)
    conn.execute("DELETE FROM adp")
    for i, r in enumerate(ordered):
        overall = i + 1
        jitter = _rng("adp", r["player_id"]).gauss(0, max(1.5, overall * 0.06))
        adp = max(1.0, overall + jitter)
        conn.execute(
            "INSERT INTO adp(player_id,source,adp,stdev,updated_at) VALUES(?,?,?,?,?)",
            (r["player_id"], "demo", round(adp, 1), round(max(2.0, adp * 0.12), 1), now),
        )
    # Market values: monotone in draft value, FantasyCalc-flavored scale.
    conn.execute("DELETE FROM player_values")
    for i, r in enumerate(ordered):
        value = max(200, 10500 * (0.962 ** i))
        trend = _rng("trend", r["player_id"]).gauss(0, value * 0.06)
        conn.execute(
            "INSERT INTO player_values(player_id,redraft_value,trend_30d,updated_at) VALUES(?,?,?,?)",
            (r["player_id"], round(value), round(trend), now),
        )
    conn.commit()

    conn.execute(
        "INSERT OR REPLACE INTO league(league_id,settings_json,scoring_json) VALUES(?,?,?)",
        ("demo-league", json.dumps({
            "name": "The Speakeasy League",
            "roster_positions": DEMO_ROSTER_POSITIONS,
            "teams": settings.teams,
            "faab_budget": settings.faab_budget,
        }), json.dumps(DEMO_SCORING)),
    )

    _seed_rosters(conn, ordered)
    _boost_street(conn)
    _seed_faab_history(conn)
    _seed_slate(conn)
    _seed_wire(conn)
    _reset_draft(conn)
    db.meta_set(conn, "demo_seeded", "1")
    return True


def _seed_slate(conn: sqlite3.Connection) -> None:
    """A week-1 slate with betting lines.

    The demo carried no nfl_games rows at all, which meant the whole schedule
    layer — kickoffs, weather flags, locked slots, and now the market-implied
    game environment — was invisible in the one mode this house rehearses in.
    A surface that cannot be rehearsed locally is a surface nobody has checked,
    and PRODUCT.md commits to the opposite.

    Deliberately partial: four clubs are left UNPRICED so the "no line posted
    yet" path renders on a real board instead of only in a unit test. That is
    the state most of a real season is actually in.
    """
    teams = sorted({r["team"] for r in conn.execute(
        "SELECT DISTINCT team FROM players WHERE team IS NOT NULL")})
    if not teams:
        return
    rng = _rng("slate", "week1")
    unpriced = set(teams[:4])
    rows = []
    # Pair clubs off so every game has two sides that share one total.
    for i in range(0, len(teams) - 1, 2):
        home, away = teams[i], teams[i + 1]
        total = round(rng.uniform(38.0, 52.0), 1)
        spread = round(rng.uniform(-9.5, 9.5), 1)      # positive = home favored
        for team, opp, is_home, sp in ((home, away, 1, spread), (away, home, 0, -spread)):
            priced = team not in unpriced and opp not in unpriced
            rows.append((
                settings.season, 1, team, opp, is_home,
                "2026-09-13T17:00:00+00:00", "outdoors", f"{team} Field", 0,
                sp if priced else None,
                total if priced else None,
                round((total + sp) / 2, 1) if priced else None,
            ))
    conn.executemany(
        "INSERT INTO nfl_games(season,week,team,opponent,is_home,kickoff_utc,roof,"
        "stadium,neutral_site,spread,total_line,implied_total) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(season,week,team) DO UPDATE SET "
        "opponent=excluded.opponent,is_home=excluded.is_home,"
        "kickoff_utc=excluded.kickoff_utc,spread=excluded.spread,"
        "total_line=excluded.total_line,implied_total=excluded.implied_total",
        rows)
    conn.commit()


def _boost_street(conn: sqlite3.Connection) -> None:
    """Post-draft leagues only have a waiver wire because of breakouts. Give a
    few unrostered RB/WRs a September surge so the FAAB engine has a street to
    price during rehearsal."""
    rostered: set[str] = set()
    for r in conn.execute("SELECT players_json FROM rosters"):
        rostered |= set(json.loads(r["players_json"]))
    free = [r for r in conn.execute(
        "SELECT sleeper_id, pos FROM players WHERE pos IN ('RB','WR')")
        if r["sleeper_id"] not in rostered]
    rng = _rng("street")
    for row in rng.sample(free, min(5, len(free))):
        factor = rng.uniform(1.35, 1.8)
        conn.execute("UPDATE projections SET pts = pts * ? WHERE player_id=?",
                     (factor, row["sleeper_id"]))
    conn.commit()
    compute_consensus(conn, week=0)
    compute_consensus(conn, week=1)


SEASON_WEEKS = 14          # the regular season the demo's records come from
NFL_SEASON_WEEKS = 17      # converts a season-long projection to a weekly rate
# Week-to-week scoring noise. Real fantasy runs nearer 25, which over a
# 14-game slate drowns roster quality entirely (r≈0.35) — true to life, and
# unreadable in a demo: the top-ranked seat lands mid-table for no visible
# reason. Dialled to 12 so upsets still happen but the standings and the
# ranking printed beside them tell the same story.
_SEASON_SIGMA = 12.0


def _round_robin_pairs(teams: int, week: int) -> list[tuple[int, int]]:
    """Circle method: seat 1 is fixed and the rest rotate, so with an even
    field every seat plays exactly once a week and nobody sits. Eleven unique
    rounds over twelve seats, then the slate repeats — which is what a real
    14-week fantasy season does too."""
    others = list(range(2, teams + 1))
    r = (week - 1) % (teams - 1)
    rot = others[r:] + others[:r]
    pairs = [(1, rot[0])]
    for i in range(1, teams // 2):
        pairs.append((rot[i], rot[-i]))
    return pairs


def _play_season(teams: int, strength: dict[int, float]) -> dict[int, dict]:
    """Play a deterministic regular season so the League room opens on a real
    table rather than a wall of 0-0. Scores come from each seat's startable
    strength plus weekly noise, so the standings correlate with roster quality
    without being a straight ranking of it."""
    rec = {t: {"w": 0, "l": 0, "t": 0, "pf": 0.0} for t in range(1, teams + 1)}
    for week in range(1, SEASON_WEEKS + 1):
        for home, away in _round_robin_pairs(teams, week):
            hs = max(0.0, strength[home] + _rng("game", week, home, away).gauss(0, _SEASON_SIGMA))
            aws = max(0.0, strength[away] + _rng("game", week, away, home).gauss(0, _SEASON_SIGMA))
            rec[home]["pf"] += hs
            rec[away]["pf"] += aws
            if hs > aws:
                rec[home]["w"] += 1
                rec[away]["l"] += 1
            elif aws > hs:
                rec[away]["w"] += 1
                rec[home]["l"] += 1
            else:
                rec[home]["t"] += 1
                rec[away]["t"] += 1
    return rec


OWNER_NAMES = ["Front Room", "Coat Check", "House Band", "The Chemist", "Card Table",
               "Projectionist", "You", "Rum Runner", "The Doorman", "Night Shift",
               "Green Lamp", "Last Call"]


ROSTER_SIZE = 14  # leaves a real free-agent pool so the waiver engine has a street


def _seed_rosters(conn: sqlite3.Connection, ordered_rows) -> None:
    """Season-mode rosters (independent of the draft sim): snake-fill 12 rosters
    from ADP order, then bend roster 7 (you) into a deliberately suboptimal
    Sunday: one starter ruled Out, one clear bench upgrade left sitting."""
    pool = [r["player_id"] for r in ordered_rows]
    pos_of = {r["sleeper_id"]: r["pos"] for r in conn.execute("SELECT sleeper_id,pos FROM players")}
    teams = settings.teams
    rosters: dict[int, list[str]] = {i: [] for i in range(1, teams + 1)}
    caps = {"QB": 2, "TE": 2, "K": 1, "DEF": 1}
    order = list(range(1, teams + 1))
    rnd, placed_any = 0, True
    while placed_any and any(len(r) < ROSTER_SIZE for r in rosters.values()):
        placed_any = False
        seq = order if rnd % 2 == 0 else order[::-1]
        for team_id in seq:
            if len(rosters[team_id]) >= ROSTER_SIZE:
                continue
            # take the best available this roster can still use
            for j, pid in enumerate(pool):
                if pid is None:
                    continue
                pos = pos_of[pid]
                have = sum(1 for q in rosters[team_id] if pos_of[q] == pos)
                if pos in caps and have >= caps[pos]:
                    continue
                need_kdef = len(rosters[team_id]) >= ROSTER_SIZE - 2 and (
                    not any(pos_of[q] == "K" for q in rosters[team_id])
                    or not any(pos_of[q] == "DEF" for q in rosters[team_id]))
                if need_kdef and pos not in ("K", "DEF"):
                    continue
                rosters[team_id].append(pid)
                pool[j] = None
                placed_any = True
                break
        pool = [p for p in pool if p is not None]
        rnd += 1

    now = db.utcnow()
    week1 = {r["player_id"]: r["pts_robust"] or 0.0 for r in
             conn.execute("SELECT player_id, pts_robust FROM consensus WHERE week=1")}
    from .engines.lineup import PlayerProj, optimize
    # Pass 1: the week-1 lineup each seat would field, and its weekly scoring
    # power for the season just played.
    season_pts = {r["player_id"]: r["pts_robust"] or 0.0 for r in
                  conn.execute("SELECT player_id, pts_robust FROM consensus WHERE week=0")}
    lineups: dict[int, list[str]] = {}
    strength: dict[int, float] = {}
    for team_id, players in rosters.items():
        projs = [PlayerProj(pid, pos_of[pid], week1.get(pid, 0.0)) for pid in players]
        lineups[team_id] = [p.player_id for _, p in
                            optimize(projs, DEMO_ROSTER_POSITIONS).assignment]
        # Scoring power comes off the SEASON book, not week 1 — The League ranks
        # seats on season points, and a record simulated from a different basis
        # would contradict the ranking printed beside it. Taken from the optimal
        # lineup, so my record isn't punished for the deliberately-wrong week-1
        # lineup that the week card exists to fix.
        season_best = optimize([PlayerProj(pid, pos_of[pid], season_pts.get(pid, 0.0))
                                for pid in players], DEMO_ROSTER_POSITIONS)
        strength[team_id] = season_best.total / NFL_SEASON_WEEKS

    season = _play_season(teams, strength)

    # Pass 2: write the seats, each carrying the season it just played.
    for team_id, players in rosters.items():
        starters = lineups[team_id]
        if team_id == settings.my_roster_id:
            starters = _spoil_my_lineup(conn, players, starters, pos_of)
        rec = season[team_id]
        conn.execute(
            "INSERT OR REPLACE INTO rosters(roster_id,owner,players_json,starters_json,updated_at,"
            "wins,losses,ties,fpts) VALUES(?,?,?,?,?,?,?,?,?)",
            (team_id, OWNER_NAMES[(team_id - 1) % len(OWNER_NAMES)],
             json.dumps(players), json.dumps(starters), now,
             rec["w"], rec["l"], rec["t"], round(rec["pf"], 2)),
        )
    for w in (1,):
        for team_id in range(1, teams + 1):
            opp = teams + 1 - team_id
            conn.execute(
                "INSERT OR REPLACE INTO matchups(week,roster_id,opp_roster_id,proj_for,proj_against) "
                "VALUES(?,?,?,?,?)",
                (w, team_id, opp,
                 round(sum(week1.get(p, 0) for p in json.loads(conn.execute(
                     "SELECT starters_json FROM rosters WHERE roster_id=?", (team_id,)
                 ).fetchone()["starters_json"])), 1),
                 0.0),
            )
    conn.commit()


def _spoil_my_lineup(conn, players: list[str], starters: list[str], pos_of) -> list[str]:
    """Make Sunday interesting: rule one of my starting WRs Out, and bench the
    best non-starting WR/RB's superior — i.e., swap a bench player into the
    starters' place so the optimizer has something material to say."""
    bench = [p for p in players if p not in starters]
    # Rule the second-best starting WR Out (keeps the injury path exercised).
    wr_starters = [p for p in starters if pos_of[p] == "WR"]
    if len(wr_starters) >= 2:
        conn.execute("UPDATE players SET injury_status='Out' WHERE sleeper_id=?",
                     (wr_starters[1],))
    # Start a weaker bench RB over the better starting RB (pure value miss).
    rb_starters = [p for p in starters if pos_of[p] == "RB"]
    rb_bench = [p for p in bench if pos_of[p] == "RB"]
    if rb_starters and rb_bench:
        i = starters.index(rb_starters[-1])
        starters = list(starters)
        starters[i] = rb_bench[0]
    conn.commit()
    return starters


def _seed_faab_history(conn: sqlite3.Connection) -> None:
    """Two seasons of plausible FAAB bids so the percentile model has a spine."""
    conn.execute("DELETE FROM transactions")
    rng = _rng("faab-history")
    n = 0
    for season_tag in ("2024", "2025"):
        for week in range(1, 15):
            for _ in range(rng.randrange(2, 6)):
                tier = rng.choices(["hot", "solid", "dart"], weights=[1, 3, 5])[0]
                faab = {"hot": rng.randrange(18, 52), "solid": rng.randrange(6, 22),
                        "dart": rng.randrange(1, 7)}[tier]
                n += 1
                conn.execute(
                    "INSERT INTO transactions(txn_id,week,type,adds_json,drops_json,faab,status,ts) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (f"demo-{season_tag}-{n}", week, "waiver",
                     json.dumps({"tier": tier}), json.dumps({}), faab, "complete",
                     f"{season_tag}-10-01T00:00:00+00:00"),
                )
    conn.commit()


# ---------------------------------------------------------------------------
# Simulated live draft
# ---------------------------------------------------------------------------


def _seed_wire(conn: sqlite3.Connection) -> None:
    """A synthetic wire so the news surfaces rehearse offline.

    Demo mode's contract is that nothing needs the network, and the wire is no
    exception. The items are written against the seeded rosters so all three
    audiences appear: one of your starters in trouble, one of your bench men,
    somebody else's man leaving for the season (the waiver window), and a
    street name nobody holds. Severity is left to engines/wire.py — the seed
    writes headlines in RotoWire's house style and lets the real classifier
    grade them, so the demo exercises the shipping code path.
    """
    from .engines import wire as wire_engine
    conn.execute("DELETE FROM news WHERE source='demo'")
    rosters = {r["roster_id"]: json.loads(r["players_json"] or "[]")
               for r in conn.execute("SELECT roster_id, players_json FROM rosters")}
    mine = rosters.get(settings.my_roster_id, [])
    my_row = conn.execute("SELECT starters_json FROM rosters WHERE roster_id=?",
                          (settings.my_roster_id,)).fetchone()
    starters = json.loads(my_row["starters_json"] or "[]") if my_row else []
    theirs = [p for rid, ids in rosters.items() if rid != settings.my_roster_id
              for p in ids]
    names = {r["sleeper_id"]: r["name"] for r in
             conn.execute("SELECT sleeper_id, name FROM players")}
    street = [r["sleeper_id"] for r in conn.execute(
        "SELECT sleeper_id FROM players LIMIT 400")
        if r["sleeper_id"] not in set(mine) | set(theirs)]

    def pick(pool: list[str], n: int) -> list[str]:
        return [p for p in pool if p in names][:n]

    script: list[tuple[str, str, str]] = []
    for pid in pick(starters, 1):
        script.append((pid, "Questionable for Sunday",
                       f"{names[pid].split()[-1]} (hamstring) is listed as questionable "
                       "and is a game-time decision, the team's beat reporter says."))
    for pid in pick([p for p in mine if p not in starters], 1):
        script.append((pid, "Limited in Wednesday's practice",
                       f"{names[pid].split()[-1]} (ankle) was limited at practice."))
    for pid in pick(theirs, 1):
        script.append((pid, "Placed on injured reserve",
                       f"{names[pid].split()[-1]} was placed on injured reserve "
                       "Tuesday and will miss at least four games."))
    for pid in pick(street, 1):
        script.append((pid, "Expected to start Sunday",
                       f"{names[pid].split()[-1]} will start with the job open."))

    now = datetime.now(timezone.utc)
    rows = []
    for i, (pid, headline, body) in enumerate(script):
        published = (now - timedelta(hours=i + 1)).isoformat(timespec="seconds")
        rows.append((
            f"demo-wire-{i}", 900000 + i, "demo", pid, names[pid], headline, body,
            "https://www.rotowire.com/football/", wire_engine.severity(headline, body),
            wire_engine.ailment(body),
            1 if wire_engine.is_departure(headline, body) else 0,
            published, db.utcnow(),
        ))
    conn.executemany(
        "INSERT OR REPLACE INTO news(guid,seq,source,player_id,name_raw,headline,body,"
        "link,severity,ailment,departure,published_at,fetched_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    db.meta_set(conn, "wire_last_ok", db.utcnow())
    conn.commit()


def _reset_draft(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM draft_picks WHERE draft_id=?", (DEMO_DRAFT_ID,))
    conn.execute(
        "INSERT OR REPLACE INTO drafts(draft_id,status,settings_json,updated_at) VALUES(?,?,?,?)",
        (DEMO_DRAFT_ID, "drafting",
         json.dumps({"teams": settings.teams, "rounds": settings.rounds,
                     "slot": settings.my_roster_id,
                     # The seating plan, so the room strip is rehearsable
                     # locally. Live, Sleeper supplies this once the order is
                     # drawn; a surface that only exists against the real
                     # league is a surface nobody has looked at.
                     "slot_to_roster_id": {str(i): i for i in
                                           range(1, settings.teams + 1)}}),
         db.utcnow()),
    )
    db.meta_set(conn, "demo_draft_next_tick", str(time.time() + 2.0))
    conn.commit()


def reset_draft(conn: sqlite3.Connection) -> None:
    _reset_draft(conn)


def current_pick_no(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(pick_no),0) AS n FROM draft_picks WHERE draft_id=?",
        (DEMO_DRAFT_ID,),
    ).fetchone()
    return row["n"] + 1


def slot_for_pick(pick_no: int) -> int:
    teams = settings.teams
    rnd = (pick_no - 1) // teams + 1
    pos = (pick_no - 1) % teams + 1
    return pos if rnd % 2 == 1 else teams - pos + 1


def _picked_ids(conn) -> set[str]:
    return {r["player_id"] for r in conn.execute(
        "SELECT player_id FROM draft_picks WHERE draft_id=?", (DEMO_DRAFT_ID,))}


def _sim_pick_for_slot(conn: sqlite3.Connection, slot: int, pick_no: int) -> str | None:
    """Opponent AI: lowest ADP with light noise, positional caps, K/DEF late."""
    rnd_no = (pick_no - 1) // settings.teams + 1
    taken = _picked_ids(conn)
    mine = [r["player_id"] for r in conn.execute(
        "SELECT player_id FROM draft_picks WHERE draft_id=? AND draft_slot=?",
        (DEMO_DRAFT_ID, slot))]
    pos_of = {r["sleeper_id"]: r["pos"] for r in conn.execute("SELECT sleeper_id,pos FROM players")}
    counts: dict[str, int] = {}
    for p in mine:
        counts[pos_of[p]] = counts.get(pos_of[p], 0) + 1
    caps = {"QB": 3, "TE": 3, "K": 1, "DEF": 1}
    last_rounds = rnd_no >= settings.rounds - 1
    need = [p for p in ("K", "DEF") if counts.get(p, 0) == 0] if last_rounds else []
    rng = _rng("simpick", DEMO_DRAFT_ID, pick_no)
    best_pid, best_key = None, None
    for r in conn.execute(
        "SELECT a.player_id, a.adp FROM adp a JOIN players p ON p.sleeper_id=a.player_id"
    ).fetchall():
        pid = r["player_id"]
        if pid in taken:
            continue
        pos = pos_of[pid]
        if counts.get(pos, 0) >= caps.get(pos, 99):
            continue
        if pos in ("K", "DEF") and not last_rounds:
            continue
        if need and pos not in need:
            continue
        key = r["adp"] + rng.gauss(0, 4.0)
        if best_key is None or key < best_key:
            best_pid, best_key = pid, key
    return best_pid


def record_pick(conn: sqlite3.Connection, pick_no: int, player_id: str) -> None:
    slot = slot_for_pick(pick_no)
    conn.execute(
        "INSERT OR REPLACE INTO draft_picks(draft_id,pick_no,round,draft_slot,roster_id,player_id,ts) "
        "VALUES(?,?,?,?,?,?,?)",
        (DEMO_DRAFT_ID, pick_no, (pick_no - 1) // settings.teams + 1,
         slot, slot, player_id, db.utcnow()),
    )
    conn.commit()


# How long a fast-forward holds the sim still afterwards. The rehearsal has to
# read a board that is not moving under it: the demo's own poller ticks every
# few seconds, and a check that reloads, counts seats, then asserts would race
# a pick that landed in between.
FF_HOLD_SECONDS = 30.0


def fast_forward(conn: sqlite3.Connection, to_pick: int, suggest_for_me) -> int:
    """Drive the simulated draft to `to_pick` with no clock, and hold it there.

    The demo resets to pick 1 and creeps forward on a timer, so every audit run
    the gate has ever made has seen the same board: round one, going out, seat
    one on the clock, no column yet emptied. That is the one shape draft night
    is NOT — the snake never reverses in round one, nobody has been crossed
    off, and the clockplate has never had to name anyone. Those paths ship
    tonight having never rendered. This exists so they can be rehearsed.
    """
    total = settings.teams * settings.rounds
    to_pick = max(1, min(int(to_pick), total + 1))
    # Land ON the requested pick, wherever the sim happens to be. A demo DB
    # survives restarts, so by the time a gate asks for round two the sim has
    # usually run the board out to 180 and "advance" would be a silent no-op —
    # which reads exactly like a passing rehearsal of nothing.
    if current_pick_no(conn) > to_pick:
        _reset_draft(conn)
    made = 0
    while current_pick_no(conn) < to_pick:
        pick_no = current_pick_no(conn)
        slot = slot_for_pick(pick_no)
        pid = (suggest_for_me() if slot == settings.my_roster_id
               else _sim_pick_for_slot(conn, slot, pick_no))
        if pid is None:            # pool exhausted under the sim's constraints
            break
        record_pick(conn, pick_no, pid)
        made += 1
    # Same heartbeat the live poller writes, so the board does not banner a
    # stale wire the moment it lands on the fast-forwarded state.
    conn.execute("UPDATE drafts SET updated_at=? WHERE draft_id=?",
                 (db.utcnow(), DEMO_DRAFT_ID))
    db.meta_set(conn, "demo_draft_next_tick", str(time.time() + FF_HOLD_SECONDS))
    conn.commit()
    return made


def tick(conn: sqlite3.Connection, suggest_for_me) -> bool:
    """Advance the simulated draft when its clock has elapsed. `suggest_for_me`
    is a callable returning my best pick's player_id (the brain drafts my team
    so the rehearsal shows the suggestion engine's taste). Returns True when a
    pick was made."""
    row = conn.execute("SELECT status FROM drafts WHERE draft_id=?", (DEMO_DRAFT_ID,)).fetchone()
    if not row or row["status"] != "drafting":
        return False
    # The sim is the demo's poller, so every visit refreshes drafts.updated_at —
    # the freshness heartbeat the board watches — exactly as
    # etl_draft_picks(full=False) does on each live poll. It has to happen here
    # rather than in record_pick: the sim idles up to demo_my_clock_seconds on
    # my clock, and a heartbeat that only moved on a landed pick would banner a
    # stale wire through every one of those pauses.
    conn.execute("UPDATE drafts SET updated_at=? WHERE draft_id=?",
                 (db.utcnow(), DEMO_DRAFT_ID))
    conn.commit()
    next_tick = float(db.meta_get(conn, "demo_draft_next_tick", "0") or 0)
    if time.time() < next_tick:
        return False
    pick_no = current_pick_no(conn)
    total = settings.teams * settings.rounds
    if pick_no > total:
        conn.execute("UPDATE drafts SET status='complete', updated_at=? WHERE draft_id=?",
                     (db.utcnow(), DEMO_DRAFT_ID))
        conn.commit()
        return False
    slot = slot_for_pick(pick_no)
    pid = suggest_for_me() if slot == settings.my_roster_id else _sim_pick_for_slot(conn, slot, pick_no)
    if pid is None:  # pool exhausted for constraints; draft over
        conn.execute("UPDATE drafts SET status='complete', updated_at=? WHERE draft_id=?",
                     (db.utcnow(), DEMO_DRAFT_ID))
        conn.commit()
        return False
    record_pick(conn, pick_no, pid)
    nxt = current_pick_no(conn)
    if nxt <= total and slot_for_pick(nxt) == settings.my_roster_id:
        delay = settings.demo_my_clock_seconds  # leave you "on the clock" to watch
    else:
        delay = settings.demo_pick_seconds
    db.meta_set(conn, "demo_draft_next_tick", str(time.time() + delay))
    return True
