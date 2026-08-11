"""Isolates every test under tests/ from the dev ./vital.db.

Sets DATABASE_URL to a temp FILE (not :memory: — SQLite in-memory DBs are
connection-scoped, which would silently break across the app's per-call
SessionLocal() pattern without extra pooling config) before any test module
gets a chance to import app.main / app.db.session. pytest guarantees this
conftest.py is imported before other test modules in this directory, so this
is the only hook point needed — app/db/session.py itself is untouched.
"""

import os
import tempfile

_fd, _TEST_DB_PATH = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH}"

import pytest  # noqa: E402

from app.db.session import Base, engine  # noqa: E402
import app.db.models  # noqa: E402,F401 -- registers tables on Base.metadata

Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def _clean_db():
    """Each test starts with empty tables — cheap enough at this DB size and
    avoids cross-test leakage (e.g. list_archived seeing another test's rows)."""
    yield
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
