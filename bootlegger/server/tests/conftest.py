import os as _os

import pytest as _pytest

# Structural guard against a mistake made twice this project: running the
# suite inside a live-configured container (real league env + mounted live
# DB) sends test traffic to real APIs and produces phantom failures.
if _os.environ.get("SLEEPER_LEAGUE_ID"):
    _pytest.exit(
        "refusing to run: SLEEPER_LEAGUE_ID is set. Run tests in a clean "
        "container (no live env vars, no /data mount).", returncode=2)

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
