import os as _os

import pytest as _pytest

# Structural guard against a mistake made twice this project: running the
# suite inside a live-configured container (real league env + mounted live
# DB) sends test traffic to real APIs and produces phantom failures.
if _os.environ.get("SLEEPER_LEAGUE_ID"):
    _pytest.exit(
        "refusing to run: SLEEPER_LEAGUE_ID is set. Run tests in a clean "
        "container (no live env vars, no /data mount).", returncode=2)

# Second door: the image sets BOOTLEGGER_DB=/data/bootlegger.db, so a mounted
# /data lets tests write recs and audit rows into the LIVE database even with
# no league env vars. Refuse when a pre-existing DB carries live markers.
import sqlite3 as _sqlite3
from pathlib import Path as _Path

_dbp = _os.environ.get("BOOTLEGGER_DB")
if _dbp and _Path(_dbp).exists():
    try:
        _conn = _sqlite3.connect(_dbp)
        _marked = any(
            _conn.execute(f"SELECT COUNT(*) FROM {_t}").fetchone()[0]
            for _t in ("league", "recommendations"))
        _conn.close()
    except _sqlite3.Error:
        _marked = True  # unreadable pre-existing DB: assume it matters
    if _marked:
        _pytest.exit(
            f"refusing to run: {_dbp} already exists and holds league/rec "
            "rows — this looks like the live database. Run tests in a "
            "container without the /data mount.", returncode=2)

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db, demo  # noqa: E402
from app.config import settings  # noqa: E402


@pytest.fixture()
def conn(tmp_path):
    """A fully seeded demo database in a temp dir."""
    old_db = settings.db_path
    settings.db_path = tmp_path / "test.db"
    c = db.connect(settings.db_path)
    db.init_db(c)
    demo.seed(c, force=True)
    yield c
    settings.db_path = old_db
