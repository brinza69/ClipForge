"""A submit that fails, and how its outcome is reconciled (R4b, atomic).

Contract: codex-verdict-wave2.md §R4b, which replaces the interim state this
file used to pin (a claim whose enqueue failed stayed `exporting`). The claim,
`clips.export_job_id` and the job row now commit together, so:

- a failure BEFORE the commit leaves neither claim nor job, and nothing is
  "given back": the rollback is the compensation, and it restores no value read
  earlier (Codex probe 2 is in test_clipper_export_atomic_r.py);
- an exception AT the commit does not prove a rollback. The outcome is read back
  by the attempt's job id, under the write lock, where a commit still in flight
  on another connection cannot hide: job present -> its REAL status; absent ->
  503 `export_not_queued` with the clip untouched;
- a failure INSIDE that reconciliation is 503 `export_outcome_unknown` — never
  "no job". Retrying with the same `attempt_id` then answers it.

Manual and auto-export share the path. `_age` stays: codex-bfix-probes.py and
test_clipper_export_atomic_r.py import it.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import async_session, engine
from models import ClipModel, JobModel
from test_clipper_mutation_atomicity import _setup, _state, api  # noqa: F401

_real_commit = AsyncSession.commit
_LONG_AGO_S = 3600      # far past the withdrawn 120 s grace


async def _jobs(cid):
    async with async_session() as s:
        return (await s.execute(select(JobModel.id, JobModel.status)
                                .where(JobModel.clip_id == cid)
                                .order_by(JobModel.created_at))).all()


async def _age(cid, seconds):
    async with async_session() as s:
        await s.execute(update(ClipModel).where(ClipModel.id == cid)
                        .values(updated_at=datetime.utcnow() - timedelta(seconds=seconds)))
        await s.commit()


def _export(api, cid, attempt=None):
    return api.post(f"/api/clipper/clips/{cid}/export",
                    json={"attempt_id": attempt} if attempt else None)


def _attempt():
    import uuid
    return uuid.uuid4().hex[:12]


class _Commit:
    """The first `AsyncSession.commit` after arming raises — after committing
    (`landed=True`) or instead of it. `then` runs between the two, on its own
    session: the job moving on before anyone reconciles it. `break_reconcile`
    makes every later BEGIN IMMEDIATE fail, i.e. reconciliation itself."""

    def __init__(self, monkeypatch, *, landed, then=None, break_reconcile=False):
        self.fired = False

        async def commit(session):
            if self.fired:
                return await _real_commit(session)
            self.fired = True
            if landed:
                await _real_commit(session)
                if then:
                    await then()
            raise RuntimeError("the commit's response was lost")

        def refuse(conn, cursor, statement, *_):
            if self.fired and statement.lstrip().upper().startswith("BEGIN IMMEDIATE"):
                raise RuntimeError("reconciliation could not read")

        monkeypatch.setattr(AsyncSession, "commit", commit)
        if break_reconcile:
            event.listen(engine.sync_engine, "before_cursor_execute", refuse)
            self.stop = lambda: event.remove(engine.sync_engine, "before_cursor_execute", refuse)
        else:
            self.stop = lambda: None


def _refuse(prefix):
    def hook(conn, cursor, statement, *_):
        if statement.lstrip().upper().startswith(prefix):
            raise RuntimeError(f"{prefix} refused by the test")
    return hook


# ---------------------------------------------------------------------------
# failure before the commit
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["candidate", "exported"])
@pytest.mark.parametrize("where", ["claim_update", "job_insert", "commit"])
async def test_a_failure_before_commit_leaves_neither_claim_nor_job(
        api, tmp_path, monkeypatch, where, status):
    _pid, cid, _ = await _setup(tmp_path, "patch", status=status)
    before, _ = await _state(cid)
    hook = None
    if where == "commit":
        _Commit(monkeypatch, landed=False)
    else:
        hook = _refuse("UPDATE CLIPS" if where == "claim_update" else "INSERT INTO JOBS")
        event.listen(engine.sync_engine, "before_cursor_execute", hook)
    try:
        resp = await _export(api, cid)
    finally:
        if hook:
            event.remove(engine.sync_engine, "before_cursor_execute", hook)
    assert resp.status_code == 503, f"{resp.status_code} {resp.text}"
    assert resp.json()["detail"]["error"] == "export_not_queued"
    row, events = await _state(cid)
    assert (row.status, row.export_path, row.export_job_id) == (
        before.status, before.export_path, None)
    assert await _jobs(cid) == [] and events == []

    monkeypatch.setattr(AsyncSession, "commit", _real_commit)       # the retry
    again = await _export(api, cid)
    assert again.status_code == 200, again.text
    assert [j for j, _s in await _jobs(cid)] == [again.json()["job_id"]]


# ---------------------------------------------------------------------------
# failure after the commit: reconciled by the attempt's job id
# ---------------------------------------------------------------------------

async def test_a_raising_commit_that_landed_reports_its_job(api, tmp_path, monkeypatch):
    _pid, cid, _ = await _setup(tmp_path, "patch")
    _Commit(monkeypatch, landed=True)
    attempt = _attempt()
    resp = await _export(api, cid, attempt)
    assert resp.status_code == 200, f"{resp.status_code} {resp.text}"
    assert resp.json() == {"job_id": attempt, "clip_id": cid, "job_status": "queued"}
    assert [tuple(j) for j in await _jobs(cid)] == [(attempt, "queued")]
    row, _ = await _state(cid)
    assert (row.status, row.export_job_id) == ("exporting", attempt)


async def test_a_lost_response_reconciles_to_the_same_job(api, tmp_path):
    """Same attempt_id -> same job, whatever it did since; a NEW intent (a new
    attempt_id) is a new render once the old one is over."""
    _pid, cid, _ = await _setup(tmp_path, "patch")
    attempt = _attempt()
    assert (await _export(api, cid, attempt)).status_code == 200
    again = await _export(api, cid, attempt)
    assert again.status_code == 200, again.text
    assert again.json() == {"job_id": attempt, "clip_id": cid, "job_status": "queued"}
    async with async_session() as s:            # the render finished meanwhile
        await s.execute(update(JobModel).where(JobModel.id == attempt).values(status="done"))
        await s.execute(update(ClipModel).where(ClipModel.id == cid).values(status="exported"))
        await s.commit()
    late = await _export(api, cid, attempt)
    assert late.json()["job_status"] == "done" and len(await _jobs(cid)) == 1
    fresh = await _export(api, cid, _attempt())
    assert fresh.status_code == 200 and len(await _jobs(cid)) == 2


@pytest.mark.parametrize("terminal", ["done", "failed", "cancelled"])
async def test_reconciliation_reports_the_real_state(api, tmp_path, monkeypatch, terminal):
    _pid, cid, _ = await _setup(tmp_path, "patch")
    attempt = _attempt()

    async def moved_on():
        async with async_session() as s:
            await s.execute(update(JobModel).where(JobModel.id == attempt)
                            .values(status=terminal))
            await _real_commit(s)

    _Commit(monkeypatch, landed=True, then=moved_on)
    resp = await _export(api, cid, attempt)
    assert resp.status_code == 200, resp.text
    assert resp.json()["job_status"] == terminal, "reported as something it is not"
    assert [tuple(j) for j in await _jobs(cid)] == [(attempt, terminal)]


@pytest.mark.parametrize("landed", [True, False])
async def test_a_failure_inside_reconciliation_is_unknown_not_absent(
        api, tmp_path, monkeypatch, landed):
    _pid, cid, _ = await _setup(tmp_path, "patch")
    attempt = _attempt()
    commit = _Commit(monkeypatch, landed=landed, break_reconcile=True)
    try:
        resp = await _export(api, cid, attempt)
    finally:
        commit.stop()
    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"]["error"] == "export_outcome_unknown"
    retry = await _export(api, cid, attempt)          # the same attempt answers it
    assert retry.status_code == 200, retry.text
    assert retry.json()["job_id"] == attempt
    assert [j for j, _s in await _jobs(cid)] == [attempt]


async def test_an_old_claim_without_a_job_is_never_taken_again(api, tmp_path):
    """A historical orphan (claimed before R4b, no job). Age is not an attempt's
    identity, so Export refuses it; releasing it is a person's explicit act
    (server/scripts/release_stuck_exports.py)."""
    _pid, cid, _ = await _setup(tmp_path, "patch")
    async with async_session() as s:
        await s.execute(update(ClipModel).where(ClipModel.id == cid).values(status="exporting"))
        await s.commit()
    await _age(cid, _LONG_AGO_S)
    resp = await _export(api, cid)
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == "already_exporting"
    assert await _jobs(cid) == []
    assert (await _state(cid))[0].status == "exporting"


# ---------------------------------------------------------------------------
# auto-export: the same submit and the same reconciliation, per clip
# ---------------------------------------------------------------------------

async def test_auto_export_rolls_back_a_clip_whose_job_row_failed(tmp_path):
    from workers.clipper_finalize import _auto_export

    pid, cid, _ = await _setup(tmp_path, "patch")
    hook = _refuse("INSERT INTO JOBS")
    event.listen(engine.sync_engine, "before_cursor_execute", hook)
    try:
        assert await _auto_export(pid, {"auto_export": 1}, None) == 0
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", hook)
    row, _ = await _state(cid)
    assert (row.status, row.export_job_id) == ("candidate", None)
    assert await _jobs(cid) == []


async def test_auto_export_reconciles_a_commit_that_landed(tmp_path, monkeypatch):
    from workers.clipper_finalize import _auto_export

    pid, cid, _ = await _setup(tmp_path, "patch")
    _Commit(monkeypatch, landed=True)
    assert await _auto_export(pid, {"auto_export": 1}, None) == 1
    jobs = await _jobs(cid)
    row, _ = await _state(cid)
    assert len(jobs) == 1 and (row.status, row.export_job_id) == ("exporting", jobs[0][0])
