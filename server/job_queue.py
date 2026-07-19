"""
ClipForge — Job Queue Manager
SQLite-backed async job queue for media processing tasks.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, Callable, Dict, Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import async_session
from models import JobModel, JobStatus, JobType, ProjectModel

logger = logging.getLogger("clipforge.queue")


class JobCancelledError(Exception):
    pass

# Doodle jobs run in their OWN concurrency lane. They are light on this
# process (OpenAI/ComfyUI HTTP calls, Kokoro CPU TTS, one FFmpeg render) —
# unlike parallel_pipeline which monopolizes the GPU. Without the separate
# lane, the video factory keeps the single job slot busy ~forever and every
# doodle job starves in `queued` (the UI looks frozen at 0/N images).
DOODLE_LANE_TYPES = frozenset(
    {"doodle_script", "doodle_tts", "doodle_render", "doodle_images"}
)
DOODLE_LANE_LIMIT = 2


class JobQueue:
    """Manages background processing jobs with SQLite persistence."""

    def __init__(self):
        self._handlers: Dict[str, Callable] = {}
        self._running_jobs: Dict[str, asyncio.Task] = {}
        self._running_types: Dict[str, str] = {}   # job_id -> job type (lane bookkeeping)
        self._cancelled_jobs = set()
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
                metadata_json=json.dumps(metadata) if metadata else None,
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
        self._running_types.pop(job_id, None)
        logger.info(f"Job {job_id} completed")

    async def fail_job(self, job_id: str, error: str):
        """Mark a job as failed."""
        from models import ProjectStatus, ClipModel, ClipStatus
        async with async_session() as session:
            job = await session.get(JobModel, job_id)
            if job:
                job.status = JobStatus.failed.value
                job.error = str(error)[:800]
                job.updated_at = datetime.utcnow()
                
                if job.project_id:
                    project = await session.get(ProjectModel, job.project_id)
                    if project:
                        project.status = ProjectStatus.failed.value
                        project.description = f"[{job.type} failed] {str(error)[:200]}"
                
                if job.clip_id:
                    clip = await session.get(ClipModel, job.clip_id)
                    if clip:
                        clip.status = ClipStatus.failed.value

            await session.commit()
        self._running_jobs.pop(job_id, None)
        self._running_types.pop(job_id, None)
        logger.error(f"Job {job_id} failed: {error}")
        # Reclaim the failed job's scratch files (downloaded source, erased
        # video, per-variant dirs). Best-effort — never let cleanup mask the
        # original failure.
        await self._cleanup_workspace(job_id)

    async def cancel_job(self, job_id: str):
        """Cancel a running or queued job."""
        self._cancelled_jobs.add(job_id)
        if job_id in self._running_jobs:
            self._running_jobs[job_id].cancel()
            self._running_jobs.pop(job_id, None)
            self._running_types.pop(job_id, None)

        async with async_session() as session:
            from models import ProjectStatus, ClipModel, ClipStatus

            await session.execute(
                update(JobModel)
                .where(JobModel.id == job_id)
                .values(
                    status=JobStatus.cancelled.value,
                    updated_at=datetime.utcnow(),
                )
            )

            # Keep parent project state consistent with the user's cancellation.
            job = await session.get(JobModel, job_id)
            if job and job.project_id:
                project = await session.get(ProjectModel, job.project_id)
                if project and project.status not in (ProjectStatus.failed.value, ProjectStatus.cancelled.value):
                    project.status = ProjectStatus.cancelled.value
                    await session.commit()
                    await session.refresh(project)

            # Best-effort clip state update (mainly for export jobs).
            if job and job.clip_id:
                clip = await session.get(ClipModel, job.clip_id)
                if clip and clip.status not in (ClipStatus.exported.value, ClipStatus.failed.value, ClipStatus.rejected.value):
                    clip.status = ClipStatus.failed.value

            await session.commit()
        logger.info(f"Job {job_id} cancelled")
        # Reclaim scratch files for the cancelled job.
        await self._cleanup_workspace(job_id)

    async def _cleanup_workspace(self, job_id: str):
        """Best-effort disk cleanup for a cancelled/failed job's project dir.
        Runs the blocking rmtree in a thread so we don't stall the loop."""
        try:
            async with async_session() as session:
                job = await session.get(JobModel, job_id)
            project_id = job.project_id if job else None
            if not project_id:
                return
            from services.cleanup import cleanup_job_workspace
            loop = asyncio.get_event_loop()
            stats = await loop.run_in_executor(
                None, lambda: cleanup_job_workspace(project_id)
            )
            if stats.get("freed_bytes"):
                logger.info(
                    f"Job {job_id}: freed {stats['freed_bytes'] // (1024*1024)} MB "
                    f"of scratch files"
                )
        except Exception:
            logger.exception(f"workspace cleanup for {job_id} failed")

    def is_cancelled(self, job_id: str) -> bool:
        return job_id in self._cancelled_jobs

    async def recover_stuck_jobs(self):
        """
        On startup, recover EVERY job left in `running` state. A job in
        `running` when the process starts can only mean the previous process
        died mid-job (crash, hard kill, or — common in dev — a restart). The
        old 30-minute threshold left fresh jobs showing "running" for half an
        hour after every restart; with `--reload` off and manual restarts
        that's a constant annoyance. We requeue them all instead.
        """
        from models import ProjectStatus

        now = datetime.utcnow()

        async with async_session() as session:
            result = await session.execute(
                select(JobModel).where(JobModel.status == JobStatus.running.value)
            )
            stuck_jobs = result.scalars().all()
            if not stuck_jobs:
                return

            # Sanity cap: if the table somehow has a flood of orphans, don't
            # requeue them all (that could thrash on a corrupt DB). Mark the
            # overflow failed and log loudly.
            MAX_RECOVER = 50
            if len(stuck_jobs) > MAX_RECOVER:
                logger.warning(
                    f"{len(stuck_jobs)} jobs stuck in running — only requeueing the "
                    f"first {MAX_RECOVER}, failing the rest."
                )

            requeued = 0
            failed = 0
            for i, job in enumerate(stuck_jobs):
                project = await session.get(ProjectModel, job.project_id)
                terminal = project and project.status in (
                    ProjectStatus.cancelled.value, ProjectStatus.failed.value,
                )
                if terminal or i >= MAX_RECOVER:
                    job.status = JobStatus.failed.value
                    job.error = (
                        "Recovered from stuck running state; "
                        + ("project is terminal." if terminal else "recovery cap exceeded.")
                    )
                    job.progress_message = "Recovered: not requeued."
                    failed += 1
                    continue

                job.status = JobStatus.queued.value
                job.progress = min(job.progress or 0.0, 0.05)
                job.progress_message = (
                    f"Recovered from backend restart at "
                    f"{now.strftime('%H:%M:%S')} — requeued."
                )
                job.error = None
                job.updated_at = datetime.utcnow()
                requeued += 1

            await session.commit()
            logger.warning(
                f"Stuck-job recovery: requeued {requeued}, failed {failed} "
                f"(of {len(stuck_jobs)} running on startup)."
            )

    async def _process_next(self):
        """Pick up the next queued job and execute it. Two lanes: heavy media
        jobs respect max_concurrent_jobs; doodle jobs have their own small
        lane so the video factory can never starve them (see DOODLE_LANE_TYPES)."""
        from config import settings

        running_doodle = sum(
            1 for t in self._running_types.values() if t in DOODLE_LANE_TYPES
        )
        running_heavy = len(self._running_jobs) - running_doodle

        want_heavy = running_heavy < settings.max_concurrent_jobs
        want_doodle = running_doodle < DOODLE_LANE_LIMIT
        if not want_heavy and not want_doodle:
            return

        async with async_session() as session:
            job = None
            if want_heavy:
                result = await session.execute(
                    select(JobModel)
                    .where(JobModel.status == JobStatus.queued.value)
                    .where(JobModel.type.notin_(DOODLE_LANE_TYPES))
                    .order_by(JobModel.created_at)
                    .limit(1)
                )
                job = result.scalar_one_or_none()
            if job is None and want_doodle:
                result = await session.execute(
                    select(JobModel)
                    .where(JobModel.status == JobStatus.queued.value)
                    .where(JobModel.type.in_(DOODLE_LANE_TYPES))
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
            job_metadata = json.loads(job.metadata_json) if job.metadata_json else {}

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
            except (asyncio.CancelledError, JobCancelledError):
                await self.cancel_job(job_id)
            except Exception as e:
                logger.exception(f"Job {job_id} failed with exception")
                await self.fail_job(job_id, str(e))

        task = asyncio.create_task(_run())
        self._running_jobs[job_id] = task
        self._running_types[job_id] = job_type
        logger.info(f"Started job {job_id} [{job_type}]")

    async def start(self):
        """Start the background job processor loop."""
        logger.info("Job queue processor started")
        self._stop_event.clear()

        try:
            await self.recover_stuck_jobs()
        except Exception:
            logger.exception("Failed to recover stuck jobs on startup")

        while not self._stop_event.is_set():
            try:
                await self._process_next()
            except Exception as e:
                logger.exception("Error in job processor loop")
            await asyncio.sleep(1)

    async def stop(self):
        """Stop the job processor gracefully.

        Marks in-flight jobs as interrupted (so the UI shows clearly what
        happened and the next startup's recovery requeues them cleanly),
        then cancels their tasks with a short grace period to let them flush
        DB writes before the event loop tears down.
        """
        self._stop_event.set()

        running = list(self._running_jobs.items())
        if running:
            # 1) Annotate in-flight jobs before cancelling.
            try:
                async with async_session() as session:
                    for job_id, _task in running:
                        await session.execute(
                            update(JobModel)
                            .where(JobModel.id == job_id)
                            .values(
                                progress_message="Interrupted by backend shutdown.",
                                updated_at=datetime.utcnow(),
                            )
                        )
                    await session.commit()
            except Exception:
                logger.exception("could not annotate jobs on shutdown")

            # 2) Cancel and give them up to 5s to wind down.
            for _job_id, task in running:
                task.cancel()
            try:
                await asyncio.wait_for(
                    asyncio.gather(*(t for _, t in running), return_exceptions=True),
                    timeout=5,
                )
            except (asyncio.TimeoutError, Exception):
                pass

        self._running_jobs.clear()
        self._running_types.clear()
        logger.info("Job queue processor stopped")


# Singleton
job_queue = JobQueue()
