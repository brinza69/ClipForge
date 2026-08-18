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
from datetime import datetime
from pathlib import Path

import pytest

from database import async_session
from job_queue import JobQueue
from models import JobModel

_SERVER = str(Path(__file__).resolve().parent.parent)


async def _queued(job_id: str) -> None:
    async with async_session() as session:
        session.add(JobModel(
            id=job_id, project_id="claim-proj", type="clipper_export", status="queued",
            progress=0.0, created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(), metadata_json=json.dumps({}),
        ))
        await session.commit()


async def _status(job_id: str) -> str:
    async with async_session() as session:
        job = await session.get(JobModel, job_id)
        return job.status


@pytest.mark.asyncio
async def test_the_second_claim_of_one_job_fails():
    queue = JobQueue()
    await _queued("claim-twice")

    async with async_session() as a:
        assert await queue._claim(a, "claim-twice") is True
    async with async_session() as b:
        assert await queue._claim(b, "claim-twice") is False, (
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
        assert await queue._claim(session, "claim-cancelled") is False


@pytest.mark.asyncio
async def test_concurrent_claims_in_one_process_produce_one_winner():
    queue = JobQueue()
    await _queued("claim-gather")

    async def attempt() -> bool:
        async with async_session() as session:
            return await queue._claim(session, "claim-gather")

    results = await asyncio.gather(*(attempt() for _ in range(8)))
    assert sum(results) == 1, f"{sum(results)} workers thought they had the job"


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
