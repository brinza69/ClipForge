"""
ClipForge — Jobs Router
API endpoints for job queue monitoring and control.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from models import JobModel, JobResponse
from queue import job_queue

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("/", response_model=list[JobResponse])
async def list_jobs(
    project_id: str = None,
    status: str = None,
    session: AsyncSession = Depends(get_session),
):
    """List jobs, optionally filtered by project or status."""
    query = select(JobModel).order_by(JobModel.created_at.desc())

    if project_id:
        query = query.where(JobModel.project_id == project_id)
    if status:
        query = query.where(JobModel.status == status)

    result = await session.execute(query.limit(100))
    jobs = result.scalars().all()

    return [JobResponse.model_validate(j) for j in jobs]


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: str, session: AsyncSession = Depends(get_session)):
    """Get a single job by ID."""
    job = await session.get(JobModel, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return JobResponse.model_validate(job)


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str):
    """Cancel a running or queued job."""
    await job_queue.cancel_job(job_id)
    return {"cancelled": job_id}
