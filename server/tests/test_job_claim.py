"""One queued job, several workers, exactly one winner.

`_process_next` used to SELECT a queued job and then UPDATE it to running in
two statements. Two processes could read the same row and both decide to run
it, and this rig is not a place where that is theoretical: `start_all.ps1`
starts a SECOND backend on 8421 against the same clipforge.db. A job claimed
twice is the same ingest downloading twice, the same export writing the same
file, and progress oscillating between two writers.

The fix is one extra WHERE. The work is proving it, so the last test here
spawns real OS processes against a real file — an asyncio gather in one process
shares a connection pool and would pass against code that only looks atomic.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import textwrap
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from database import async_session
from job_queue import JobQueue
from models import JobModel

_SERVER = str(Path(__file__).resolve().parent.parent)


async def _queued(
    job_id: str,
    created_at: datetime | None = None,
    job_type: str = "clipper_export",
) -> None:
    created_at = created_at or datetime.utcnow()
    async with async_session() as session:
        session.add(JobModel(
            id=job_id, project_id="claim-proj", type=job_type, status="queued",
            progress=0.0, created_at=created_at,
            updated_at=created_at, metadata_json=json.dumps({}),
        ))
        await session.commit()


async def _status(job_id: str) -> str:
    async with async_session() as session:
        job = await session.get(JobModel, job_id)
        return job.status


async def _set_status(job_id: str, status: str, progress: float = 0.0) -> None:
    async with async_session() as session:
        job = await session.get(JobModel, job_id)
        job.status = status
        job.progress = progress
        await session.commit()


async def _claim(queue: JobQueue, job_id: str) -> None:
    async with async_session() as session:
        assert await queue._claim(session, job_id) is not None


async def _job(job_id: str) -> JobModel:
    async with async_session() as session:
        return await session.get(JobModel, job_id)


@pytest.mark.asyncio
async def test_the_second_claim_of_one_job_fails():
    queue = JobQueue()
    await _queued("claim-twice")

    async with async_session() as a:
        assert await queue._claim(a, "claim-twice") is not None
    async with async_session() as b:
        assert await queue._claim(b, "claim-twice") is None, (
            "a job already running was claimed a second time")
    assert await _status("claim-twice") == "running"


@pytest.mark.asyncio
async def test_claiming_a_job_that_is_not_queued_fails():
    """Cancelled and failed jobs are not free to pick up either."""
    queue = JobQueue()
    await _queued("claim-cancelled")
    async with async_session() as session:
        job = await session.get(JobModel, "claim-cancelled")
        job.status = "cancelled"
        await session.commit()

    async with async_session() as session:
        assert await queue._claim(session, "claim-cancelled") is None


@pytest.mark.asyncio
async def test_terminal_job_cannot_be_completed_by_a_late_worker():
    queue = JobQueue()
    await _queued("transition-complete")
    await _set_status("transition-complete", "cancelled")

    await queue.complete_job("transition-complete")

    async with async_session() as session:
        job = await session.get(JobModel, "transition-complete")
        assert job.status == "cancelled"
        assert job.progress == 0.0


@pytest.mark.asyncio
async def test_running_job_can_be_completed():
    queue = JobQueue()
    await _queued("transition-complete-valid")
    await _claim(queue, "transition-complete-valid")
    await _set_status("transition-complete-valid", "running", progress=0.4)

    await queue.complete_job("transition-complete-valid", owner_id=queue.worker_id)

    async with async_session() as session:
        job = await session.get(JobModel, "transition-complete-valid")
        assert job.status == "done"
        assert job.progress == 1.0
        assert job.progress_message == "Complete"


@pytest.mark.asyncio
async def test_terminal_job_cannot_be_failed_by_a_late_worker():
    queue = JobQueue()
    queue._cleanup_workspace = AsyncMock()
    await _queued("transition-fail")
    await _set_status("transition-fail", "done", progress=1.0)

    await queue.fail_job("transition-fail", "late worker error")

    assert await _status("transition-fail") == "done"
    queue._cleanup_workspace.assert_not_awaited()


@pytest.mark.asyncio
async def test_running_job_can_be_failed():
    queue = JobQueue()
    queue._cleanup_workspace = AsyncMock()
    await _queued("transition-fail-valid")
    await _claim(queue, "transition-fail-valid")

    await queue.fail_job(
        "transition-fail-valid", "expected failure", owner_id=queue.worker_id
    )

    async with async_session() as session:
        job = await session.get(JobModel, "transition-fail-valid")
        assert job.status == "failed"
        assert job.error == "expected failure"
    queue._cleanup_workspace.assert_awaited_once_with("transition-fail-valid")


@pytest.mark.asyncio
async def test_terminal_job_cannot_be_cancelled_by_a_late_request():
    queue = JobQueue()
    queue._cleanup_workspace = AsyncMock()
    await _queued("transition-cancel")
    await _set_status("transition-cancel", "done", progress=1.0)

    await queue.cancel_job("transition-cancel")

    assert await _status("transition-cancel") == "done"
    queue._cleanup_workspace.assert_not_awaited()


@pytest.mark.asyncio
async def test_running_job_can_be_cancelled():
    queue = JobQueue()
    queue._cleanup_workspace = AsyncMock()
    await _queued("transition-cancel-valid")
    await _claim(queue, "transition-cancel-valid")

    await queue.cancel_job("transition-cancel-valid", owner_id=queue.worker_id)

    assert await _status("transition-cancel-valid") == "cancelled"
    queue._cleanup_workspace.assert_awaited_once_with("transition-cancel-valid")


@pytest.mark.asyncio
async def test_progress_updates_are_ignored_after_job_is_terminal():
    queue = JobQueue()
    await _queued("transition-progress")
    await _set_status("transition-progress", "done", progress=1.0)

    await queue.update_progress("transition-progress", 0.25, "stale update")

    async with async_session() as session:
        job = await session.get(JobModel, "transition-progress")
        assert job.status == "done"
        assert job.progress == 1.0
        assert job.progress_message != "stale update"


@pytest.mark.asyncio
async def test_claim_records_owner_lease_and_attempt():
    queue = JobQueue()
    await _queued("lease-claim")

    await _claim(queue, "lease-claim")

    job = await _job("lease-claim")
    assert job.worker_id == queue.worker_id
    assert job.lease_expires_at > datetime.utcnow()
    assert job.last_heartbeat is not None
    assert job.attempt_count == 1
    assert job.cancellation_requested is False


@pytest.mark.asyncio
async def test_a_different_worker_cannot_finalize_owned_job():
    owner = JobQueue()
    other = JobQueue()
    await _queued("lease-fencing")
    await _claim(owner, "lease-fencing")

    await other.update_progress("lease-fencing", 0.75, "stale progress")
    await other.complete_job("lease-fencing", owner_id=other.worker_id)
    await other.fail_job("lease-fencing", "stale failure", owner_id=other.worker_id)

    job = await _job("lease-fencing")
    assert job.status == "running"
    assert job.worker_id == owner.worker_id
    assert job.progress == 0.0


@pytest.mark.asyncio
async def test_recovery_leaves_a_live_lease_alone():
    owner = JobQueue()
    recovery = JobQueue()
    await _queued("lease-live")
    await _claim(owner, "lease-live")

    await recovery.recover_stuck_jobs()

    job = await _job("lease-live")
    assert job.status == "running"
    assert job.worker_id == owner.worker_id


@pytest.mark.asyncio
async def test_recovery_requeues_only_an_expired_lease():
    owner = JobQueue()
    recovery = JobQueue()
    await _queued("lease-expired")
    await _claim(owner, "lease-expired")
    async with async_session() as session:
        job = await session.get(JobModel, "lease-expired")
        job.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
        await session.commit()

    await recovery.recover_stuck_jobs()

    job = await _job("lease-expired")
    assert job.status == "queued"
    assert job.worker_id is None
    assert job.lease_expires_at is None
    assert job.attempt_count == 1
    assert "requeued" in job.progress_message

    await _claim(recovery, "lease-expired")
    job = await _job("lease-expired")
    assert job.worker_id == recovery.worker_id
    assert job.attempt_count == 2


@pytest.mark.asyncio
async def test_recovery_does_not_execute_legacy_unowned_running_job():
    recovery = JobQueue()
    await _queued("lease-legacy")
    await _set_status("lease-legacy", "running")

    await recovery.recover_stuck_jobs()

    job = await _job("lease-legacy")
    assert job.status == "failed"
    assert "no lease owner" in job.error


@pytest.mark.asyncio
async def test_graceful_stop_requeues_owned_job():
    queue = JobQueue()
    started = asyncio.Event()

    async def handler(**_kwargs):
        started.set()
        await asyncio.Event().wait()

    queue.register_handler("clipper_export", handler)
    await _queued("lease-shutdown", created_at=datetime(2000, 1, 1))
    await queue._process_next()
    await asyncio.wait_for(started.wait(), timeout=2)

    await queue.stop()

    job = await _job("lease-shutdown")
    assert job.status == "queued"
    assert job.worker_id is None
    assert job.lease_expires_at is None
    assert "requeued" in job.progress_message


@pytest.mark.asyncio
async def test_concurrent_claims_in_one_process_produce_one_winner():
    queue = JobQueue()
    await _queued("claim-gather")

    async def attempt():
        async with async_session() as session:
            return await queue._claim(session, "claim-gather")

    results = await asyncio.gather(*(attempt() for _ in range(8)))
    won = sum(r is not None for r in results)
    assert won == 1, f"{won} workers thought they had the job"


_CHILD = textwrap.dedent("""
    import asyncio, os, sys
    os.environ["CLIPFORGE_DATA_DIR"] = sys.argv[1]
    sys.path.insert(0, sys.argv[2])
    from database import async_session
    from job_queue import JobQueue

    async def main():
        # All the children open the DB, then wait for the same wall-clock
        # instant, so the claims actually overlap instead of queueing up
        # behind each other's import time.
        deadline = float(sys.argv[4])
        async with async_session() as session:
            q = JobQueue()
            while asyncio.get_event_loop().time() < 0:
                pass
            import time
            while time.time() < deadline:
                time.sleep(0.002)
            print("WON" if await q._claim(session, sys.argv[3]) else "LOST")

    asyncio.run(main())
""")


@pytest.mark.asyncio
async def test_two_real_processes_cannot_both_claim_one_job(tmp_path):
    """The one that matters. Separate processes, separate connection pools,
    one SQLite file — which is the shape of this rig with the second backend
    on 8421."""
    import time

    await _queued("claim-processes")
    child = tmp_path / "claimer.py"
    child.write_text(_CHILD, encoding="utf-8")

    deadline = time.time() + 6.0
    procs = [
        subprocess.Popen(
            [sys.executable, str(child), os.environ["CLIPFORGE_DATA_DIR"],
             _SERVER, "claim-processes", str(deadline)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(4)
    ]
    outs = []
    for p in procs:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, f"claimer crashed:\n{err}"
        outs.append(out.strip())

    assert outs.count("WON") == 1, f"claims: {outs}"
    assert await _status("claim-processes") == "running"


_CRASH_CHILD = textwrap.dedent("""
    import asyncio, os, sys
    sys.path.insert(0, sys.argv[2])
    os.environ["CLIPFORGE_DATA_DIR"] = sys.argv[1]
    from database import async_session
    from job_queue import JobQueue

    async def main():
        async with async_session() as session:
            claimed = await JobQueue()._claim(session, sys.argv[3])
        print("CLAIMED" if claimed else "NOT_CLAIMED", flush=True)
        # A hard process exit deliberately skips graceful queue shutdown.
        os._exit(0)

    asyncio.run(main())
""")


@pytest.mark.asyncio
async def test_restart_recovers_a_job_owned_by_a_crashed_process(tmp_path):
    """A new worker can reclaim a lease left behind by a hard process exit."""
    job_id = "restart-recovery"
    await _queued(job_id)
    child = tmp_path / "crashed_worker.py"
    child.write_text(_CRASH_CHILD, encoding="utf-8")

    process = subprocess.Popen(
        [sys.executable, str(child), os.environ["CLIPFORGE_DATA_DIR"],
         _SERVER, job_id],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    out, err = process.communicate(timeout=120)
    assert process.returncode == 0, f"crashed worker failed:\n{err}"
    assert out.strip() == "CLAIMED"

    owned = await _job(job_id)
    assert owned.status == "running"
    assert owned.worker_id

    async with async_session() as session:
        job = await session.get(JobModel, job_id)
        job.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
        await session.commit()

    recovery = JobQueue()
    await recovery.recover_stuck_jobs()

    recovered = await _job(job_id)
    assert recovered.status == "queued"
    assert recovered.worker_id is None
    assert recovered.attempt_count == 1
    assert "requeued" in recovered.progress_message

    await _claim(recovery, job_id)
    claimed_again = await _job(job_id)
    assert claimed_again.worker_id == recovery.worker_id
    assert claimed_again.attempt_count == 2


@pytest.mark.asyncio
async def test_queue_processes_many_jobs_with_bounded_concurrency(monkeypatch):
    """Several queued jobs finish once each without exceeding the lane cap."""
    from config import settings

    monkeypatch.setattr(settings, "max_concurrent_jobs", 3)
    queue = JobQueue()
    active = 0
    max_active = 0
    handled: list[str] = []
    guard = asyncio.Lock()

    async def handler(*, job_id, queue, **_kwargs):
        nonlocal active, max_active
        async with guard:
            active += 1
            max_active = max(max_active, active)
            handled.append(job_id)
        await queue.update_progress(job_id, 0.5, "halfway")
        await asyncio.sleep(0.03)
        async with guard:
            active -= 1

    queue.register_handler("queue_stress", handler)
    job_ids = [f"queue-stress-{i}" for i in range(12)]
    for i, job_id in enumerate(job_ids):
        await _queued(
            job_id,
            created_at=datetime(1900, 1, 1) + timedelta(seconds=i),
            job_type="queue_stress",
        )

    deadline = time.monotonic() + 10.0
    try:
        while time.monotonic() < deadline:
            statuses = await asyncio.gather(*(_status(job_id) for job_id in job_ids))
            if all(status == "done" for status in statuses):
                break
            await queue._process_next()
            await asyncio.sleep(0.01)
    finally:
        await queue.stop()

    final_statuses = await asyncio.gather(*(_status(job_id) for job_id in job_ids))
    assert all(status == "done" for status in final_statuses)
    assert len(handled) == len(job_ids)
    assert len(set(handled)) == len(job_ids)
    assert max_active <= 3
