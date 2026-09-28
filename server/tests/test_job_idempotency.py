"""One active pipeline per deterministic request fingerprint."""

import asyncio

import pytest
from sqlalchemy import select

from database import async_session
from job_queue import JobQueue
from models import JobModel, JobStatus


pytestmark = pytest.mark.asyncio


async def test_same_idempotency_key_reuses_an_active_job():
    queue = JobQueue()

    first = await queue.enqueue(
        project_id="idem-project", job_type="remix_pipeline",
        metadata={"url": "https://example.test/a"}, idempotency_key="same-request",
    )
    second = await queue.enqueue(
        project_id="idem-project-2", job_type="remix_pipeline",
        metadata={"url": "https://example.test/a"}, idempotency_key="same-request",
    )

    assert second == first
    async with async_session() as session:
        rows = (
            await session.execute(
                select(JobModel).where(JobModel.idempotency_key == "same-request")
            )
        ).scalars().all()
    assert len(rows) == 1


async def test_concurrent_enqueue_has_one_winner():
    queue = JobQueue()

    ids = await asyncio.gather(*(
        queue.enqueue(
            project_id=f"idem-{i}", job_type="parallel_pipeline",
            metadata={"url": "https://example.test/b"},
            idempotency_key="concurrent-request",
        )
        for i in range(8)
    ))

    assert len(set(ids)) == 1


async def test_key_can_be_reused_after_the_previous_job_is_terminal():
    queue = JobQueue()
    first = await queue.enqueue(
        project_id="idem-terminal", job_type="remix_pipeline",
        metadata={}, idempotency_key="reusable-request",
    )
    async with async_session() as session:
        job = await session.get(JobModel, first)
        job.status = JobStatus.done.value
        await session.commit()

    second = await queue.enqueue(
        project_id="idem-terminal-2", job_type="remix_pipeline",
        metadata={}, idempotency_key="reusable-request",
    )

    assert second != first
