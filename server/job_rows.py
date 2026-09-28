"""
ClipForge — the queued job row.

`new_job_row` and `add_job` moved here verbatim from job_queue.py, which was
over CLAUDE.md's 500-line limit (R5, review-R4b-B3r/REVIEW.md). job_queue.py
re-exports both, so `from job_queue import add_job` keeps working.
"""

import json
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from models import JobModel, JobStatus


def new_job_row(project_id: str, job_type: str, clip_id: Optional[str] = None,
                metadata: Optional[dict] = None, idempotency_key: Optional[str] = None,
                job_id: Optional[str] = None) -> JobModel:
    """The ONE place a queued job row is built. `enqueue` commits it in its own
    session; `add_job` puts it in the caller's transaction, which is how a
    Clipper export's claim and its job row commit together (R4b)."""
    job = JobModel(
        project_id=project_id,
        clip_id=clip_id,
        type=job_type,
        status=JobStatus.queued.value,
        attempt_count=0,
        cancellation_requested=False,
        idempotency_key=str(idempotency_key) if idempotency_key else None,
        metadata_json=json.dumps(metadata) if metadata else None,
    )
    if job_id is not None:
        job.id = job_id
    return job


async def add_job(session: AsyncSession, **kwargs) -> JobModel:
    """`new_job_row` added and flushed in the caller's session. No commit, no
    refresh: the caller's transaction owns it."""
    job = new_job_row(**kwargs)
    session.add(job)
    await session.flush()
    return job
