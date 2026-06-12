"""
Pytest fixtures for ClipForge API smoke tests.

Uses httpx ASGITransport to drive the FastAPI app in-process — no running
server, no network. The lifespan (DB init, job-queue start) runs via the
transport so the endpoints behave as in production.
"""

import sys
from pathlib import Path

import pytest

# Make `import main`, `import config`, etc. work when pytest is run from the
# repo root or from server/.
_SERVER_DIR = Path(__file__).resolve().parent.parent
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))


@pytest.fixture
async def client():
    from httpx import ASGITransport, AsyncClient
    from main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
