"""
ClipForge Worker - Jobs Router (Phase 1 Stub)
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from models import JobModel, JobStatus
from schemas import JobResponse
from job_queue import job_queue

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("/", response_model=list[JobResponse])
async def list_jobs(
    project_id: str = None,
    status: str = None,
    session: AsyncSession = Depends(get_session),
):
    """List jobs, optionally filtered by project and/or status.

    `status` accepts a single value or a comma-separated list, e.g.
    `?status=running` or `?status=queued,running` (used by the sidebar
    running-jobs badge)."""
    query = select(JobModel).order_by(JobModel.created_at.desc())

    if project_id:
        query = query.where(JobModel.project_id == project_id)
    if status:
        wanted = [s.strip() for s in status.split(",") if s.strip()]
        if wanted:
            query = query.where(JobModel.status.in_(wanted))

    result = await session.execute(query.limit(100))
    return result.scalars().all()


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: str, session: AsyncSession = Depends(get_session)):
    job = await session.get(JobModel, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return JobResponse.model_validate(job)


@router.get("/{job_id}/stream")
async def stream_job(job_id: str):
    """Server-Sent Events stream of a job's progress. Emits a `data:` event
    whenever progress/status/message changes, and closes when the job hits a
    terminal state. Replaces 1.5s polling — one open connection instead of
    N×M repeated GETs across tabs. The frontend uses EventSource and falls
    back to polling on error."""
    import asyncio
    import json as _json

    from database import async_session
    from fastapi.responses import StreamingResponse

    async def events():
        last = None
        # Safety cap so a wedged job can't keep a connection open forever.
        deadline = asyncio.get_event_loop().time() + 3600
        while True:
            async with async_session() as session:
                job = await session.get(JobModel, job_id)
            if not job:
                yield 'event: error\ndata: {"detail":"not found"}\n\n'
                return
            cur = (job.progress, job.status, job.progress_message)
            if cur != last:
                last = cur
                payload = {
                    "id": job.id,
                    "status": job.status,
                    "progress": job.progress,
                    "progress_message": job.progress_message,
                    "error": job.error,
                }
                yield f"data: {_json.dumps(payload)}\n\n"
            if job.status in ("done", "failed", "cancelled"):
                return
            if asyncio.get_event_loop().time() > deadline:
                yield 'event: error\ndata: {"detail":"stream timeout"}\n\n'
                return
            await asyncio.sleep(1.0)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str, session: AsyncSession = Depends(get_session)):
    job = await session.get(JobModel, job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    if job.status not in (JobStatus.queued.value, JobStatus.running.value):
        return {"job_id": job_id, "status": job.status, "message": "Job not cancellable"}

    await job_queue.cancel_job(job_id)
    return {"job_id": job_id, "status": "cancelled"}
