"""BURST R1c: a preview handler runs as the attempt the QUEUE CLAIMED — never as the row says later.

codex-verdict-next-25 §1 (`next25-check/probe_capture.py`): the handler read `{worker, attempt}` from
the job row AFTER another claim had taken the job, and published its own bytes under the NEW
attempt's identity. Now `_claim` returns the attempt its UPDATE wrote and `_run` sets it in the
handler's own task. These go through the real `JobQueue._process_next` → `_run` → `handle_preview`
(the encoder stubbed); the takeover is the probe's: the row changes while the handler reads inputs.
"""
from __future__ import annotations

import asyncio
import datetime as dt
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from config import settings
from database import async_session
from job_attempt import CLAIMED_ATTEMPT, ClaimedAttempt
from job_queue import JobQueue
from models import JobModel
from services.clipper import storage
from test_clipper_caption_display_api import WELL_TIMED, _built, _render, _seed
from test_clipper_preview_attempt_r import A, _publish, _selected, _set_job
from workers import clipper_preview_publish as pub
from workers import clipper_render_jobs as jobs

OLDEST = dt.datetime(1990, 1, 1)    # first in its lane, whatever else the session left queued


async def _queued(ident):
    """A clip and ONE queued preview job for it, as the API enqueues it."""
    cid = await _seed(ident, _built(WELL_TIMED), status="approved", exported=False)
    async with async_session() as session:
        session.add(JobModel(id=f"{ident}j", project_id=ident, clip_id=cid, type="clipper_preview",
                             status="queued", metadata_json="{}", created_at=OLDEST))
        await session.commit()
    return cid, f"{ident}j"


def _stub_encoder(monkeypatch, calls=None):
    import services.clipper.render as static_render

    async def encode(_src, _cand, _plan, _ass, out, **_kw):
        if calls is not None:
            calls.append(out)
        Path(out).write_bytes(b"this-invocation")

    monkeypatch.setattr(static_render, "render_preview", encode)
    monkeypatch.setattr(jobs, "_source_path", lambda _: "unused.mp4")


def _queue(monkeypatch, worker="w1"):
    _stub_encoder(monkeypatch)
    monkeypatch.setattr(settings, "max_concurrent_jobs", 0)    # heavy lane shut: only ours is picked
    queue = JobQueue()
    queue.worker_id = worker
    queue._cleanup_workspace = AsyncMock()
    queue.register_handler("clipper_preview", jobs.handle_preview)
    return queue


async def _run_once(queue, job):
    """`_process_next` claims the job and starts `_run`; wait for that task to end."""
    await queue._process_next()
    await asyncio.gather(queue._running_jobs[job], return_exceptions=True)


async def _row(job):
    async with async_session() as session:
        j = await session.get(JobModel, job)
        return {"status": j.status, "worker": j.worker_id, "attempt": j.attempt_count,
                "error": j.error, "metadata": j.metadata_json}


@pytest.mark.parametrize("new_worker", ["w2", "w1"], ids=["another_worker", "same_worker_retry"])
async def test_a_takeover_while_the_handler_reads_its_inputs_selects_nothing(monkeypatch, tmp_path,
                                                                             new_worker):
    ident = f"rc1{new_worker}"
    cid, job = await _queued(ident)
    queue = _queue(monkeypatch)
    real_load = jobs._load
    after = {}

    async def load_then_taken_over(clip_id):
        loaded = await real_load(clip_id)
        # The probe's point: another claim took the job as attempt 2 while this handler read its
        # inputs, and that attempt then published its own preview.
        await _set_job(job, worker_id=new_worker, attempt_count=2,
                       lease_expires_at=dt.datetime.utcnow() + dt.timedelta(minutes=10))
        cause, _ = await _publish(ident, cid, A(job, 2, new_worker), _render(_built(WELL_TIMED)),
                                  tmp_path, b"new-attempt")
        assert cause is None
        after["selected"], after["row"] = await _selected(cid), await _row(job)
        return loaded

    monkeypatch.setattr(jobs, "_load", load_then_taken_over)
    await _run_once(queue, job)

    path, record = await _selected(cid)
    assert (path, record) == after["selected"]
    assert (record["job_id"], record["attempt"], record["worker"]) == (job, 2, new_worker)
    assert Path(path).read_bytes() == b"new-attempt"
    assert await _row(job) == after["row"]      # still running as attempt 2: status, error, metadata
    mine = pub.attempt_path(storage.preview_path(ident, cid), A(job, 1, "w1"))
    assert not mine.exists(), "the old attempt left its unselected file behind"


async def test_the_claimed_attempt_publishes_under_its_own_identity(monkeypatch):
    ident = "rc1ok"
    cid, job = await _queued(ident)
    await _run_once(_queue(monkeypatch), job)
    path, record = await _selected(cid)
    assert (record["job_id"], record["attempt"], record["worker"]) == (job, 1, "w1")
    assert path == str(pub.attempt_path(storage.preview_path(ident, cid), A(job, 1, "w1")))
    assert Path(path).read_bytes() == b"this-invocation"
    row = await _row(job)
    assert (row["status"], row["worker"], row["attempt"]) == ("done", "w1", 1)


class _Queue:
    worker_id = "w1"

    async def update_progress(self, *_):
        pass

    def is_cancelled(self, _):
        return False


@pytest.mark.parametrize("claim", [None, ClaimedAttempt("another-job", 1, "w1")],
                         ids=["no_claim", "another_jobs_claim"])
async def test_a_handler_not_running_this_jobs_claim_refuses_before_rendering(monkeypatch, claim):
    ident = "rc1none" if claim is None else "rc1other"
    cid, job = await _queued(ident)
    rendered = []
    _stub_encoder(monkeypatch, rendered)
    assert CLAIMED_ATTEMPT.get() is None
    if claim is not None:
        CLAIMED_ATTEMPT.set(claim)
    with pytest.raises(RuntimeError, match="no claimed attempt"):
        await jobs.handle_preview(job, ident, cid, {}, _Queue())
    assert rendered == [] and await _selected(cid) == (None, None)


@pytest.mark.parametrize("end,status", [("complete", "done"), ("fail", "failed"),
                                        ("cancel", "cancelled")])
async def test_only_the_current_attempt_ends_the_row(end, status):
    """The same worker's retry is attempt 2: attempt 1's end must not touch it; attempt 2's does."""
    queue = JobQueue()
    queue.worker_id = "w1"
    queue._cleanup_workspace = AsyncMock()
    job = f"rc1end{end}"
    async with async_session() as session:
        session.add(JobModel(id=job, project_id="rc1p", type="clipper_preview", status="queued",
                             metadata_json="{}"))
        await session.commit()
    async with async_session() as session:
        first = await queue._claim(session, job)
    await _set_job(job, status="queued")                      # requeued, e.g. by recovery
    async with async_session() as session:
        second = await queue._claim(session, job)
    assert (first, second) == (ClaimedAttempt(job, 1, "w1"), ClaimedAttempt(job, 2, "w1"))

    async def finish(attempt):
        mine = {"owner_id": "w1", "attempt": attempt.attempt}
        if end == "complete":
            await queue.complete_job(job, **mine)
        elif end == "fail":
            await queue.fail_job(job, "late", **mine)
        else:
            await queue.cancel_job(job, **mine)

    await finish(first)
    row = await _row(job)
    assert (row["status"], row["worker"], row["attempt"], row["error"]) == ("running", "w1", 2, None)
    await finish(second)
    assert (await _row(job))["status"] == status
