"""Sleeper public API client (read-only, no auth). ~1000 req/min soft limit;
we poll draft picks at 2s and everything else far slower. The players blob is
~5MB, so it is cached on disk for 24h (design doc §2)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx

from .config import settings

BASE = "https://api.sleeper.app/v1"
PLAYERS_CACHE_TTL_S = 24 * 3600

# Fantasy-relevant positions; Sleeper's blob includes OL, practice squads, etc.
KEEP_POSITIONS = {"QB", "RB", "WR", "TE", "K", "DEF"}


class SleeperClient:
    def __init__(self, cache_dir: Path | None = None, timeout: float = 15.0):
        self.cache_dir = Path(cache_dir or settings.db_path.parent)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._http = httpx.Client(timeout=timeout, headers={"User-Agent": "bootlegger/0.1"})

    def _get(self, path: str) -> Any:
        r = self._http.get(f"{BASE}{path}")
        r.raise_for_status()
        return r.json()

    # -- reference data -----------------------------------------------------
    def players(self, force: bool = False) -> dict[str, dict]:
        """Full NFL players blob, disk-cached for 24h."""
        cache = self.cache_dir / "sleeper_players.json"
        if not force and cache.exists() and time.time() - cache.stat().st_mtime < PLAYERS_CACHE_TTL_S:
            return json.loads(cache.read_text())
        data = self._get("/players/nfl")
        cache.write_text(json.dumps(data))
        return data

    def relevant_players(self, force: bool = False) -> dict[str, dict]:
        return {
            pid: p
            for pid, p in self.players(force=force).items()
            if p.get("position") in KEEP_POSITIONS and (p.get("team") or p.get("position") == "DEF")
        }

    def trending_adds(self, hours: int = 24, limit: int = 25) -> list[dict]:
        return self._get(f"/players/nfl/trending/add?lookback_hours={hours}&limit={limit}")

    def nfl_state(self) -> dict:
        return self._get("/state/nfl")

    # -- league -------------------------------------------------------------
    def user(self, username_or_id: str) -> dict:
        return self._get(f"/user/{username_or_id}")

    def league(self, league_id: str) -> dict:
        return self._get(f"/league/{league_id}")

    def rosters(self, league_id: str) -> list[dict]:
        return self._get(f"/league/{league_id}/rosters")

    def users(self, league_id: str) -> list[dict]:
        return self._get(f"/league/{league_id}/users")

    def matchups(self, league_id: str, week: int) -> list[dict]:
        return self._get(f"/league/{league_id}/matchups/{week}")

    def transactions(self, league_id: str, week: int) -> list[dict]:
        return self._get(f"/league/{league_id}/transactions/{week}")

    # -- draft --------------------------------------------------------------
    def league_drafts(self, league_id: str) -> list[dict]:
        return self._get(f"/league/{league_id}/drafts")

    def draft(self, draft_id: str) -> dict:
        return self._get(f"/draft/{draft_id}")

    def draft_picks(self, draft_id: str) -> list[dict]:
        return self._get(f"/draft/{draft_id}/picks")
