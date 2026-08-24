"""External projection/market/consensus sources: FFC ADP, FantasyCalc values,
and FantasyPros (keyless ECR consensus ranks; key-gated point projections).
FantasyCalc rows carry a sleeperId, so they join directly; the others join on
normalized name+position."""
from __future__ import annotations

import json
import re
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
FP_UA = "Mozilla/5.0 (X11; Linux x86_64) bootlegger/0.1"


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
                                  timeout: float = 20.0) -> list[dict[str, Any]]:
    """FantasyPros aggregate season point projections via the public v2 API.
    Needs a personal key (request at fantasypros.com/apis). Response shape is
    parsed defensively — verify on first keyed run."""
    out = []
    for pos in ("QB", "RB", "WR", "TE", "K", "DST"):
        r = httpx.get(FP_PROJECTIONS_URL.format(year=year),
                      params={"position": pos, "week": 0, "scoring": scoring},
                      headers={"x-api-key": api_key, "User-Agent": FP_UA},
                      timeout=timeout)
        r.raise_for_status()
        for p in r.json().get("players", []):
            stats = p.get("stats") or {}
            pts = stats.get("points") or stats.get("fpts") or p.get("fpts")
            if not pts:
                continue
            out.append({
                "name": p.get("name") or p.get("player_name", ""),
                "position": "DEF" if pos == "DST" else pos,
                "team": p.get("team_id") or p.get("team"),
                "pts": float(pts),
            })
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
