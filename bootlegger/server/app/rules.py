"""Don't-act policy (design doc §5.6): hands can never fire while any enabled
rule trips. Evaluation is advisory for Tier 1 (shown on the card) and binding
for Tier 2 (checked again at job execution)."""
from __future__ import annotations

import json
import sqlite3

from . import schedule
from .config import SOURCE_DISAGREEMENT_MAX


def _enabled(conn: sqlite3.Connection) -> dict[str, float | None]:
    return {r["name"]: r["threshold"] for r in
            conn.execute("SELECT name, threshold FROM rules WHERE enabled=1")}


def _team_of(conn: sqlite3.Connection, player_id: str) -> str | None:
    row = conn.execute("SELECT team FROM players WHERE sleeper_id=?",
                       (player_id,)).fetchone()
    return row["team"] if row else None


def kickoff_hours_away(conn: sqlite3.Connection, player_id: str, week: int) -> float | None:
    """Hours until the player's kickoff, from the nfl_games schedule table.
    None (no schedule row, or kickoff not yet announced) still fails toward
    acting only in approve mode where a human is in the loop."""
    return schedule.kickoff_hours_away(conn, _team_of(conn, player_id), week)


def _disagreement(conn: sqlite3.Connection, player_id: str, week: int) -> float:
    row = conn.execute(
        "SELECT pts_robust, stdev FROM consensus WHERE player_id=? AND week=?",
        (player_id, week),
    ).fetchone()
    if not row or not row["stdev"] or not row["pts_robust"] or row["pts_robust"] < 1:
        return 0.0
    return row["stdev"] / row["pts_robust"]


def evaluate(conn: sqlite3.Connection, swaps: list[dict], week: int) -> list[str]:
    """Names of the rules that fire for this set of swaps."""
    enabled = _enabled(conn)
    fired: list[str] = []
    player_ids = [pid for s in swaps for pid in (s.get("out_id"), s.get("in_id")) if pid]

    if "questionable_near_kickoff" in enabled:
        window = enabled["questionable_near_kickoff"] or 3.0
        for pid in player_ids:
            row = conn.execute("SELECT injury_status FROM players WHERE sleeper_id=?",
                               (pid,)).fetchone()
            if row and row["injury_status"] in ("Questionable", "Doubtful"):
                hrs = kickoff_hours_away(conn, pid, week)
                if hrs is not None and hrs <= window:
                    fired.append("questionable_near_kickoff")
                    break

    if "source_disagreement" in enabled:
        limit = enabled["source_disagreement"] or SOURCE_DISAGREEMENT_MAX
        if any(_disagreement(conn, pid, week) > limit for pid in player_ids):
            fired.append("source_disagreement")

    if "any_drop_involved" in enabled:
        # Structurally impossible for lineup swaps; kept as a tripwire in case a
        # future payload ever smuggles one in.
        if any("drop" in json.dumps(s).lower() for s in swaps):
            fired.append("any_drop_involved")

    if "any_faab_involved" in enabled:
        if any("faab" in json.dumps(s).lower() for s in swaps):
            fired.append("any_faab_involved")

    if "weather_flag_on_game" in enabled:
        # Threshold = wind mph (default 20); ≥70% precip flags regardless.
        wind_min = enabled["weather_flag_on_game"] or 20.0
        for pid in player_ids:
            g = schedule.game_for(conn, _team_of(conn, pid), week)
            if schedule.weather_flags(g, wind_min=wind_min):
                fired.append("weather_flag_on_game")
                break

    return fired
