"""A heartbeat renews the lease to exactly its own time plus the lease, and only
for the worker that owns the job.

This replaced `test_job_claim.py::test_heartbeat_renews_lease_and_fences_old_owner`,
which asserted `renewed.lease_expires_at > before.lease_expires_at` on the real
clock and failed whenever the claim and the heartbeat landed in the same
microsecond. `>=` would have stopped the flake and also passed a heartbeat that
renews nothing. So the clock is controlled instead: the claim reads T0, the
heartbeat reads T1, and the row must carry exactly T1 + LEASE_SECONDS and
last_heartbeat T1. When both read the same tick the equal expiry is the correct
answer, and is tested as such.

`_claim` and `_heartbeat_once` both read `job_recovery.datetime.utcnow()` since
R1c moved the claim there (`job_queue.datetime` is patched as well); nothing
sleeps, and no production code changed to make this testable.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

import job_queue
import job_recovery
from database import async_session
from job_queue import JobQueue
from models import JobModel

T0 = datetime(2026, 9, 26, 12, 0, 0, 0)
T1 = T0 + timedelta(seconds=30, microseconds=1)
T2 = T1 + timedelta(seconds=30)
LEASE = timedelta(seconds=JobQueue.LEASE_SECONDS)


class _Clock(datetime):
    """datetime with a utcnow() the test sets; every other use is datetime's."""
    at: datetime = T0

    @classmethod
    def utcnow(cls):
        return cls.at


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(job_queue, "datetime", _Clock)
    monkeypatch.setattr(job_recovery, "datetime", _Clock)

    def set_to(t: datetime) -> None:
        _Clock.at = t
    set_to(T0)
    return set_to


async def _claimed(queue: JobQueue, job_id: str) -> None:
    async with async_session() as session:
        session.add(JobModel(id=job_id, project_id="lease-proj", type="clipper_export",
                             status="queued", progress=0.0, created_at=T0 - timedelta(minutes=1),
                             updated_at=T0 - timedelta(minutes=1), metadata_json=json.dumps({})))
        await session.commit()
    async with async_session() as session:
        assert await queue._claim(session, job_id) is not None


async def _row(job_id: str) -> dict:
    async with async_session() as session:
        job = await session.get(JobModel, job_id)
        return {k: getattr(job, k) for k in ("status", "worker_id", "lease_expires_at",
                                             "last_heartbeat", "updated_at", "attempt_count")}


@pytest.mark.asyncio
async def test_a_heartbeat_renews_the_lease_to_exactly_its_own_time_plus_the_lease(clock):
    owner = JobQueue()
    await _claimed(owner, "hb-renew")
    claimed = await _row("hb-renew")
    assert claimed["lease_expires_at"] == T0 + LEASE and claimed["last_heartbeat"] == T0

    clock(T1)
    assert await owner._heartbeat_once("hb-renew") is True
    renewed = await _row("hb-renew")
    assert renewed["lease_expires_at"] == T1 + LEASE
    assert renewed["last_heartbeat"] == T1 and renewed["updated_at"] == T1
    assert renewed["lease_expires_at"] > claimed["lease_expires_at"]
    assert {k: renewed[k] for k in ("status", "worker_id", "attempt_count")} == \
        {k: claimed[k] for k in ("status", "worker_id", "attempt_count")}


@pytest.mark.asyncio
async def test_a_heartbeat_in_the_claims_own_tick_keeps_an_equal_expiry(clock):
    """The flake's case: claim and heartbeat in one clock tick. Equal is correct."""
    owner = JobQueue()
    await _claimed(owner, "hb-same-tick")
    assert await owner._heartbeat_once("hb-same-tick") is True
    row = await _row("hb-same-tick")
    assert row["lease_expires_at"] == T0 + LEASE and row["last_heartbeat"] == T0
    assert row["status"] == "running" and row["worker_id"] == owner.worker_id


@pytest.mark.asyncio
async def test_the_old_owner_is_refused_and_the_row_is_left_unchanged(clock):
    owner, other = JobQueue(), JobQueue()
    await _claimed(owner, "hb-fenced")
    clock(T1)
    assert await owner._heartbeat_once("hb-fenced") is True
    async with async_session() as session:
        job = await session.get(JobModel, "hb-fenced")
        job.worker_id = other.worker_id
        await session.commit()
    taken = await _row("hb-fenced")

    clock(T2)
    assert await owner._heartbeat_once("hb-fenced") is False
    assert await _row("hb-fenced") == taken                 # not renewed by the old owner
    assert taken["lease_expires_at"] == T1 + LEASE and taken["worker_id"] == other.worker_id
    assert "hb-fenced" in owner._lost_ownership_jobs
