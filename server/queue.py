"""
ClipForge — Job Queue Manager
SQLite-backed async job queue for media processing tasks.
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional, Callable, Dict, Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import async_session
from models import JobModel, JobStatus, JobType, ProjectModel

logger = logging.getLogger("clipforge.queue")


class JobQueue:
    """Manages background processing jobs with SQLite persistence."""

    def __init__(self):
        self._handlers: Dict[str, Callable] = {}
        self._running_jobs: Dict[str, asyncio.Task] = {}
        self._processor_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    def register_handler(self, job_type: str, handler: Callable):
        """Register a handler function for a job type."""
        self._handlers[job_type] = handler
        logger.info(f"Registered handler for job type: {job_type}")

    async def enqueue(
        self,
        project_id: str,
        job_type: str,
        clip_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> str:
        """Add a job to the queue. Returns job ID."""
        async with async_session() as session:
            job = JobModel(
                project_id=project_id,
                clip_id=clip_id,
                type=job_type,
                status=JobStatus.queued.value,
                metadata=metadata or {},
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)
            logger.info(f"Enqueued job {job.id} [{job_type}] for project {project_id}")
            return job.id

    async def update_progress(
        self,
        job_id: str,
        progress: float,
        message: str = "",
    ):
        """Update job progress (0.0 - 1.0)."""
        async with async_session() as session:
            await session.execute(
                update(JobModel)
                .where(JobModel.id == job_id)
                .values(
                    progress=progress,
                    progress_message=message,
                    updated_at=datetime.utcnow(),
                )
            )
            await session.commit()

    async def complete_job(self, job_id: str):
        """Mark a job as completed."""
        async with async_session() as session:
            await session.execute(
                update(JobModel)
                .where(JobModel.id == job_id)
                .values(
                    status=JobStatus.done.value,
                    progress=1.0,
                    progress_message="Complete",
                    updated_at=datetime.utcnow(),
                )
            )
            await session.commit()
        self._running_jobs.pop(job_id, None)
        logger.info(f"Job {job_id} completed")

    async def fail_job(self, job_id: str, error: str):
        """Mark a job as failed."""
        async with async_session() as session:
            await session.execute(
                update(JobModel)
                .where(JobModel.id == job_id)
                .values(
                    status=JobStatus.failed.value,
                    error=error,
                    updated_at=datetime.utcnow(),
                )
            )
            await session.commit()
        self._running_jobs.pop(job_id, None)
        logger.error(f"Job {job_id} failed: {error}")

    async def cancel_job(self, job_id: str):
        """Cancel a running or queued job."""
        if job_id in self._running_jobs:
            self._running_jobs[job_id].cancel()
            self._running_jobs.pop(job_id, None)

        async with async_session() as session:
            await session.execute(
                update(JobModel)
                .where(JobModel.id == job_id)
                .values(
                    status=JobStatus.cancelled.value,
                    updated_at=datetime.utcnow(),
                )
            )
            await session.commit()
        logger.info(f"Job {job_id} cancelled")

    async def _process_next(self):
        """Pick up the next queued job and execute it."""
        from config import settings

        if len(self._running_jobs) >= settings.max_concurrent_jobs:
            return

        async with async_session() as session:
            result = await session.execute(
                select(JobModel)
                .where(JobModel.status == JobStatus.queued.value)
                .order_by(JobModel.created_at)
                .limit(1)
            )
            job = result.scalar_one_or_none()

            if not job:
                return

            handler = self._handlers.get(job.type)
            if not handler:
                await self.fail_job(job.id, f"No handler registered for job type: {job.type}")
                return

            # Mark as running
            await session.execute(
                update(JobModel)
                .where(JobModel.id == job.id)
                .values(
                    status=JobStatus.running.value,
                    updated_at=datetime.utcnow(),
                )
            )
            await session.commit()

            # Capture job info before session closes
            job_id = job.id
            project_id = job.project_id
            clip_id = job.clip_id
            job_type = job.type
            job_metadata = job.metadata or {}

        # Run handler in a background task
        async def _run():
            try:
                await handler(
                    job_id=job_id,
                    project_id=project_id,
                    clip_id=clip_id,
                    metadata=job_metadata,
                    queue=self,
                )
                await self.complete_job(job_id)
            except asyncio.CancelledError:
                await self.cancel_job(job_id)
            except Exception as e:
                logger.exception(f"Job {job_id} failed with exception")
                await self.fail_job(job_id, str(e))

        task = asyncio.create_task(_run())
        self._running_jobs[job_id] = task
        logger.info(f"Started job {job_id} [{job_type}]")

    async def start(self):
        """Start the background job processor loop."""
        logger.info("Job queue processor started")
        self._stop_event.clear()

        while not self._stop_event.is_set():
            try:
                await self._process_next()
            except Exception as e:
                logger.exception("Error in job processor loop")
            await asyncio.sleep(1)

    async def stop(self):
        """Stop the job processor."""
        self._stop_event.set()
        for job_id, task in self._running_jobs.items():
            task.cancel()
        self._running_jobs.clear()
        logger.info("Job queue processor stopped")


# Singleton
job_queue = JobQueue()
