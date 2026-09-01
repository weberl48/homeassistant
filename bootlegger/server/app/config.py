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
ADP_SIGMA_FLOOR = 3.0          # real Sleeper mock (n=180): early picks deviate σ≈2.9

# Position pressure: how far the room's picks at one position must diverge from
# what ADP said that pick range would take before the board says anything.
# Calibrated 2026-08-26 by sweeping every window of a full demo draft — twelve
# independent ADP-followers, so no runs exist in it and every fire is a false
# alarm. Share of windows where at least one position fired:
#     threshold 3.0 -> 13.5%   (too chatty to mean anything)
#     threshold 4.0 ->  2.9%
#     threshold 5.0 ->  0.0%   (silent on noise, likely deaf to real runs)
# 4.0 is the knee. NOTE the denominator: an earlier read of "2.8% at 3.0"
# counted position-windows (each position judged separately), not windows —
# per window the same data reads 13.5%. A real room's residual will be wider
# than the simulator's: recalibrate on live drafts before this number is
# trusted, and before it is ever allowed near survival_prob (see
# engines/advisories.py).
RUN_RESIDUAL_THRESHOLD = 4.0
# Picks of history the pressure window looks back over.
RUN_WINDOW_PICKS = 10


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class Settings:
    mode: str = "demo"                     # demo | live
    db_path: Path = PROJECT_DIR / "data" / "bootlegger.db"
    audit_dir: Path = PROJECT_DIR / "audit"
    season: int = 2026
    platform: str = "sleeper"              # sleeper | espn — who answers league-shaped calls
    league_id: str = ""                    # live mode: your league id on that platform
    league_label: str = ""                 # what the masthead calls this league
    sibling_port: int = 0                  # the OTHER league's port on this host; 0 = none
    sibling_label: str = ""                # what to call it on the switcher
    espn_swid: str = ""                    # espn platform: SWID cookie (private leagues)
    espn_s2: str = ""                      # espn platform: espn_s2 cookie
    user_id: str = ""                      # live mode: your Sleeper user id
    draft_id: str = ""                     # optional explicit draft id
    my_roster_id: int = 7                  # demo: draft slot / roster id
    teams: int = 12
    rounds: int = 15
    faab_budget: int = 100
    demo_pick_seconds: float = 5.0         # sim cadence between opponent picks
    demo_my_clock_seconds: float = 16.0    # how long the sim leaves you "on the clock"
    draft_poll_seconds: float = 2.0        # live draft poll cadence
    scan_seconds: float = 300.0            # in-season lineup scan cadence
    approve_required: bool = True          # actuation gate (design doc §5.5)
    hands_dry_run: bool = True             # dry-run applies swaps to the local mirror only
    healthchecks_url: str = ""             # dead-man ping target, empty = disabled
    fantasypros_api_key: str = ""          # unlocks FP point projections (fantasypros.com/apis)
    api_token: str = ""                    # gates mutating routes; REQUIRED before hands go live
    ds_cookie_file: str = "/data/.ds_cookie"  # Draft Sharks session (mode 600); missing = source off
    expo_push_tokens: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> "Settings":
        s = cls()
        s.mode = _env("BOOTLEGGER_MODE", s.mode)
        s.db_path = Path(_env("BOOTLEGGER_DB", str(s.db_path)))
        s.audit_dir = Path(_env("BOOTLEGGER_AUDIT_DIR", str(s.audit_dir)))
        s.season = int(_env("BOOTLEGGER_SEASON", str(s.season)))
        s.platform = _env("BOOTLEGGER_PLATFORM", s.platform).lower()
        # SLEEPER_LEAGUE_ID keeps its name for compatibility with the running
        # stack; BOOTLEGGER_LEAGUE_ID wins when both are set, and is the one
        # an ESPN deployment should use.
        s.league_id = _env("BOOTLEGGER_LEAGUE_ID", _env("SLEEPER_LEAGUE_ID", s.league_id))
        # Underscores stand in for spaces: deploy.sh expands its env bundles
        # UNQUOTED, so a space in a value word-splits into docker arguments —
        # observed as docker trying to pull an image named "no".
        s.league_label = _env("BOOTLEGGER_LEAGUE_LABEL", s.league_label).replace("_", " ")
        s.sibling_port = int(_env("BOOTLEGGER_SIBLING_PORT", str(s.sibling_port)))
        s.sibling_label = _env("BOOTLEGGER_SIBLING_LABEL", s.sibling_label).replace("_", " ")
        s.espn_swid = _env("BOOTLEGGER_ESPN_SWID", s.espn_swid)
        s.espn_s2 = _env("BOOTLEGGER_ESPN_S2", s.espn_s2)
        s.user_id = _env("SLEEPER_USER_ID", s.user_id)
        s.draft_id = _env("SLEEPER_DRAFT_ID", s.draft_id)
        s.my_roster_id = int(_env("BOOTLEGGER_MY_ROSTER_ID", str(s.my_roster_id)))
        s.teams = int(_env("BOOTLEGGER_TEAMS", str(s.teams)))
        s.rounds = int(_env("BOOTLEGGER_ROUNDS", str(s.rounds)))
        s.demo_pick_seconds = float(_env("DEMO_PICK_SECONDS", str(s.demo_pick_seconds)))
        s.demo_my_clock_seconds = float(_env("DEMO_MY_CLOCK_SECONDS", str(s.demo_my_clock_seconds)))
        s.scan_seconds = float(_env("BOOTLEGGER_SCAN_SECONDS", str(s.scan_seconds)))
        s.approve_required = _env("BOOTLEGGER_APPROVE_REQUIRED", "1") not in ("0", "false", "no")
        s.hands_dry_run = _env("HANDS_DRY_RUN", "1") not in ("0", "false", "no")
        s.healthchecks_url = _env("HEALTHCHECKS_URL", s.healthchecks_url)
        s.fantasypros_api_key = _env("FANTASYPROS_API_KEY", s.fantasypros_api_key)
        s.api_token = _env("BOOTLEGGER_API_TOKEN", s.api_token)
        s.ds_cookie_file = _env("DS_COOKIE_FILE", s.ds_cookie_file)
        tokens = _env("EXPO_PUSH_TOKENS", "")
        s.expo_push_tokens = [t.strip() for t in tokens.split(",") if t.strip()]
        return s


settings = Settings.from_env()

# Demo league shape: 12-team full-PPR, standard Sleeper roster — and the same
# shape as the real league this project actually flies against. It was one FLEX
# against the real two until 2026-08-31, which made every self-play result
# quietly untrustworthy: a second flex slot is the whole reason a fourth
# receiver or a second tight end has anywhere to go, so the demo was rehearsing
# a scarcity regime the live board never sees. Fifteen slots either way, so the
# pick count and every exhaustion calculation downstream are unchanged.
DEMO_ROSTER_POSITIONS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX",
                         "K", "DEF", "BN", "BN", "BN", "BN", "BN"]
DEMO_SCORING = {"rec": 1.0, "pass_td": 4.0, "rush_td": 6.0, "rec_td": 6.0}
