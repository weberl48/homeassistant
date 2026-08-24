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
