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


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str, session: AsyncSession = Depends(get_session)):
    job = await session.get(JobModel, job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    if job.status not in (JobStatus.queued.value, JobStatus.running.value):
        return {"job_id": job_id, "status": job.status, "message": "Job not cancellable"}

    await job_queue.cancel_job(job_id)
    return {"job_id": job_id, "status": "cancelled"}
