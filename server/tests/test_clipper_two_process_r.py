"""Claim versus edit across two OS processes on the suite's temporary DB (B1-r).

Contract: codex-verdict-wave1.md R4 last paragraph — one small probe with two
processes, not the whole matrix. Every other race test uses two connections in
one process; this one proves the claim is serialised by SQLite's file lock,
which is what a second backend (start_all.ps1, port 8421) actually meets.

The child is a second Python process that imports the app and PATCHes a
headline. File barriers stop it INSIDE its transaction (after BEGIN IMMEDIATE,
on the event insert) until the parent says go.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

from test_clipper_mutation_atomicity import _claim, _setup, _state

_SERVER = Path(__file__).resolve().parents[1]

_CHILD = r'''
import asyncio, json, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[3])
barrier, cid = Path(sys.argv[1]), sys.argv[2]

async def main():
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import event
    from database import engine
    from main import app

    def pause(conn, cursor, statement, *a):
        if (statement.lstrip().upper().startswith("INSERT INTO CLIP_FEEDBACK")
                and not (barrier / "locked").exists()):
            (barrier / "locked").write_text(
                str(conn.connection.dbapi_connection._connection.in_transaction))
            deadline = time.monotonic() + 30
            while not (barrier / "go").exists() and time.monotonic() < deadline:
                time.sleep(0.01)

    event.listen(engine.sync_engine, "before_cursor_execute", pause)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.patch(f"/api/clipper/clips/{cid}",
                          json={"headline_text": "from the other process"})
    print(json.dumps({"status": r.status_code}))

asyncio.run(main())
'''


def _spawn(barrier: Path, cid: str) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", _CHILD, str(barrier), cid, str(_SERVER)],
                            cwd=str(_SERVER), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True)


def _result(child: subprocess.Popen) -> int:
    out, err = child.communicate(timeout=90)
    assert child.returncode == 0, err[-2000:]
    return json.loads(out.strip().splitlines()[-1])["status"]


async def test_a_claim_waits_for_an_edit_holding_the_lock_in_another_process(tmp_path):
    _pid, cid, _ = await _setup(tmp_path, "patch")
    barrier = tmp_path / "barrier"
    barrier.mkdir()
    child = _spawn(barrier, cid)
    try:
        deadline = time.monotonic() + 60
        while not (barrier / "locked").exists():
            assert child.poll() is None, child.communicate()[1][-2000:]
            assert time.monotonic() < deadline, "the child never reached its barrier"
            await asyncio.sleep(0.05)
        assert (barrier / "locked").read_text() == "True", "paused outside its transaction"

        claiming = asyncio.ensure_future(_claim(cid))
        await asyncio.sleep(0.5)
        assert not claiming.done(), "the claim committed while the other process held the lock"
        (barrier / "go").write_text("1")
        status = await asyncio.get_running_loop().run_in_executor(None, _result, child)
        claimed = await claiming
    finally:
        if child.poll() is None:
            child.kill()

    assert status == 200 and claimed is True
    row, events = await _state(cid)
    assert row.headline_text == "from the other process" and row.status == "exporting"
    assert events == [("headline_changed", "manual")]


async def test_an_edit_in_another_process_sees_a_claim_committed_first(tmp_path):
    _pid, cid, _ = await _setup(tmp_path, "patch")
    assert await _claim(cid)
    barrier = tmp_path / "barrier"
    barrier.mkdir()
    (barrier / "go").write_text("1")
    status = await asyncio.get_running_loop().run_in_executor(
        None, _result, _spawn(barrier, cid))
    assert status == 409
    row, events = await _state(cid)
    assert row.headline_text is None and row.status == "exporting" and events == []
