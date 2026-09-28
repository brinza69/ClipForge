"""A write lock another writer holds is a controlled, temporary refusal (B1-r R4a).

Contract: codex-verdict-wave1.md R4. Only SQLite BUSY/LOCKED on the lock path
(`clip_mutations.begin_write`: every clip edit and PATCH settings) becomes
503 `database_busy` with `{"error","message"}` and a Retry-After, after a
rollback. Any other SQL error stays a 500. The competing writer is a plain
sqlite3 connection holding BEGIN IMMEDIATE on the suite's temporary file; the
app's connection gets a 50 ms busy_timeout for the test, so nothing waits 30 s.
"""
from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import event

from config import settings
from database import engine
from test_clipper_mutation_atomicity import _setup, _state, api  # noqa: F401


@pytest.fixture
async def short_busy_timeout():
    """50 ms instead of 30 s on the connection that runs BEGIN IMMEDIATE. The
    pool is disposed afterwards so no later test inherits it."""
    def shorten(conn, cursor, statement, *_):
        if statement.strip().upper() == "BEGIN IMMEDIATE":
            cursor.execute("PRAGMA busy_timeout=50")

    event.listen(engine.sync_engine, "before_cursor_execute", shorten)
    try:
        yield
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", shorten)
        await engine.dispose()


async def _snapshot(cid, pid):
    from database import async_session
    from models import ProjectModel
    clip, events = await _state(cid)
    async with async_session() as s:
        project = await s.get(ProjectModel, pid)
    return (clip.title, clip.caption_plan, clip.status, events, project.clipper_settings)


@pytest.mark.parametrize("request_kind", ["clip_patch", "approve", "caption_source",
                                          "settings_patch"])
async def test_a_held_write_lock_is_a_controlled_503(api, tmp_path, short_busy_timeout,
                                                      request_kind):
    pid, cid, _ = await _setup(tmp_path, "patch")
    before = await _snapshot(cid, pid)
    send = {
        "clip_patch": lambda: api.patch(f"/api/clipper/clips/{cid}", json={"title": "x"}),
        "approve": lambda: api.post(f"/api/clipper/clips/{cid}/approve"),
        "caption_source": lambda: api.put(f"/api/clipper/clips/{cid}/caption-source",
                                          json={"source_has_burned_captions": True}),
        "settings_patch": lambda: api.patch(f"/api/clipper/projects/{pid}/settings",
                                            json={"settings": {"source_has_burned_captions":
                                                               True}}),
    }[request_kind]

    conn = sqlite3.connect(str(settings.db_path), timeout=0.05, isolation_level=None)
    conn.execute("BEGIN IMMEDIATE")
    try:
        resp = await send()
    finally:
        conn.execute("ROLLBACK")
        conn.close()

    assert resp.status_code == 503, f"{resp.status_code} {resp.text}"
    detail = resp.json()["detail"]
    assert detail["error"] == "database_busy" and detail["message"]
    assert resp.headers.get("retry-after")
    assert await _snapshot(cid, pid) == before, "a refused request wrote something"
    # The lock is gone: the same request now goes through on the same pool.
    assert (await send()).status_code == 200


async def test_another_sql_error_on_the_lock_path_is_not_called_busy(api, tmp_path):
    """An OperationalError that is not BUSY/LOCKED (here: no such table) must
    stay a 500 — reporting it as "try again" would hide a real fault."""
    _pid, cid, _ = await _setup(tmp_path, "patch")

    def break_begin(conn, cursor, statement, parameters, context, executemany):
        if statement.strip().upper() == "BEGIN IMMEDIATE":
            return "SELECT * FROM no_such_table_for_this_test", parameters
        return statement, parameters

    event.listen(engine.sync_engine, "before_cursor_execute", break_begin, retval=True)
    try:
        resp = await api.patch(f"/api/clipper/clips/{cid}", json={"title": "x"})
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", break_begin)
    assert resp.status_code == 500, f"{resp.status_code} {resp.text}"
    assert (await _state(cid))[0].title == "t"


def test_only_busy_and_locked_codes_are_busy():
    from services.clipper.clip_mutations import _is_busy
    from sqlalchemy.exc import OperationalError

    def err(msg, code=None):
        orig = sqlite3.OperationalError(msg)
        if code is not None:
            orig.sqlite_errorcode = code
        return OperationalError("BEGIN IMMEDIATE", {}, orig)

    assert _is_busy(err("database is locked", 5))            # SQLITE_BUSY
    assert _is_busy(err("database table is locked", 6))      # SQLITE_LOCKED
    assert _is_busy(err("database is locked", 5 | (2 << 8)))  # SQLITE_BUSY_SNAPSHOT
    assert _is_busy(err("database is locked"))               # no code: the message
    assert not _is_busy(err("no such table: x", 1))
    assert not _is_busy(err("disk I/O error", 10))
    assert not _is_busy(err("database is locked", 10)), "the code wins over the text"
