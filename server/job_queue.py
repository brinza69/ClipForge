"""
ClipForge — Job Queue Manager
SQLite-backed async job queue for media processing tasks.
"""

import asyncio
import json
import logging
import os
import socket
import uuid
from datetime import datetime, timedelta
from typing import Optional, Callable, Dict, Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from database import async_session
import job_recovery
from job_attempt import CLAIMED_ATTEMPT, ClaimedAttempt
from job_rows import add_job, new_job_row
from models import JobModel, JobStatus, JobType, ProjectModel

logger = logging.getLogger("clipforge.queue")


class JobCancelledError(Exception):
    pass

# Doodle jobs run in their OWN concurrency lane. They are light on this
# process (OpenAI/ComfyUI HTTP calls, Kokoro CPU TTS, one FFmpeg render) —
# unlike parallel_pipeline which monopolizes the GPU. Without the separate
# lane, the video factory keeps the single job slot busy ~forever and every
# doodle job starves in `queued` (the UI looks frozen at 0/N images).
#
# The TikTok Transformation wizard's light steps join this lane for the same
# reason: they are API calls / single-frame ffmpeg work, and without a lane the
# video factory would starve the whole wizard. tiktok_render is deliberately
# NOT here — it is a full 1080x1920 encode and belongs in the heavy lane.
#
# The AI Stream Clipper splits the same way. clipper_analyze/score/preview read
# a 480p proxy and do CPU-light work, so they join this lane and the review UI
# keeps responding while a render is going. clipper_ingest (yt-dlp + proxy
# build), clipper_transcribe (whisper on the GPU) and clipper_export (full
# 1080x1920 encode) stay heavy.
DOODLE_LANE_TYPES = frozenset(
    {
        "doodle_script", "doodle_tts", "doodle_render", "doodle_images",
        "tiktok_import", "tiktok_frames", "tiktok_script", "tiktok_voice",
        "tiktok_thumbnails", "tiktok_description",
        "clipper_analyze", "clipper_score", "clipper_preview",
    }
)
DOODLE_LANE_LIMIT = 2
# Jobs about ONE clip. Their failure or cancel never writes the project row.
_CLIP_SCOPED_TYPES = frozenset({"clipper_preview", "clipper_export"})


class JobQueue:
    """Manages background processing jobs with SQLite persistence."""

    LEASE_SECONDS = 120.0
    HEARTBEAT_SECONDS = 10.0
    MAX_RECOVER = 50

    def __init__(self):
        self._handlers: Dict[str, Callable] = {}
        self._running_jobs: Dict[str, asyncio.Task] = {}
        self._running_types: Dict[str, str] = {}   # job_id -> job type (lane bookkeeping)
        self._cancelled_jobs = set()
        self._lost_ownership_jobs = set()
        self.worker_id = (
            f"{socket.gethostname()[:80]}:{os.getpid()}:{uuid.uuid4().hex[:12]}"
        )
        self._processor_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._ready = False

    @property
    def is_ready(self) -> bool:
        """Whether startup recovery completed and the processor is serving."""
        return self._ready

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
        idempotency_key: Optional[str] = None,
    ) -> str:
        """Add a job to the queue. Returns job ID."""
        key = str(idempotency_key) if idempotency_key else None
        async with async_session() as session:
            if key:
                existing = await session.scalar(
                    select(JobModel.id)
                    .where(JobModel.idempotency_key == key)
                    .where(JobModel.status.in_([
                        JobStatus.queued.value, JobStatus.running.value
                    ]))
                    .limit(1)
                )
                if existing:
                    return existing
            job = new_job_row(project_id, job_type, clip_id, metadata, key)
            session.add(job)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                if not key:
                    raise
                existing = await session.scalar(
                    select(JobModel.id)
                    .where(JobModel.idempotency_key == key)
                    .where(JobModel.status.in_([
                        JobStatus.queued.value, JobStatus.running.value
                    ]))
                    .limit(1)
                )
                if not existing:
                    raise
                return existing
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
        now = datetime.utcnow()
        async with async_session() as session:
            await session.execute(
                update(JobModel)
                .where(JobModel.id == job_id)
                .where(JobModel.status == JobStatus.running.value)
                .where(JobModel.worker_id == self.worker_id)
                .where(JobModel.cancellation_requested.is_(False))
                .values(
                    progress=progress,
                    progress_message=message,
                    last_heartbeat=now,
                    lease_expires_at=now + timedelta(seconds=self.LEASE_SECONDS),
                    updated_at=now,
                )
            )
            await session.commit()

    async def complete_job(self, job_id: str, *, owner_id: Optional[str] = None,
                           attempt: Optional[int] = None):
        """Mark a job as completed. `attempt`: only while the row is still that attempt (R1c)."""
        async with async_session() as session:
            query = (
                update(JobModel)
                .where(JobModel.id == job_id)
                .where(JobModel.status == JobStatus.running.value)
                .values(
                    status=JobStatus.done.value,
                    progress=1.0,
                    progress_message="Complete",
                    updated_at=datetime.utcnow(),
                )
            )
            if owner_id is not None:
                query = query.where(JobModel.worker_id == owner_id)
            if attempt is not None:
                query = query.where(JobModel.attempt_count == attempt)
            result = await session.execute(query)
            await session.commit()
        self._running_jobs.pop(job_id, None)
        self._running_types.pop(job_id, None)
        if result.rowcount == 1:
            self._cancelled_jobs.discard(job_id)
            logger.info(f"Job {job_id} completed")
        else:
            logger.warning(
                f"Ignored completion for job {job_id}: it was no longer running"
            )

    async def fail_job(
        self,
        job_id: str,
        error: str,
        *,
        owner_id: Optional[str] = None,
        attempt: Optional[int] = None,
    ):
        """Mark a job as failed. `attempt`: only while the row is still that attempt (R1c)."""
        from models import ProjectStatus
        from services.clipper.clip_mutations import release_export_claim
        error_text = str(error)[:800]
        async with async_session() as session:
            query = update(JobModel).where(JobModel.id == job_id)
            if owner_id is not None:
                query = query.where(
                    JobModel.status == JobStatus.running.value,
                    JobModel.worker_id == owner_id,
                )
            else:
                query = query.where(
                    JobModel.status == JobStatus.queued.value,
                    JobModel.worker_id.is_(None),
                )
            if attempt is not None:
                query = query.where(JobModel.attempt_count == attempt)
            result = await session.execute(
                query.values(
                    status=JobStatus.failed.value,
                    error=error_text,
                    updated_at=datetime.utcnow(),
                )
            )

            job = await session.get(JobModel, job_id) if result.rowcount == 1 else None
            if job:
                # A clip-scoped job speaks for its clip, not its project: recovery
                # reads a failed project as terminal and would fail another clip's
                # live export (R4b review F1). Pipeline and other jobs as before.
                if job.project_id and job.type not in _CLIP_SCOPED_TYPES:
                    project = await session.get(ProjectModel, job.project_id)
                    if project:
                        project.status = ProjectStatus.failed.value
                        project.description = f"[{job.type} failed] {error_text[:200]}"

                # Only the clip's CURRENT export attempt frees it (R4b): a failed
                # preview, or an export another attempt superseded, leaves it.
                await release_export_claim(session, job)

            await session.commit()
        self._running_jobs.pop(job_id, None)
        self._running_types.pop(job_id, None)
        if result.rowcount != 1:
            logger.warning(
                f"Ignored failure for job {job_id}: it was already terminal or missing"
            )
            return

        self._cancelled_jobs.discard(job_id)
        logger.error(f"Job {job_id} failed: {error}")
        # Reclaim the failed job's scratch files (downloaded source, erased
        # video, per-variant dirs). Best-effort — never let cleanup mask the
        # original failure.
        await self._cleanup_workspace(job_id)

    async def cancel_job(self, job_id: str, *, owner_id: Optional[str] = None,
                         attempt: Optional[int] = None):
        """Cancel a running or queued job. `attempt`: only while the row is still that attempt (R1c)."""
        self._cancelled_jobs.add(job_id)
        task = self._running_jobs.get(job_id)
        if task and task is not asyncio.current_task():
            task.cancel()
            self._running_jobs.pop(job_id, None)
            self._running_types.pop(job_id, None)

        async with async_session() as session:
            from models import ProjectStatus
            from services.clipper.clip_mutations import release_export_claim

            query = (
                update(JobModel)
                .where(JobModel.id == job_id)
                .where(
                    JobModel.status.in_(
                        (JobStatus.queued.value, JobStatus.running.value)
                    )
                )
                .values(
                    status=JobStatus.cancelled.value,
                    cancellation_requested=True,
                    worker_id=None,
                    lease_expires_at=None,
                    last_heartbeat=None,
                    updated_at=datetime.utcnow(),
                )
            )
            if owner_id is not None:
                query = query.where(
                    (JobModel.status == JobStatus.running.value)
                    & (JobModel.worker_id == owner_id)
                )
            if attempt is not None:
                query = query.where(JobModel.attempt_count == attempt)
            result = await session.execute(query)

            # Keep parent project state consistent with the user's cancellation.
            job = await session.get(JobModel, job_id)
            transitioned = result.rowcount == 1
            if transitioned and job and job.project_id and job.type not in _CLIP_SCOPED_TYPES:
                project = await session.get(ProjectModel, job.project_id)
                if project and project.status not in (ProjectStatus.failed.value, ProjectStatus.cancelled.value):
                    project.status = ProjectStatus.cancelled.value

            # The clip moves only for its current export attempt (R4b), as in fail_job.
            if transitioned and job:
                await release_export_claim(session, job)

            await session.commit()
        if transitioned:
            logger.info(f"Job {job_id} cancelled")
        elif job and job.status == JobStatus.cancelled.value:
            logger.info(f"Job {job_id} was already cancelled")
        else:
            self._cancelled_jobs.discard(job_id)
            logger.warning(
                f"Ignored cancellation for job {job_id}: it was already terminal or missing"
            )
            return
        # Reclaim scratch files for the cancelled job.
        await self._cleanup_workspace(job_id)

    async def _cleanup_workspace(self, job_id: str):
        return await job_recovery._cleanup_workspace(self, job_id)

    def is_cancelled(self, job_id: str) -> bool:
        return job_id in self._cancelled_jobs

    async def _heartbeat_once(self, job_id: str) -> bool:
        return await job_recovery._heartbeat_once(self, job_id)

    async def _heartbeat_loop(self, job_id: str) -> None:
        return await job_recovery._heartbeat_loop(self, job_id)

    async def _requeue_owned_job(self, job_id: str) -> bool:
        return await job_recovery._requeue_owned_job(self, job_id)

    async def recover_stuck_jobs(self):
        return await job_recovery.recover_stuck_jobs(self)

    async def _claim(self, session, job_id: str) -> Optional[ClaimedAttempt]:
        return await job_recovery._claim(self, session, job_id)

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

            # Mark as running — and only if it is still queued. The SELECT
            # above and this UPDATE are two statements, so without the extra
            # WHERE two processes can both read the same `queued` row and both
            # decide to run it. That is not hypothetical here: start_all.ps1
            # runs a SECOND backend on 8421 against the same clipforge.db, and
            # a job claimed twice means the same ingest downloading twice, the
            # same export writing the same file, and progress that oscillates
            # between two writers.
            #
            # SQLite serialises writers, so the loser sees the committed
            # `running` and matches nothing. The returned attempt is the whole
            # signal, and the handler's identity from here on (R1c).
            claimed = await self._claim(session, job.id)
            if claimed is None:
                logger.debug("job %s was claimed by another worker", job.id)
                return

            # Capture job info before session closes
            job_id = job.id
            project_id = job.project_id
            clip_id = job.clip_id
            job_type = job.type
            job_metadata = json.loads(job.metadata_json) if job.metadata_json else {}

        # Run handler in a background task
        async def _run():
            # This task's own context: the handler, and what it awaits or starts, run as the
            # claimed attempt. Its end changes the row only while the row is still that attempt.
            CLAIMED_ATTEMPT.set(claimed)
            mine = {"owner_id": claimed.worker, "attempt": claimed.attempt}
            heartbeat_task = asyncio.create_task(self._heartbeat_loop(job_id))
            try:
                await handler(
                    job_id=job_id,
                    project_id=project_id,
                    clip_id=clip_id,
                    metadata=job_metadata,
                    queue=self,
                )
                if job_id not in self._lost_ownership_jobs:
                    await self.complete_job(job_id, **mine)
            except asyncio.CancelledError:
                if job_id in self._lost_ownership_jobs:
                    logger.warning("Stopped stale worker for job %s", job_id)
                elif self._stop_event.is_set():
                    await self._requeue_owned_job(job_id)
                else:
                    await self.cancel_job(job_id, **mine)
            except JobCancelledError:
                if job_id in self._lost_ownership_jobs:
                    logger.warning("Cancelled stale worker for job %s", job_id)
                elif self._stop_event.is_set():
                    await self._requeue_owned_job(job_id)
                else:
                    await self.cancel_job(job_id, **mine)
            except Exception as e:
                logger.exception(f"Job {job_id} failed with exception")
                if job_id not in self._lost_ownership_jobs:
                    await self.fail_job(job_id, str(e), **mine)
            finally:
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
                self._running_jobs.pop(job_id, None)
                self._running_types.pop(job_id, None)
                self._lost_ownership_jobs.discard(job_id)
                self._cancelled_jobs.discard(job_id)

        task = asyncio.create_task(_run())
        self._running_jobs[job_id] = task
        self._running_types[job_id] = job_type
        logger.info(f"Started job {job_id} [{job_type}]")

    async def start(self):
        """Start the background job processor loop."""
        logger.info("Job queue processor started")
        self._stop_event.clear()
        self._ready = False

        try:
            await self.recover_stuck_jobs()
        except Exception:
            logger.exception("Failed to recover stuck jobs on startup")
            return

        self._ready = True
        try:
            while not self._stop_event.is_set():
                try:
                    await self._process_next()
                except Exception:
                    logger.exception("Error in job processor loop")
                await asyncio.sleep(1)
        finally:
            self._ready = False

    async def stop(self):
        return await job_recovery.stop(self)


# Singleton
job_queue = JobQueue()
