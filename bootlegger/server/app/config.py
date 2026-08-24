"""Runtime configuration, all overridable via environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
SERVER_DIR = APP_DIR.parent
PROJECT_DIR = SERVER_DIR.parent

# VOLS baselines for a 12-team full-PPR league (design doc §4).
VOLS_BASELINES = {"QB": 12, "RB": 31, "WR": 40, "TE": 12, "K": 12, "DEF": 12}

# Positions a FLEX slot accepts, and slot vocabulary shared with Sleeper.
FLEX_ELIGIBLE = {"RB", "WR", "TE"}
SUPER_FLEX_ELIGIBLE = {"QB", "RB", "WR", "TE"}

MATERIALITY_PTS = 1.5          # lineup diff worth notifying about
AUTO_EDGE_PTS = 2.0            # auto_execute needs at least this projection edge
AUTO_KICKOFF_BUFFER_H = 2.0    # auto_execute needs this many hours before kickoff
SOURCE_DISAGREEMENT_MAX = 0.25 # don't-act: relative stdev across sources
ADP_SIGMA_FALLBACK = 0.15      # sigma = 0.15 * ADP when no source stdev
ADP_SIGMA_FLOOR = 2.0          # never model a pick tighter than ±2 slots


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class Settings:
    mode: str = "demo"                     # demo | live
    db_path: Path = PROJECT_DIR / "data" / "bootlegger.db"
    audit_dir: Path = PROJECT_DIR / "audit"
    season: int = 2026
    league_id: str = ""                    # live mode: your Sleeper league id
    user_id: str = ""                      # live mode: your Sleeper user id
    draft_id: str = ""                     # optional explicit draft id
    my_roster_id: int = 7                  # demo: draft slot / roster id
    teams: int = 12
    rounds: int = 15
    faab_budget: int = 100
    demo_pick_seconds: float = 5.0         # sim cadence between opponent picks
    demo_my_clock_seconds: float = 16.0    # how long the sim leaves you "on the clock"
    draft_poll_seconds: float = 2.0        # live draft poll cadence
    approve_required: bool = True          # actuation gate (design doc §5.5)
    hands_dry_run: bool = True             # dry-run applies swaps to the local mirror only
    healthchecks_url: str = ""             # dead-man ping target, empty = disabled
    expo_push_tokens: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> "Settings":
        s = cls()
        s.mode = _env("BOOTLEGGER_MODE", s.mode)
        s.db_path = Path(_env("BOOTLEGGER_DB", str(s.db_path)))
        s.audit_dir = Path(_env("BOOTLEGGER_AUDIT_DIR", str(s.audit_dir)))
        s.season = int(_env("BOOTLEGGER_SEASON", str(s.season)))
        s.league_id = _env("SLEEPER_LEAGUE_ID", s.league_id)
        s.user_id = _env("SLEEPER_USER_ID", s.user_id)
        s.draft_id = _env("SLEEPER_DRAFT_ID", s.draft_id)
        s.my_roster_id = int(_env("BOOTLEGGER_MY_ROSTER_ID", str(s.my_roster_id)))
        s.teams = int(_env("BOOTLEGGER_TEAMS", str(s.teams)))
        s.rounds = int(_env("BOOTLEGGER_ROUNDS", str(s.rounds)))
        s.demo_pick_seconds = float(_env("DEMO_PICK_SECONDS", str(s.demo_pick_seconds)))
        s.demo_my_clock_seconds = float(_env("DEMO_MY_CLOCK_SECONDS", str(s.demo_my_clock_seconds)))
        s.approve_required = _env("BOOTLEGGER_APPROVE_REQUIRED", "1") not in ("0", "false", "no")
        s.hands_dry_run = _env("HANDS_DRY_RUN", "1") not in ("0", "false", "no")
        s.healthchecks_url = _env("HEALTHCHECKS_URL", s.healthchecks_url)
        tokens = _env("EXPO_PUSH_TOKENS", "")
        s.expo_push_tokens = [t.strip() for t in tokens.split(",") if t.strip()]
        return s


settings = Settings.from_env()

# Demo league shape: 12-team full-PPR, standard Sleeper roster.
DEMO_ROSTER_POSITIONS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF",
                         "BN", "BN", "BN", "BN", "BN", "BN"]
DEMO_SCORING = {"rec": 1.0, "pass_td": 4.0, "rush_td": 6.0, "rec_td": 6.0}
