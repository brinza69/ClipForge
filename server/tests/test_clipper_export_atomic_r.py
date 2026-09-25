"""R4b: an export's claim, its attempt identity and its job row are ONE commit.

Contract: data/claude-master-20260924/codex-verdict-wave2.md §R4b, and the
table "Probe obligatorii pentru R4b corectat" in codex-verdict-bfix.md. Codex's
two counterexamples (codex-bfix-probes.py) are here too, adapted to the atomic
submit: the suspension they modelled between the claim and the job now falls
INSIDE the transaction, so it holds SQLite's write lock and the retry waits.
They are not relaxed: each still asserts one job, and an edit that survives.

Failure/reconciliation cases are in test_clipper_claim_enqueue_r.py; what fail,
cancel, recovery and a finishing render may do to a clip is in
test_clipper_export_identity_r.py.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.util import await_only

from database import async_session, engine
from models import ClipModel, JobModel
from test_clipper_mutation_atomicity import (FIRST_WRITE, Gate, _rescore, _setup,  # noqa: F401
                                             _state, api, interleave)

_SERVER = Path(__file__).resolve().parents[1]
_WAIT = 10.0             # seconds; a barrier that is never reached fails, not hangs


def _attempt() -> str:
    return uuid.uuid4().hex[:12]


def _export(api, cid, attempt: str | None = None):
    body = {"attempt_id": attempt} if attempt else None
    return api.post(f"/api/clipper/clips/{cid}/export", json=body)


async def _jobs(cid: str) -> list[tuple]:
    async with async_session() as s:
        return [tuple(r) for r in (await s.execute(
            select(JobModel.id, JobModel.status, JobModel.type)
            .where(JobModel.clip_id == cid).order_by(JobModel.created_at))).all()]


class Hold:
    """One-shot: pause the first statement `match` accepts, in a transaction or
    not, and say which. Unlike `Gate`, a pause INSIDE a transaction is the
    point: it holds the write lock, and a competitor has to wait on it."""

    def __init__(self, match):
        self.match, self.in_tx = match, None
        self.reached, self.release = asyncio.Event(), asyncio.Event()

    def __call__(self, conn, cursor, statement, *_):
        if self.in_tx is not None or not self.match(statement.lstrip().upper()):
            return
        self.in_tx = conn.connection.dbapi_connection._connection.in_transaction
        self.reached.set()
        await_only(self.release.wait())


class CommitHold:
    """The same, at the first `AsyncSession.commit` after arming: the job row
    is flushed, the transaction is open, nothing is committed yet."""

    def __init__(self, monkeypatch):
        self.reached, self.release, self.used = asyncio.Event(), asyncio.Event(), False
        real = AsyncSession.commit

        async def commit(session):
            if not self.used:
                self.used = True
                self.reached.set()
                await self.release.wait()
            return await real(session)

        monkeypatch.setattr(AsyncSession, "commit", commit)


# ---------------------------------------------------------------------------
# a first request suspended, a retry, the first resumed
# ---------------------------------------------------------------------------

_BEGIN = lambda s: s.startswith("BEGIN IMMEDIATE")          # noqa: E731
_JOB_INSERT = lambda s: s.startswith("INSERT INTO JOBS")    # noqa: E731


@pytest.mark.parametrize("where", ["before_begin", "after_claim", "after_job_row"])
async def test_a_suspended_submit_and_a_retry_accept_one_attempt(api, tmp_path, monkeypatch, where):
    """At most one 200 and one job; the other is 409 `already_exporting`."""
    _pid, cid, _ = await _setup(tmp_path, "patch")
    hold = (CommitHold(monkeypatch) if where == "after_job_row"
            else Hold(_BEGIN if where == "before_begin" else _JOB_INSERT))
    if isinstance(hold, Hold):
        event.listen(engine.sync_engine, "before_cursor_execute", hold)
    try:
        first = asyncio.ensure_future(_export(api, cid))
        await asyncio.wait_for(hold.reached.wait(), _WAIT)
        if where == "after_claim":
            assert hold.in_tx, "the job insert is outside the claim's transaction"
        retry = asyncio.ensure_future(_export(api, cid))
        if where != "before_begin":
            await asyncio.sleep(0.3)
            assert not retry.done(), "the retry decided while the first held the lock"
            hold.release.set()
        else:
            await asyncio.wait_for(asyncio.shield(retry), _WAIT)
            hold.release.set()
        original, again = await first, await retry
    finally:
        hold.release.set()
        if isinstance(hold, Hold):
            event.remove(engine.sync_engine, "before_cursor_execute", hold)

    codes = sorted([original.status_code, again.status_code])
    assert codes == [200, 409], f"{original.text} / {again.text}"
    winner = original if original.status_code == 200 else again
    loser = again if winner is original else original
    assert loser.json()["detail"]["error"] == "already_exporting"
    jobs = await _jobs(cid)
    assert [j[0] for j in jobs] == [winner.json()["job_id"]], jobs
    row, _ = await _state(cid)
    assert (row.status, row.export_job_id) == ("exporting", winner.json()["job_id"])


# ---------------------------------------------------------------------------
# Codex's two counterexamples, adapted to the atomic submit
# ---------------------------------------------------------------------------

_GRACE_S = 120          # the withdrawn ORPHAN_CLAIM_GRACE_S, as a literal (Bfix2)


@pytest.mark.parametrize("where", ["inside_transaction", "before_transaction"])
async def test_codex_probe_1_a_delayed_original_and_an_aged_retry_make_one_job(
        api, tmp_path, where):
    """Probe 1: the original is SUSPENDED where it used to be — after its claim,
    before its job row — its claim "ages" past the old grace, a retry comes.
    Inside the transaction the age write and the retry both wait for the lock;
    before it, the retry wins outright and the original is refused."""
    from test_clipper_claim_enqueue_r import _age

    _pid, cid, _ = await _setup(tmp_path, "patch")
    hold = Hold(_JOB_INSERT if where == "inside_transaction" else _BEGIN)
    event.listen(engine.sync_engine, "before_cursor_execute", hold)
    try:
        first = asyncio.ensure_future(_export(api, cid))
        await asyncio.wait_for(hold.reached.wait(), _WAIT)
        aged = asyncio.ensure_future(_age(cid, _GRACE_S + 5))
        second = asyncio.ensure_future(_export(api, cid))
        if where == "before_transaction":
            await asyncio.wait_for(asyncio.gather(aged, asyncio.shield(second)), _WAIT)
        hold.release.set()
        first_response, retry = await first, await second
        await aged
    finally:
        hold.release.set()
        event.remove(engine.sync_engine, "before_cursor_execute", hold)
    jobs = await _jobs(cid)
    assert len(jobs) == 1, (f"original={first_response.status_code}, "
                            f"retry={retry.status_code}, jobs={jobs}")
    assert sorted([first_response.status_code, retry.status_code]) == [200, 409]


async def test_codex_probe_2_a_failed_submit_does_not_restore_a_stale_status(api, tmp_path):
    """Probe 2: an `exported` clip; an edit is accepted between the export's
    first step and its write; the job row then fails to persist. The edit —
    `approved`, no export_path, its event — is what stays, with no job."""
    _pid, cid, _ = await _setup(tmp_path, "patch", status="exported")

    def refuse(conn, cursor, statement, *_):
        if _JOB_INSERT(statement.lstrip().upper()):
            raise RuntimeError("the job row did not persist")

    event.listen(engine.sync_engine, "before_cursor_execute", refuse)
    try:
        gate = Gate(FIRST_WRITE)
        exported, edited = await interleave(
            _export(api, cid), gate,
            lambda: api.put(f"/api/clipper/clips/{cid}/caption-source",
                            json={"source_has_burned_captions": True}))
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", refuse)
    assert gate.window and edited.status_code == 200, edited.text
    assert exported.status_code == 503, exported.text
    assert exported.json()["detail"]["error"] == "export_not_queued"
    row, events = await _state(cid)
    assert (row.status, row.export_path) == ("approved", None), (
        f"status={row.status}, export_path={row.export_path}, events={events}")
    assert events == [("caption_changed", "manual")]
    assert await _jobs(cid) == [] and row.export_job_id is None


# ---------------------------------------------------------------------------
# an exported clip whose job still runs; attempt ids
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("job_status", ["queued", "running"])
async def test_an_exported_clip_whose_export_job_is_live_refuses_a_submit(
        api, tmp_path, job_status):
    """`handle_export` commits `exported` BEFORE its job is terminal (Codex,
    wave 2 (b)); that window must not accept a second render."""
    _pid, cid, _ = await _setup(tmp_path, "patch")
    first = await _export(api, cid)
    assert first.status_code == 200, first.text
    async with async_session() as s:
        await s.execute(update(JobModel).where(JobModel.id == first.json()["job_id"])
                        .values(status=job_status))
        await s.execute(update(ClipModel).where(ClipModel.id == cid)
                        .values(status="exported", export_path="/tmp/x.mp4"))
        await s.commit()
    again = await _export(api, cid)
    assert again.status_code == 409, again.text
    assert again.json()["detail"]["error"] == "already_exporting"
    assert len(await _jobs(cid)) == 1
    assert (await _state(cid))[0].status == "exported"


async def test_an_attempt_id_is_the_job_id_and_is_bound_to_its_clip(api, tmp_path):
    _pid, a, _ = await _setup(tmp_path, "patch")
    _pid2, b, _ = await _setup(tmp_path, "patch")
    attempt = _attempt()
    first = await _export(api, a, attempt)
    assert first.status_code == 200, first.text
    assert first.json() == {"job_id": attempt, "clip_id": a, "job_status": "queued"}
    other = await _export(api, b, attempt)
    assert other.status_code == 409, other.text
    assert other.json()["detail"]["error"] == "attempt_id_conflict"
    assert await _jobs(b) == [] and (await _state(b))[0].status == "candidate"
    for bad in ("x" * 12, "ABCDEF123456", "abc", 12):
        r = await _export(api, a, bad)
        assert r.status_code == 400, f"{bad!r}: {r.text}"
    assert len(await _jobs(a)) == 1


# ---------------------------------------------------------------------------
# two OS processes submit at once
# ---------------------------------------------------------------------------

_CHILD = r'''
import asyncio, json, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
barrier, mode, pid, cid = Path(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5]

async def main():
    from sqlalchemy import event
    from database import engine

    def pause(conn, cursor, statement, *a):
        if (statement.lstrip().upper().startswith("INSERT INTO JOBS")
                and not (barrier / "locked").exists()):
            (barrier / "locked").write_text(
                str(conn.connection.dbapi_connection._connection.in_transaction))
            deadline = time.monotonic() + 60
            while not (barrier / "go").exists() and time.monotonic() < deadline:
                time.sleep(0.01)

    event.listen(engine.sync_engine, "before_cursor_execute", pause)
    if mode == "manual":
        from httpx import ASGITransport, AsyncClient
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(f"/api/clipper/clips/{cid}/export")
        print(json.dumps({"status": r.status_code, "job_id": r.json().get("job_id")}))
    else:
        from workers.clipper_finalize import _auto_export
        print(json.dumps({"queued": await _auto_export(pid, {"auto_export": 1}, None)}))

asyncio.run(main())
'''


@pytest.mark.parametrize("child_mode", ["manual", "auto"])
async def test_two_processes_accept_one_submit(api, tmp_path, child_mode):
    """The other process holds the write lock inside its submit (after the
    claim, on the job insert); this one's manual Export waits for it, then
    sees a live export and is refused. One job, one durable acceptance."""
    pid, cid, _ = await _setup(tmp_path, "patch")
    barrier = tmp_path / "barrier"
    barrier.mkdir()
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD, str(_SERVER), str(barrier), child_mode, pid, cid],
        cwd=str(_SERVER), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 60
        while not (barrier / "locked").exists():
            assert child.poll() is None, child.communicate()[1][-2000:]
            assert time.monotonic() < deadline, "the child never reached its barrier"
            await asyncio.sleep(0.05)
        assert (barrier / "locked").read_text() == "True", "paused outside its transaction"
        mine = asyncio.ensure_future(_export(api, cid))
        await asyncio.sleep(0.5)
        assert not mine.done(), "this submit decided while the other process held the lock"
        (barrier / "go").write_text("1")
        out, err = await asyncio.get_running_loop().run_in_executor(
            None, lambda: child.communicate(timeout=90))
        assert child.returncode == 0, err[-2000:]
        theirs = json.loads(out.strip().splitlines()[-1])
        resp = await mine
    finally:
        if child.poll() is None:
            child.kill()
    assert theirs.get("status") == 200 if child_mode == "manual" else theirs == {"queued": 1}
    assert resp.status_code == 409 and resp.json()["detail"]["error"] == "already_exporting"
    jobs = await _jobs(cid)
    row, _ = await _state(cid)
    assert len(jobs) == 1 and row.export_job_id == jobs[0][0]
    if child_mode == "manual":
        assert theirs["job_id"] == jobs[0][0]


# ---------------------------------------------------------------------------
# a rescore before / after the submit's commit
# ---------------------------------------------------------------------------

async def test_a_rescore_before_the_submit_commit_refuses_it_explicitly(api, tmp_path):
    pid, cid, _ = await _setup(tmp_path, "patch")
    gate = Gate(FIRST_WRITE)
    resp, _ = await interleave(_export(api, cid), gate, lambda: _rescore(pid))
    assert gate.window, "the submit wrote before the barrier: the race was not forced"
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["error"] == "clip_not_found"
    assert await _jobs(cid) == []


async def test_a_rescore_after_the_submit_commit_keeps_the_active_export(api, tmp_path):
    pid, cid, _ = await _setup(tmp_path, "patch")
    resp = await _export(api, cid)
    assert resp.status_code == 200, resp.text
    await _rescore(pid)
    row, _ = await _state(cid)
    assert row is not None and row.status == "exporting"
    assert row.export_job_id == resp.json()["job_id"]
    assert [(j[0], j[1]) for j in await _jobs(cid)] == [(resp.json()["job_id"], "queued")]


# ---------------------------------------------------------------------------
# one authority for the job row
# ---------------------------------------------------------------------------

async def test_enqueue_and_submit_export_build_the_same_row():
    import inspect

    from job_queue import JobQueue, add_job, job_queue

    kw = dict(project_id="authority-p", job_type="clipper_export",
              clip_id="authority-c", metadata={"origin": "manual"})
    a = await job_queue.enqueue(**kw)
    async with async_session() as s:
        b = (await add_job(s, **kw, job_id=_attempt())).id
        await s.commit()
    async with async_session() as s:
        rows = [await s.get(JobModel, i) for i in (a, b)]
    skip = {"id", "created_at", "updated_at"}
    cols = [c.name for c in JobModel.__table__.columns if c.name not in skip]
    assert {c: getattr(rows[0], c) for c in cols} == {c: getattr(rows[1], c) for c in cols}
    src = inspect.getsource(JobQueue.enqueue)
    assert "new_job_row(" in src and "JobModel(" not in src


async def test_the_export_job_row_carries_who_asked(api, tmp_path):
    """Review F4 (probe P5). `_job_origin` reads this; a missing or wrong stamp
    would file auto-exports as human approvals, or approvals as `system`."""
    from workers.clipper_finalize import _auto_export

    _pid, manual, _ = await _setup(tmp_path, "patch")
    job = (await _export(api, manual)).json()["job_id"]
    pid2, auto, _ = await _setup(tmp_path, "patch")
    assert await _auto_export(pid2, {"auto_export": 1}, None) == 1
    async with async_session() as s:
        got = {cid: json.loads(m) if m else None for cid, m in (await s.execute(
            select(JobModel.clip_id, JobModel.metadata_json)
            .where(JobModel.clip_id.in_((manual, auto))))).all()}
    assert got == {manual: {"origin": "manual"}, auto: {"origin": "auto"}}, (job, got)
