"""AQ1: two REAL attempts of one job alive in one queue — the same worker's retry after its lease was
taken back (codex-verdict-next-26 §2, `next26-check/probe_cancel.py`).

The old attempt's every exit — complete, fail, cancel, `finally`, heartbeat, progress, requeue — leaves
the new attempt's row, task, registration, cancel flag and lease exactly as they were. The new
attempt's own heartbeat and completion, and a person's cancel of the current attempt, still work.
Synchronised with Events and task awaits only: nothing sleeps to wait.
"""
from __future__ import annotations

import asyncio
import contextvars
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from config import settings
from database import async_session
from job_attempt import CLAIMED_ATTEMPT, ClaimedAttempt
from job_queue import JobQueue
from models import JobModel

# First in its lane, whatever else the session left queued: other tests leave queued rows at 1990,
# and a tie there let `_process_next` claim one of THEM (seen in the full suite).
OLDEST = datetime(1900, 1, 1)
LONG_AGO = datetime(2000, 1, 1)


class _Gate:
    def __init__(self):
        self.entered, self.go, self.fail = asyncio.Event(), asyncio.Event(), False


async def _two_attempts(monkeypatch, job):
    """Attempt 1 claimed and held; the row taken back to `queued`, as another backend's recovery
    does after an expired lease; the SAME queue claims it again as attempt 2, also held."""
    monkeypatch.setattr(settings, "max_concurrent_jobs", 0)    # heavy lane shut: only ours runs
    gates = {1: _Gate(), 2: _Gate()}

    async def handler(job_id, project_id, clip_id, metadata, queue):
        gate = gates[CLAIMED_ATTEMPT.get().attempt]
        gate.entered.set()
        await gate.go.wait()
        if gate.fail:
            raise RuntimeError("this attempt failed")

    queue = JobQueue()
    queue.worker_id = "w1"
    queue._cleanup_workspace = AsyncMock()
    queue.register_handler("clipper_preview", handler)
    async with async_session() as session:
        session.add(JobModel(id=job, project_id="aq1p", type="clipper_preview", status="queued",
                             metadata_json="{}", created_at=OLDEST))
        await session.commit()
    await queue._process_next()
    t1 = queue._running_jobs[job]
    await gates[1].entered.wait()
    async with async_session() as session:
        row = await session.get(JobModel, job)
        row.status, row.worker_id, row.lease_expires_at = "queued", None, None
        await session.commit()
    await queue._process_next()
    t2 = queue._running_jobs[job]
    await gates[2].entered.wait()
    assert t1 is not t2 and queue._running_attempts[job] == 2
    async with async_session() as session:                  # a mark only a renewal can move
        row = await session.get(JobModel, job)
        row.last_heartbeat = LONG_AGO
        await session.commit()
    return queue, gates, t1, t2


async def _row(job):
    async with async_session() as session:
        j = await session.get(JobModel, job)
        return {k: getattr(j, k) for k in ("status", "worker_id", "attempt_count", "error", "progress",
                                           "progress_message", "lease_expires_at", "last_heartbeat",
                                           "metadata_json", "cancellation_requested")}


def _new_one_intact(queue, job, t2):
    return (not t2.done() and not t2.cancelling() and queue._running_jobs.get(job) is t2
            and queue._running_attempts.get(job) == 2 and not queue.is_cancelled(job))


async def _as(claim: ClaimedAttempt, call):
    """Run `call()` in a task that runs as `claim`, the way `_run`'s own calls do."""
    ctx = contextvars.copy_context()
    ctx.run(CLAIMED_ATTEMPT.set, claim)
    return await asyncio.create_task(call(), context=ctx)


async def _finish_new_one(queue, gates, t2, job):
    """The positive witness: the current attempt still ends its own row."""
    gates[2].go.set()
    await t2
    row = await _row(job)
    assert (row["status"], row["attempt_count"]) == ("done", 2)
    assert job not in queue._running_jobs


@pytest.mark.parametrize("exit_", ["complete", "fail", "cancel"])
async def test_the_old_attempt_ending_leaves_the_new_one_alive_and_its_row_untouched(monkeypatch, exit_):
    job = f"aq1-end-{exit_}"
    queue, gates, t1, t2 = await _two_attempts(monkeypatch, job)
    before = await _row(job)
    if exit_ == "cancel":
        t1.cancel()                     # its `_run` then cancels "its" job as attempt 1
    else:
        gates[1].fail = exit_ == "fail"
        gates[1].go.set()
    await asyncio.gather(t1, return_exceptions=True)        # through its `finally` too
    assert await _row(job) == before
    assert _new_one_intact(queue, job, t2)
    await _finish_new_one(queue, gates, t2, job)


async def test_the_old_heartbeat_renews_nothing_and_stops_only_the_old_task(monkeypatch):
    job = "aq1-heartbeat"
    queue, gates, t1, t2 = await _two_attempts(monkeypatch, job)
    before = await _row(job)
    assert await _as(ClaimedAttempt(job, 1, "w1"), lambda: queue._heartbeat_once(job, t1)) is False
    assert await _row(job) == before                         # no renewal of attempt 2's lease
    await asyncio.gather(t1, return_exceptions=True)
    assert t1.done() and job not in queue._lost_ownership_jobs   # stopped ("stale"), then cleaned up
    assert await _row(job) == before and _new_one_intact(queue, job, t2)
    # the positive witness: attempt 2's heartbeat renews its own lease
    assert await _as(ClaimedAttempt(job, 2, "w1"), lambda: queue._heartbeat_once(job, t2)) is True
    after = await _row(job)
    assert after["last_heartbeat"] > LONG_AGO and after["lease_expires_at"] >= before["lease_expires_at"]
    assert _new_one_intact(queue, job, t2)
    await _finish_new_one(queue, gates, t2, job)


async def test_the_old_progress_and_requeue_move_nothing_of_the_new_attempt(monkeypatch):
    job = "aq1-progress"
    queue, gates, t1, t2 = await _two_attempts(monkeypatch, job)
    before = await _row(job)
    old = ClaimedAttempt(job, 1, "w1")
    await _as(old, lambda: queue.update_progress(job, 0.5, "old attempt"))
    assert await _as(old, lambda: queue._requeue_owned_job(job)) is False
    assert await _row(job) == before and _new_one_intact(queue, job, t2)
    await _as(ClaimedAttempt(job, 2, "w1"), lambda: queue.update_progress(job, 0.6, "new attempt"))
    row = await _row(job)
    assert (row["progress"], row["progress_message"]) == (0.6, "new attempt")
    assert row["last_heartbeat"] > LONG_AGO
    gates[1].go.set()
    await asyncio.gather(t1, return_exceptions=True)
    await _finish_new_one(queue, gates, t2, job)


async def test_a_persons_cancel_stops_the_current_attempt(monkeypatch):
    job = "aq1-person"
    queue, gates, t1, t2 = await _two_attempts(monkeypatch, job)
    await queue.cancel_job(job)                              # what the API's cancel does
    assert t2.cancelling() and queue.is_cancelled(job) and job not in queue._running_jobs
    await asyncio.gather(t2, return_exceptions=True)
    row = await _row(job)
    assert row["status"] == "cancelled" and row["cancellation_requested"]
    gates[1].go.set()                                        # the stale attempt ends later...
    await asyncio.gather(t1, return_exceptions=True)
    assert (await _row(job))["status"] == "cancelled"        # ...and changes nothing
