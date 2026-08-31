"""ESPN league adapter: an EspnClient that speaks Sleeper.

The whole integration strategy in one sentence: **Sleeper's public data stays
the universe, and ESPN supplies only league-shaped facts, translated into
Sleeper's shapes at this boundary.** Every table keys on sleeper_id, every
engine downstream is source-agnostic (demo mode proves it daily), and the
national layer — players, projections, ADP, news — needs no league auth at
all. So the only thing an ESPN league changes is who answers the six or seven
league-shaped calls, and this module answers them in SleeperClient's own
dialect: `league()` returns a dict with `roster_positions` as strings,
`rosters()` returns rows with sleeper player ids, `matchups()` rows carry
`players_points`, and the draft-history walker's `previous_league_id` chain
works because this client mints season tokens for it. ingest.py runs the SAME
etl functions either way.

Identity is the hard part, handled once here:

- Skill players map ESPN id -> sleeper_id by normalized name + position
  against the players table, with the refuse-on-ambiguity discipline the
  hands' surname matcher uses: two candidates is zero candidates. An unmapped
  man gets a synthetic `espn-{id}` so nothing downstream crashes — his pick
  still carries `metadata.position`, which is all the room-tendency curves
  need (draft_picks.pos exists for exactly this reason).
- Defenses need no fuzzy match at all: Sleeper's DEF player_id IS the team
  abbreviation ("JAX"), so ESPN's proTeamId goes through a fixed table.

Auth: a private league needs the `SWID` and `espn_s2` cookies from a logged-in
espn.com session (env vars, or /data/.espn_cookies.json — see
tools/espn_login.py). ESPN's read host answers 401 without them, and this
client says so in words rather than passing empty payloads downstream.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx

from .config import settings
from .sources import normalize_name

READ_HOST = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"
COOKIE_FILE = Path("/data/.espn_cookies.json")

# ESPN lineupSlotId -> the position strings every engine already speaks.
# Slots this app has no concept for (TQB, HC, misc IDP) map to None and are
# dropped from the roster shape rather than guessed at.
SLOT_MAP = {
    0: "QB", 2: "RB", 3: "WRRB_FLEX", 4: "WR", 5: "REC_FLEX", 6: "TE",
    7: "SUPER_FLEX", 16: "DEF", 17: "K", 20: "BN", 21: "IR", 23: "FLEX",
}
# ESPN defaultPositionId -> position.
POS_MAP = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DEF"}
# ESPN proTeamId -> the abbreviations Sleeper uses. WSH is ESPN's spelling;
# Sleeper writes WAS, and Sleeper's DEF player_id is the abbreviation itself.
PRO_TEAMS = {
    1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL", 7: "DEN",
    8: "DET", 9: "GB", 10: "TEN", 11: "IND", 12: "KC", 13: "LV", 14: "LAR",
    15: "MIA", 16: "MIN", 17: "NE", 18: "NO", 19: "NYG", 20: "NYJ",
    21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC", 25: "SF", 26: "SEA",
    27: "TB", 28: "WAS", 29: "CAR", 30: "JAX", 33: "BAL", 34: "HOU",
}
# ESPN statId -> Sleeper scoring key, for the handful the app reads.
# _league_scoring only inspects `rec`; the rest keep the stored scoring_json
# legible to a human comparing it against the league settings page.
STAT_MAP = {
    3: "pass_yd", 4: "pass_td", 20: "pass_int", 24: "rush_yd", 25: "rush_td",
    42: "rec_yd", 43: "rec_td", 53: "rec", 72: "fum_lost",
}

_HIST = re.compile(r"^espnhist:(\d{4})$")


class EspnAuthError(RuntimeError):
    pass


class EspnClient:
    """SleeperClient's league surface, answered from ESPN.

    Only the league-shaped methods exist. Player-universe calls (players,
    projections, trending) deliberately do not: those stay on Sleeper's free
    public API even when the league lives on ESPN.
    """

    def __init__(self, conn=None, timeout: float = 20.0):
        self.timeout = timeout
        self._conn = conn                     # players table, for the id map
        self._cache: dict[str, Any] = {}
        self._name_map: dict[tuple[str, str], str] | None = None
        self._ambiguous: set[tuple[str, str]] = set()

    # -- auth ----------------------------------------------------------------

    def _cookies(self) -> dict[str, str]:
        swid, s2 = settings.espn_swid, settings.espn_s2
        if not (swid and s2) and COOKIE_FILE.is_file():
            try:
                d = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
                swid = swid or d.get("swid") or d.get("SWID") or ""
                s2 = s2 or d.get("espn_s2") or ""
            except (ValueError, OSError):
                pass
        if not (swid and s2):
            raise EspnAuthError(
                "the league is private and no ESPN session is configured — set "
                "BOOTLEGGER_ESPN_SWID and BOOTLEGGER_ESPN_S2, or run "
                "tools/espn_login.py to capture them into "
                f"{COOKIE_FILE}")
        # ESPN issues SWID wrapped in braces and rejects it bare.
        if not swid.startswith("{"):
            swid = "{" + swid.strip("{}") + "}"
        return {"SWID": swid, "espn_s2": s2}

    # -- transport -----------------------------------------------------------

    def _league_doc(self, season: int) -> dict:
        key = f"doc:{season}"
        if key in self._cache:
            return self._cache[key]
        views = "view=mSettings&view=mTeam&view=mRoster&view=mDraftDetail&view=mStatus"
        current = season == settings.season
        if current:
            url = f"{READ_HOST}/seasons/{season}/segments/0/leagues/{settings.league_id}?{views}"
        else:
            # Past seasons live behind a different path and come back as a
            # one-element LIST, not a dict. Both facts are load-bearing.
            url = (f"{READ_HOST}/leagueHistory/{settings.league_id}"
                   f"?seasonId={season}&{views}")
        r = httpx.get(url, cookies=self._cookies(), timeout=self.timeout,
                      headers={"Accept": "application/json"})
        if r.status_code == 401:
            raise EspnAuthError(
                "ESPN answered 401 — the SWID/espn_s2 session is missing, "
                "expired, or for the wrong account")
        r.raise_for_status()
        doc = r.json()
        if isinstance(doc, list):
            if not doc:
                raise RuntimeError(f"ESPN has no season {season} for this league")
            doc = doc[0]
        self._cache[key] = doc
        return doc

    def _week_doc(self, week: int) -> dict:
        key = f"week:{week}"
        if key not in self._cache:
            url = (f"{READ_HOST}/seasons/{settings.season}/segments/0/leagues/"
                   f"{settings.league_id}?view=mMatchupScore&view=mRoster"
                   f"&scoringPeriodId={week}")
            r = httpx.get(url, cookies=self._cookies(), timeout=self.timeout,
                          headers={"Accept": "application/json"})
            r.raise_for_status()
            self._cache[key] = r.json()
        return self._cache[key]

    # -- identity ------------------------------------------------------------

    def _names(self) -> dict[tuple[str, str], str]:
        """(normalized name, pos) -> sleeper_id, ambiguity removed entirely."""
        if self._name_map is None:
            m: dict[tuple[str, str], str] = {}
            dup: set[tuple[str, str]] = set()
            for r in self._conn.execute("SELECT sleeper_id, name, pos FROM players"):
                k = (normalize_name(r["name"]), r["pos"])
                if k in m:
                    dup.add(k)
                else:
                    m[k] = r["sleeper_id"]
            for k in dup:
                m.pop(k, None)
            self._ambiguous = dup
            self._name_map = m
        return self._name_map

    def map_player(self, p: dict) -> str:
        """ESPN player object -> sleeper_id, or a synthetic espn-{id}."""
        pos = POS_MAP.get(p.get("defaultPositionId"))
        if pos == "DEF":
            return PRO_TEAMS.get(p.get("proTeamId"), f"espn-{p.get('id')}")
        name = p.get("fullName") or ""
        sid = self._names().get((normalize_name(name), pos or ""))
        return sid or f"espn-{p.get('id')}"

    # -- the Sleeper dialect -------------------------------------------------

    def league(self, league_id: str) -> dict:
        m = _HIST.match(str(league_id))
        season = int(m.group(1)) if m else settings.season
        doc = self._league_doc(season)
        st = doc.get("settings") or {}
        counts = ((st.get("rosterSettings") or {}).get("lineupSlotCounts") or {})
        roster_positions: list[str] = []
        for slot_id, n in sorted(counts.items(), key=lambda kv: int(kv[0])):
            pos = SLOT_MAP.get(int(slot_id))
            if pos and int(n) > 0:
                roster_positions += [pos] * int(n)
        scoring: dict[str, float] = {}
        for item in ((st.get("scoringSettings") or {}).get("scoringItems") or []):
            k = STAT_MAP.get(item.get("statId"))
            if k is not None:
                scoring[k] = item.get("points", 0)
        # The walker asks each season for the one before it. ESPN keeps the
        # list in status.previousSeasons, so the chain is minted as tokens this
        # client itself resolves — which is what lets etl_draft_history run
        # completely unmodified against an ESPN league.
        prior = sorted(s for s in ((doc.get("status") or {}).get("previousSeasons") or [])
                       if s < season)
        return {
            "name": st.get("name") or f"ESPN league {settings.league_id}",
            "roster_positions": roster_positions,
            "settings": {"num_teams": st.get("size") or len(doc.get("teams") or [])},
            "previous_league_id": f"espnhist:{prior[-1]}" if prior else None,
            "scoring_settings": scoring,
        }

    def users(self, league_id: str) -> list[dict]:
        doc = self._league_doc(settings.season)
        members = {m.get("id"): m.get("displayName") or m.get("firstName") or m.get("id")
                   for m in doc.get("members") or []}
        out = []
        for t in doc.get("teams") or []:
            # The team's name is the identity a league mate recognizes;
            # ESPN member handles are often an email fragment.
            name = t.get("name") or f"{t.get('location', '')} {t.get('nickname', '')}".strip()
            owner = (t.get("owners") or [None])[0]
            out.append({"user_id": owner or f"team-{t['id']}",
                        "display_name": name or members.get(owner) or f"Team {t['id']}"})
        return out

    def rosters(self, league_id: str) -> list[dict]:
        doc = self._league_doc(settings.season)
        out = []
        for t in doc.get("teams") or []:
            entries = ((t.get("roster") or {}).get("entries") or [])
            players, starters = [], []
            for e in entries:
                p = ((e.get("playerPoolEntry") or {}).get("player") or {})
                pid = self.map_player(p)
                players.append(pid)
                if SLOT_MAP.get(e.get("lineupSlotId")) not in ("BN", "IR", None):
                    starters.append(pid)
            rec = ((t.get("record") or {}).get("overall") or {})
            out.append({
                "roster_id": t["id"],
                "owner_id": (t.get("owners") or [None])[0] or f"team-{t['id']}",
                "players": players,
                "starters": starters,
                "settings": {"wins": rec.get("wins", 0), "losses": rec.get("losses", 0),
                             "ties": rec.get("ties", 0),
                             "fpts": rec.get("pointsFor", 0), "fpts_decimal": 0},
            })
        return out

    def matchups(self, league_id: str, week: int) -> list[dict]:
        doc = self._week_doc(week)
        # Weekly realized points per rostered man: the roster view carries each
        # player's stat lines, and the actual for this week is the entry with
        # statSourceId 0 for this scoringPeriodId. This is what feeds
        # player_week_actuals — source calibration and the forecast ledger eat
        # from that table, so it is worth the second view.
        points: dict[int, dict[str, float]] = {}
        starters_by_team: dict[int, list[str]] = {}
        for t in doc.get("teams") or []:
            pp, ss = {}, []
            for e in ((t.get("roster") or {}).get("entries") or []):
                p = ((e.get("playerPoolEntry") or {}).get("player") or {})
                pid = self.map_player(p)
                for s in p.get("stats") or []:
                    if (s.get("statSourceId") == 0
                            and s.get("scoringPeriodId") == week
                            and s.get("appliedTotal") is not None):
                        pp[pid] = float(s["appliedTotal"])
                if SLOT_MAP.get(e.get("lineupSlotId")) not in ("BN", "IR", None):
                    ss.append(pid)
            points[t["id"]] = pp
            starters_by_team[t["id"]] = ss
        out = []
        for g in doc.get("schedule") or []:
            if g.get("matchupPeriodId") != week:
                continue
            for side in ("home", "away"):
                s = g.get(side)
                if not s or s.get("teamId") is None:
                    continue
                tid = s["teamId"]
                out.append({
                    "matchup_id": g.get("id"),
                    "roster_id": tid,
                    "points": s.get("totalPoints"),
                    "starters": starters_by_team.get(tid, []),
                    "players_points": points.get(tid, {}),
                })
        return out

    # -- drafts, current and historical --------------------------------------

    def league_drafts(self, league_id: str) -> list[dict]:
        m = _HIST.match(str(league_id))
        season = int(m.group(1)) if m else settings.season
        doc = self._league_doc(season)
        dd = doc.get("draftDetail") or {}
        if not dd.get("drafted"):
            return []
        teams = ((doc.get("settings") or {}).get("size")
                 or len(doc.get("teams") or []) or 0)
        picks = dd.get("picks") or []
        rounds = max((p.get("roundId", 0) for p in picks), default=0)
        return [{"draft_id": f"espn-{settings.league_id}-{season}",
                 "status": "complete",
                 "season": str(season),
                 "settings": {"teams": teams, "rounds": rounds}}]

    def draft(self, draft_id: str) -> dict:
        season = int(str(draft_id).rsplit("-", 1)[-1])
        doc = self._league_doc(season)
        dd = doc.get("draftDetail") or {}
        picks = sorted(dd.get("picks") or [], key=lambda p: p.get("overallPickNumber", 0))
        teams = ((doc.get("settings") or {}).get("size")
                 or len(doc.get("teams") or []) or 0)
        rounds = max((p.get("roundId", 0) for p in picks), default=0)
        # ESPN publishes no draft_order map; for a snake it IS the first-round
        # order, which the picks carry. slot_to_roster_id is what turns "slot
        # 3 on the clock" into a name, exactly as it does for Sleeper.
        slot_to_roster = {str(i + 1): p.get("teamId")
                          for i, p in enumerate(picks[:teams])}
        roster_to_slot = {v: int(k) for k, v in slot_to_roster.items()}
        my_slot = roster_to_slot.get(settings.my_roster_id)
        st = {"teams": teams, "rounds": rounds}
        if my_slot:
            st["slot"] = my_slot
        # slot_to_roster_id rides at the TOP level of the draft doc — that is
        # where Sleeper puts it and where etl_draft_picks reads it from.
        return {"draft_id": draft_id,
                "status": "complete" if dd.get("drafted") else "pre_draft",
                "settings": st,
                "slot_to_roster_id": slot_to_roster}

    def draft_picks(self, draft_id: str) -> list[dict]:
        season = int(str(draft_id).rsplit("-", 1)[-1])
        doc = self._league_doc(season)
        dd = doc.get("draftDetail") or {}
        players = {}
        for t in doc.get("teams") or []:
            for e in ((t.get("roster") or {}).get("entries") or []):
                p = ((e.get("playerPoolEntry") or {}).get("player") or {})
                if p.get("id") is not None:
                    players[p["id"]] = p
        picks = sorted(dd.get("picks") or [], key=lambda p: p.get("overallPickNumber", 0))
        teams = ((doc.get("settings") or {}).get("size") or 0)
        roster_to_slot = {p.get("teamId"): i + 1 for i, p in enumerate(picks[:teams])}
        out = []
        for p in picks:
            player = players.get(p.get("playerId"), {})
            pos = POS_MAP.get(player.get("defaultPositionId"))
            pid = (self.map_player(player) if player
                   else f"espn-{p.get('playerId')}")
            out.append({
                "pick_no": p.get("overallPickNumber"),
                "round": p.get("roundId"),
                "draft_slot": roster_to_slot.get(p.get("teamId")),
                "roster_id": p.get("teamId"),
                "player_id": pid,
                # The history walker reads position from metadata — the pick
                # keeps it even when the man has left every players table.
                "metadata": {"position": pos or ""},
            })
        return out

    def transactions(self, league_id: str, week: int) -> list[dict]:
        # Waiver history feeds FAAB percentile pricing. ESPN v1: not wired —
        # an empty list degrades that surface honestly (the book reads as
        # empty) rather than inventing bids.
        return []
