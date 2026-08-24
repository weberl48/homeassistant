"""External projection/market sources: FFC ADP and FantasyCalc values.
Both are free JSON APIs. FantasyCalc rows carry a sleeperId, so they join
directly; FFC joins on normalized name+position."""
from __future__ import annotations

import re
from typing import Any

import httpx

FFC_URL = "https://fantasyfootballcalculator.com/api/v1/adp/ppr"
FANTASYCALC_URL = "https://api.fantasycalc.com/values/current"


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
