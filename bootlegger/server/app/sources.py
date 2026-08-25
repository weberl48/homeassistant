"""External projection/market/consensus sources: FFC ADP, FantasyCalc values,
and FantasyPros (keyless ECR consensus ranks; key-gated point projections).
FantasyCalc rows carry a sleeperId, so they join directly; the others join on
normalized name+position."""
from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

FFC_URL = "https://fantasyfootballcalculator.com/api/v1/adp/ppr"
FANTASYCALC_URL = "https://api.fantasycalc.com/values/current"
# The cheat-sheet pages serve the FULL ecrData JSON to anonymous visitors —
# only the point-projection API needs a key. One GET per nightly is a lighter
# touch than a browser visit.
FP_ECR_URLS = {
    "ppr": "https://www.fantasypros.com/nfl/rankings/ppr-cheatsheets.php",
    "half": "https://www.fantasypros.com/nfl/rankings/half-point-ppr-cheatsheets.php",
    "std": "https://www.fantasypros.com/nfl/rankings/cheatsheets.php",
}
FP_PROJECTIONS_URL = "https://api.fantasypros.com/public/v2/json/nfl/{year}/projections"
FP_UA = "Mozilla/5.0 (X11; Linux x86_64) bootlegger/0.1"  # page scrape only
# Their WAF sporadically 403s browser-ish UAs on the API host; an honest tool
# UA passes cleanly (verified 2026-08-24: 6/6 vs random refusals).
FP_API_UA = "bootlegger/0.1 (personal use)"
# ESPN's fantasy API is keyless; leaguedefaults/3 is their PPR default league.
ESPN_PROJ_URL = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{year}/segments/0/leaguedefaults/3"
ESPN_POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DEF"}

# The two ffanalytics-lineage scrape sources that still serve plain
# server-rendered tables (Yahoo needs OAuth, NFL.com's API is dead,
# NumberFire went JS-only under FanDuel).
CBS_URL = "https://www.cbssports.com/fantasy/football/stats/{pos}/{year}/season/projections/ppr/"
FFT_URL = ("https://www.fftoday.com/rankings/playerproj.php"
           "?Season={year}&PosID={posid}&LeagueID=1&cur_page={page}")
# FFToday stat columns per position (after Team, Bye; FPts trails and is
# IGNORED — LeagueID=1 is not our scoring; points are computed by the caller
# from the league's own scoring settings, the ffanalytics approach).
FFT_COLS = {
    10: ("QB", ["cmp", "pass_att", "pass_yd", "pass_td", "pass_int",
                "rush_att", "rush_yd", "rush_td"]),
    20: ("RB", ["rush_att", "rush_yd", "rush_td", "rec", "rec_yd", "rec_td"]),
    30: ("WR", ["rec", "rec_yd", "rec_td", "rush_att", "rush_yd", "rush_td"]),
    40: ("TE", ["rec", "rec_yd", "rec_td"]),
}


def normalize_name(name: str) -> str:
    """'Amon-Ra St. Brown Jr.' -> 'amonra st brown' — good enough to join ADP rows."""
    n = name.lower()
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", n)
    n = re.sub(r"[^a-z ]", "", n)
    return re.sub(r"\s+", " ", n).strip()


def fetch_ffc_adp(teams: int = 12, year: int = 2026, timeout: float = 15.0) -> list[dict[str, Any]]:
    """Rows: {name, position, team, adp, stdev}. FFC 'high'/'low' give the range;
    stdev falls back to (high-low)/4 when absent."""
    r = httpx.get(FFC_URL, params={"teams": teams, "year": year}, timeout=timeout,
                  headers={"User-Agent": "bootlegger/0.1"})
    r.raise_for_status()
    out = []
    for p in r.json().get("players", []):
        stdev = p.get("stdev")
        if stdev is None and p.get("high") and p.get("low"):
            stdev = (float(p["low"]) - float(p["high"])) / 4.0
        out.append({
            "name": p.get("name", ""),
            "position": p.get("position", ""),
            "team": p.get("team"),
            "adp": float(p.get("adp", 0) or 0),
            "stdev": float(stdev) if stdev else None,
        })
    return out


def fetch_fp_ecr(scoring: str = "ppr", timeout: float = 20.0) -> dict[str, Any]:
    """FantasyPros expert-consensus ranks from the cheat-sheet page's embedded
    ecrData blob. Returns {"experts": n, "players": [{name, position, team,
    bye, rank_ave, rank_std}]} — rank_ave/rank_std are the mean and stdev of
    the expert ranks, which map straight onto the survival model's (adp, stdev)."""
    r = httpx.get(FP_ECR_URLS.get(scoring, FP_ECR_URLS["ppr"]), timeout=timeout,
                  headers={"User-Agent": FP_UA}, follow_redirects=True)
    r.raise_for_status()
    m = re.search(r"var ecrData\s*=\s*(\{.*?\});", r.text, re.S)
    if not m:
        return {"experts": 0, "players": []}
    data = json.loads(m.group(1))
    out = []
    for p in data.get("players", []):
        pos = (p.get("player_position_id") or "").upper()
        if pos == "DST":
            pos = "DEF"
        try:
            ave = float(p.get("rank_ave") or 0)
        except (TypeError, ValueError):
            continue
        if not ave:
            continue
        try:
            std = float(p.get("rank_std") or 0) or None
        except (TypeError, ValueError):
            std = None
        bye = str(p.get("player_bye_week") or "")
        out.append({
            "name": p.get("player_name", ""),
            "position": pos,
            "team": p.get("player_team_id"),
            "bye": int(bye) if bye.isdigit() else None,
            "rank_ave": ave,
            "rank_std": std,
        })
    return {"experts": data.get("total_experts", 0), "players": out}


def fetch_fantasypros_projections(api_key: str, year: int, scoring: str = "PPR",
                                  week: int = 0,
                                  timeout: float = 20.0) -> dict[str, Any]:
    """FantasyPros aggregate point projections via the public v2 API (personal
    key from fantasypros.com/apis). Verified shape 2026-08-24: every row
    carries stats.points (STD), stats.points_ppr, stats.points_half — the
    scoring query param does NOT reshape the payload, so pick the field here.
    Week 0 = season; a short pause between positions stays under burst limits."""
    field = {"PPR": "points_ppr", "HALF": "points_half", "STD": "points"}.get(scoring, "points_ppr")
    out = []
    failed = []
    # Their gateway answers sporadic 403s (~1 in 5 observed on a fresh key,
    # any position, any UA — edge-node key propagation). Retry each position
    # independently and never let one position sink the batch.
    for i, pos in enumerate(("QB", "RB", "WR", "TE", "K", "DST")):
        if i:
            time.sleep(0.8)
        r = None
        for backoff in (0, 2, 5, 10):
            if backoff:
                time.sleep(backoff)
            r = httpx.get(FP_PROJECTIONS_URL.format(year=year),
                          params={"position": pos, "week": week, "scoring": scoring},
                          headers={"x-api-key": api_key, "User-Agent": FP_API_UA},
                          timeout=timeout)
            if r.status_code == 200:
                break
        if r is None or r.status_code != 200:
            failed.append(pos)
            continue
        for p in r.json().get("players", []):
            stats = p.get("stats") or {}
            pts = stats.get(field) or stats.get("points")
            if not pts:
                continue
            out.append({
                "name": p.get("name", ""),
                "position": "DEF" if pos == "DST" else pos,
                "team": p.get("team_id"),
                "pts": float(pts),
            })
    return {"rows": out, "failed": failed}


def fetch_espn_projections(year: int, week: int = 0,
                           timeout: float = 25.0) -> list[dict[str, Any]]:
    """ESPN fantasy-point projections (PPR default league), keyless. Stat row
    ids: season total = f"10{year}" (source 1, split 0); a single week's
    projection = f"11{year}{week}" (source 1, split 1)."""
    out = []
    want = f"10{year}" if not week else f"11{year}{week}"
    for offset in (0, 400):
        flt = json.dumps({"players": {"limit": 400, "offset": offset,
                          "sortPercOwned": {"sortAsc": False, "sortPriority": 1}}})
        r = httpx.get(ESPN_PROJ_URL.format(year=year),
                      params={"view": "kona_player_info"},
                      headers={"x-fantasy-filter": flt, "User-Agent": FP_UA},
                      timeout=timeout)
        r.raise_for_status()
        for row in r.json().get("players", []):
            p = row.get("player") or {}
            pos = ESPN_POS.get(p.get("defaultPositionId"))
            if not pos:
                continue
            pts = next((s.get("appliedTotal") for s in (p.get("stats") or [])
                        if s.get("id") == want), None)
            if not pts:
                continue
            out.append({"name": p.get("fullName", ""), "position": pos,
                        "pts": float(pts)})
    return out


def _num(s: str) -> float:
    try:
        return float(s.replace(",", "").replace("%", "") or 0)
    except ValueError:
        return 0.0


def fetch_cbs_projections(year: int, timeout: float = 25.0) -> list[dict[str, Any]]:
    """CBS season projections from their server-rendered PPR pages. The fpts
    column is CBS's PPR scoring — use only for full-PPR leagues (the caller
    guards). Rows: {name, position, pts}."""
    out = []
    for i, pos in enumerate(("QB", "RB", "WR", "TE", "K")):
        if i:
            time.sleep(1.0)
        r = httpx.get(CBS_URL.format(pos=pos, year=year), timeout=timeout,
                      headers={"User-Agent": FP_UA}, follow_redirects=True)
        r.raise_for_status()
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", r.text, re.S):
            if "CellPlayerName--long" not in tr:
                continue
            name_m = re.search(r'CellPlayerName--long.*?<a[^>]*>([^<]+)</a>', tr, re.S)
            cells = [re.sub(r"<[^>]+>", "", c).strip()
                     for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            if not name_m or len(cells) < 4:
                continue
            pts = _num(cells[-2])  # fpts; fppg is last
            if pts:
                out.append({"name": name_m.group(1).strip(), "position": pos, "pts": pts})
    return out


def fetch_fftoday_projections(year: int, scoring: dict[str, float],
                              timeout: float = 25.0) -> list[dict[str, Any]]:
    """FFToday season stat projections, scored HERE with the league's own
    scoring settings (their FPts column is ignored). Rows: {name, position, pts}."""
    weights = {
        "pass_yd": scoring.get("pass_yd", 0.04), "pass_td": scoring.get("pass_td", 4.0),
        "pass_int": scoring.get("pass_int", -1.0), "rush_yd": scoring.get("rush_yd", 0.1),
        "rush_td": scoring.get("rush_td", 6.0), "rec": scoring.get("rec", 1.0),
        "rec_yd": scoring.get("rec_yd", 0.1), "rec_td": scoring.get("rec_td", 6.0),
    }
    out = []
    for posid, (pos, cols) in FFT_COLS.items():
        for page in (0, 1):
            if posid in (10, 40) and page:  # 50 QBs / 50 TEs are plenty
                continue
            time.sleep(1.0)
            r = httpx.get(FFT_URL.format(year=year, posid=posid, page=page),
                          timeout=timeout, headers={"User-Agent": FP_UA},
                          follow_redirects=True)
            r.raise_for_status()
            for m in re.finditer(r'<A HREF="/stats/players/[^"]*"[^>]*>([^<]+)</A>(.*?)</TR>',
                                 r.text, re.S | re.I):
                cells = [re.sub(r"<[^>]+>", "", c).strip()
                         for c in re.findall(r"<TD[^>]*>(.*?)</TD>", m.group(2), re.S | re.I)]
                stats = cells[2:]  # after Team, Bye; FPts trails
                if len(stats) != len(cols) + 1:
                    continue
                pts = sum(_num(v) * weights.get(k, 0.0) for k, v in zip(cols, stats))
                if pts > 0:
                    out.append({"name": m.group(1).strip(), "position": pos,
                                "pts": round(pts, 1)})
    return out


def fetch_fantasycalc_values(ppr: float = 1.0, num_qbs: int = 1,
                             timeout: float = 15.0) -> list[dict[str, Any]]:
    """Rows: {sleeper_id, name, redraft_value, trend_30d}."""
    r = httpx.get(FANTASYCALC_URL,
                  params={"isDynasty": "false", "numQbs": num_qbs, "ppr": ppr},
                  timeout=timeout, headers={"User-Agent": "bootlegger/0.1"})
    r.raise_for_status()
    out = []
    for row in r.json():
        player = row.get("player", {})
        sid = player.get("sleeperId")
        if not sid:
            continue
        out.append({
            "sleeper_id": str(sid),
            "name": player.get("name", ""),
            "redraft_value": float(row.get("value", 0) or 0),
            "trend_30d": float(row.get("trend30Day", 0) or 0),
        })
    return out


# NFL schedule: nflverse's games.csv (Lee Sharpe's canonical file, mirrored by
# the nflverse-data release). Carries gameday + gametime (US/Eastern), roof,
# stadium, and location — everything kickoff rules, byes, and weather need.
NFLVERSE_GAMES_URL = "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv"
NFLVERSE_GAMES_FALLBACK = "http://www.habitatring.com/games.csv"
# nflverse team codes vs Sleeper's: only the Rams differ (verified against the
# live players table 2026-08-25 — 32 codes, LAR not LA).
NFLVERSE_TO_SLEEPER = {"LA": "LAR"}

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


def fetch_nflverse_games(season: int, timeout: float = 60.0) -> list[dict[str, Any]]:
    """Regular-season rows for `season` from games.csv. Rows: {week, gameday,
    gametime (ET, may be ''), away_team, home_team, roof, stadium, location} —
    team codes already normalized to Sleeper's vocabulary."""
    import csv
    import io
    text = None
    for url in (NFLVERSE_GAMES_URL, NFLVERSE_GAMES_FALLBACK):
        try:
            r = httpx.get(url, timeout=timeout, follow_redirects=True,
                          headers={"User-Agent": "bootlegger/0.1"})
            r.raise_for_status()
            text = r.text
            break
        except httpx.HTTPError:
            continue
    if text is None:
        raise RuntimeError("nflverse games.csv unreachable (both mirrors)")
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        if row.get("game_type") != "REG":
            continue
        try:
            if int(row.get("season") or 0) != season:
                continue
            week = int(row.get("week") or 0)
        except ValueError:
            continue
        if not week:
            continue
        out.append({
            "week": week,
            "gameday": row.get("gameday") or "",
            "gametime": row.get("gametime") or "",
            "away_team": NFLVERSE_TO_SLEEPER.get(row.get("away_team", ""), row.get("away_team", "")),
            "home_team": NFLVERSE_TO_SLEEPER.get(row.get("home_team", ""), row.get("home_team", "")),
            "roof": (row.get("roof") or "").strip().lower(),
            "stadium": row.get("stadium") or "",
            "location": (row.get("location") or "Home").strip(),
        })
    return out


def fetch_openmeteo_hour(lat: float, lon: float, date: str, hour_utc: int,
                         timeout: float = 15.0) -> dict[str, float | None]:
    """One forecast hour (UTC) at a point from Open-Meteo (keyless).
    Returns {wind_mph, precip_prob, temp_f} with None for anything missing."""
    r = httpx.get(OPEN_METEO_URL, params={
        "latitude": lat, "longitude": lon,
        "hourly": "temperature_2m,precipitation_probability,wind_speed_10m",
        "wind_speed_unit": "mph", "temperature_unit": "fahrenheit",
        "timezone": "UTC", "start_date": date, "end_date": date,
    }, timeout=timeout, headers={"User-Agent": "bootlegger/0.1"})
    r.raise_for_status()
    h = r.json().get("hourly") or {}
    times = h.get("time") or []
    want = f"{date}T{hour_utc:02d}:00"
    try:
        i = times.index(want)
    except ValueError:
        return {"wind_mph": None, "precip_prob": None, "temp_f": None}

    def _at(key: str) -> float | None:
        vals = h.get(key) or []
        v = vals[i] if i < len(vals) else None
        return float(v) if v is not None else None

    return {"wind_mph": _at("wind_speed_10m"),
            "precip_prob": _at("precipitation_probability"),
            "temp_f": _at("temperature_2m")}


# Draft Sharks (paid sub): the rankings lazy-load endpoint serves the full
# table as HTML fragments. Auth = session cookie exported after login, stored
# mode-600 at DS_COOKIE_FILE — never in the repo. Slug is PPR; other scorings
# have different slugs we have not mapped (caller guards).
DS_ROWS_URL = ("https://www.draftsharks.com/rankings/load-rows?offset=0&limit=400"
               "&fantasyPosition=&pprSuperflexSlug=ppr&sort=-dsValue&researchDepth=rankings")


def fetch_draftsharks(cookie: str, timeout: float = 40.0) -> list[dict[str, Any]]:
    """Rows: {name, position, pts (their DS projection), floor, ceiling,
    injury_pct, proj_games}. Their 3-year award-winning house numbers."""
    r = httpx.get(DS_ROWS_URL, timeout=timeout, follow_redirects=True,
                  headers={"Cookie": cookie, "User-Agent": FP_UA,
                           "X-Requested-With": "XMLHttpRequest"})
    r.raise_for_status()
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", r.text, re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(cells) < 12:
            continue
        nm = re.search(r'first-name="([^"]*)"\s+last-name="([^"]*)"', cells[1])
        pm = re.search(r'pos-roster-spot="(QB|RB|WR|TE|K|DST|DEF)"', cells[1])
        if not nm:
            continue
        txt = [re.sub(r"<[^>]+>", " ", c) for c in cells]
        txt = [re.sub(r"\s+", " ", t).strip() for t in txt]
        floor, cons, ds, ceil = (_num(txt[i]) for i in (7, 8, 9, 10))
        if not ds:
            continue
        pos = (pm.group(1) if pm else "").replace("DST", "DEF")
        out.append({
            "name": f"{nm.group(1)} {nm.group(2)}".strip(),
            "position": pos,
            "pts": ds, "floor": floor or None, "ceiling": ceil or None,
            "injury_pct": _num(txt[6]) or None,
            "proj_games": _num(txt[2]) or None,
        })
    return out
