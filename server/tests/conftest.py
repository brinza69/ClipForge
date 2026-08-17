"""
Pytest fixtures for ClipForge API smoke tests.

Uses httpx ASGITransport to drive the FastAPI app in-process — no running
server, no network. The lifespan (DB init, job-queue start) runs via the
transport so the endpoints behave as in production.

THE SUITE RUNS AGAINST A THROWAWAY DATA DIRECTORY. It did not always: until
2026-08-16 every test shared the real `data/db/clipforge.db`, so any test that
reached a write wrote into the operator's actual projects. That is not
hypothetical — a test of the transcribe handler left a stray `p1` row in the
`transcripts` table of a database holding five real sources and 900 scored
clips, and it was found by accident.

The redirection has to happen HERE and at import time. `database.py` builds its
engine from `settings.db_path` at module scope, so by the time any fixture
runs, the connection has already been made to whatever `CLIPFORGE_DATA_DIR`
said. conftest is imported before the test modules, and `config` is not
importable before the sys.path line below, which is what makes this the only
correct place for it.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# Make `import main`, `import config`, etc. work when pytest is run from the
# repo root or from server/.
_SERVER_DIR = Path(__file__).resolve().parent.parent
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

# Set BEFORE config is importable, never inside a fixture. Honoured if the
# caller already pointed somewhere themselves.
_OWNED_TMP = None
if not os.environ.get("CLIPFORGE_DATA_DIR"):
    _OWNED_TMP = tempfile.mkdtemp(prefix="clipforge_tests_")
    os.environ["CLIPFORGE_DATA_DIR"] = _OWNED_TMP

# Import config only NOW, with the environment already pointing at the copy,
# and lay out the tree it expects. `database.py` opens its engine at import
# time and sqlite will not create a missing parent directory for it.
from config import settings  # noqa: E402 — ordering is the whole point

settings.ensure_dirs()


def _create_schema() -> None:
    """Build the tables the suite is about to use.

    httpx's ASGITransport does NOT run lifespan events, so `init_db()` has
    never actually executed during a test run — the suite passed because it was
    reusing a real database that already had every table. Against a fresh one
    the first query fails with "no such table: projects".

    The engine is disposed afterwards so no pooled connection is left bound to
    this throwaway event loop.
    """
    import asyncio

    async def _prepare():
        import models  # noqa: F401 — registers every table on Base.metadata
        from database import engine, init_db

        await init_db()
        await engine.dispose()

    asyncio.run(_prepare())


_create_schema()


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001 — pytest hook
    if _OWNED_TMP:
        shutil.rmtree(_OWNED_TMP, ignore_errors=True)


@pytest.fixture(scope="session", autouse=True)
def _never_the_real_database():
    """Fail loudly rather than write into real data.

    An assertion rather than a comment because the failure it guards against is
    silent: a test that writes to the operator's database still passes.
    """
    from config import settings

    real = (_SERVER_DIR.parent / "data").resolve()
    used = Path(settings.data_dir).resolve()
    assert used != real, (
        f"the test suite is pointing at the real data directory ({used}). "
        "Something imported `config` before conftest could redirect it."
    )
    yield


@pytest.fixture
async def client():
    from httpx import ASGITransport, AsyncClient
    from main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
