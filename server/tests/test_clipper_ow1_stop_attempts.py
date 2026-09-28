"""OW1r2 (codex-verdict-next-27 §4 R3): the stop token follows AQ1's identity.

Two REAL attempts of one job in ONE queue, same worker (the setup of test_job_attempt_overlap.py).
Each attempt registers its own token exactly as `handle_analyze` does. The handler's own `release`
in its `finally` is HELD, so every Event seen set here was set by the QUEUE (M36/M37: the effect is
proven apart from `finally`). Synchronised with Events and task awaits only.
"""
from __future__ import annotations

import asyncio
import contextvars
import threading
from datetime import datetime
from unittest.mock import AsyncMock

from config import settings
from database import async_session
from job_attempt import CLAIMED_ATTEMPT, ClaimedAttempt
from job_queue import JobQueue
from models import JobModel
from services.clipper import attempt_stop

OLDEST = datetime(1900, 1, 1)


class _Attempt:
    def __init__(self):
        self.entered, self.go, self.release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        self.stop: threading.Event | None = None


async def _two_attempts(monkeypatch, job):
    monkeypatch.setattr(settings, "max_concurrent_jobs", 0)
    att = {1: _Attempt(), 2: _Attempt()}

    async def handler(job_id, project_id, clip_id, metadata, queue):
        claimed = CLAIMED_ATTEMPT.get()
        a = att[claimed.attempt]
        a.stop = attempt_stop.register(queue, job_id, claimed.attempt)
        a.entered.set()
        try:
            await a.go.wait()
        finally:
            await a.release.wait()             # the handler's own release, held by the test
            attempt_stop.release(queue, job_id, claimed.attempt, a.stop)

    queue = JobQueue()
    queue.worker_id = "w1"
    queue.HEARTBEAT_SECONDS = 300
    queue._cleanup_workspace = AsyncMock()
    queue.register_handler("clipper_preview", handler)
    async with async_session() as session:
        session.add(JobModel(id=job, project_id="ow1r2p", type="clipper_preview", status="queued",
                             metadata_json="{}", created_at=OLDEST))
        await session.commit()
    await queue._process_next()
    t1 = queue._running_jobs[job]
    await att[1].entered.wait()
    async with async_session() as session:
        row = await session.get(JobModel, job)
        row.status, row.worker_id, row.lease_expires_at = "queued", None, None
        await session.commit()
    await queue._process_next()
    t2 = queue._running_jobs[job]
    await att[2].entered.wait()
    assert t1 is not t2 and queue._running_attempts[job] == 2
    assert att[1].stop is not att[2].stop
    return queue, att, t1, t2


async def _as(claim: ClaimedAttempt, call):
    ctx = contextvars.copy_context()
    ctx.run(CLAIMED_ATTEMPT.set, claim)
    return await asyncio.create_task(call(), context=ctx)


async def _end(att, *tasks):
    for a in att.values():
        a.go.set()
        a.release.set()
    await asyncio.gather(*tasks, return_exceptions=True)


async def _status(job):
    async with async_session() as session:
        return (await session.get(JobModel, job)).status


async def test_an_old_attempts_refused_cancel_signals_no_one_and_the_current_ones_signals_itself(monkeypatch):
    job = "ow1r2-stop-cancel"
    queue, att, t1, t2 = await _two_attempts(monkeypatch, job)
    await queue.cancel_job(job, owner_id="w1", attempt=1)     # refused: the row is attempt 2
    assert not att[2].stop.is_set(), "an old attempt's refused cancel stopped its replacement"
    assert not att[1].stop.is_set(), "a refused cancel signals nothing"
    assert await _status(job) == "running"
    # the positive witness: attempt 2 ending itself, the row confirms it — the attempt-scoped cancel
    # moves no task, so only the queue's signal can have set this Event
    await queue.cancel_job(job, owner_id="w1", attempt=2)
    assert att[2].stop.is_set() and not att[1].stop.is_set()
    assert not t2.done() and not t2.cancelling()
    await _end(att, t1, t2)


async def test_an_attempt_scoped_cancel_signals_its_attempt_even_when_no_task_is_registered(monkeypatch):
    job = "ow1r2-stop-unregistered"
    queue, att, t1, t2 = await _two_attempts(monkeypatch, job)
    queue._unregister(job, 2)                                 # the registration is gone (AQ1's pops)
    assert job not in queue._running_jobs
    await queue.cancel_job(job, owner_id="w1", attempt=2)
    assert await _status(job) == "cancelled"
    assert att[2].stop.is_set(), "the row confirmed attempt 2: its threads are told"
    assert not att[1].stop.is_set()
    await _end(att, t1, t2)


async def test_an_old_heartbeat_signals_its_own_attempt_never_the_replacement(monkeypatch):
    job = "ow1r2-stop-heartbeat"
    queue, att, t1, t2 = await _two_attempts(monkeypatch, job)
    assert await _as(ClaimedAttempt(job, 1, "w1"), lambda: queue._heartbeat_once(job, t1)) is False
    assert att[1].stop.is_set(), "the loss reached attempt 1's threads before its finally ran"
    assert not att[1].release.is_set() and not t1.done()      # its finally is still held
    assert not att[2].stop.is_set(), "an old heartbeat stopped the replacement"
    # the positive witness: attempt 2's heartbeat renews and signals nothing
    assert await _as(ClaimedAttempt(job, 2, "w1"), lambda: queue._heartbeat_once(job, t2)) is True
    assert not att[2].stop.is_set()
    await _end(att, t1, t2)


async def test_a_heartbeat_loss_with_no_owner_signals_the_registered_attempt(monkeypatch):
    job = "ow1r2-stop-direct"
    queue, att, t1, t2 = await _two_attempts(monkeypatch, job)
    async with async_session() as session:
        row = await session.get(JobModel, job)
        row.worker_id = "someone-else"
        await session.commit()
    assert await queue._heartbeat_once(job) is False          # a direct call: no claim, no owner
    assert att[2].stop.is_set() and not att[1].stop.is_set()
    assert not att[2].release.is_set()
    await _end(att, t1, t2)


async def test_a_persons_cancel_signals_the_registered_attempt_before_its_finally(monkeypatch):
    job = "ow1r2-stop-person"
    queue, att, t1, t2 = await _two_attempts(monkeypatch, job)
    await queue.cancel_job(job)                               # what the API's cancel does
    assert att[2].stop.is_set(), "the person's cancel reached the current attempt's threads"
    assert not t2.done(), "its finally is still held: the Event was set by the queue"
    assert not att[1].stop.is_set(), "the old attempt is not the person's target"
    assert await _status(job) == "cancelled"
    await _end(att, t1, t2)
    assert (job, 1) not in queue.__dict__.get("_stop_tokens", {})
    assert (job, 2) not in queue.__dict__.get("_stop_tokens", {})
