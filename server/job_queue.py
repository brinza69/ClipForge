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

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

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
            job = JobModel(
                project_id=project_id,
                clip_id=clip_id,
                type=job_type,
                status=JobStatus.queued.value,
                attempt_count=0,
                cancellation_requested=False,
                idempotency_key=key,
                metadata_json=json.dumps(metadata) if metadata else None,
            )
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

    async def complete_job(self, job_id: str, *, owner_id: Optional[str] = None):
        """Mark a job as completed."""
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
    ):
        """Mark a job as failed."""
        from models import ProjectStatus, ClipModel, ClipStatus
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
            result = await session.execute(
                query.values(
                    status=JobStatus.failed.value,
                    error=error_text,
                    updated_at=datetime.utcnow(),
                )
            )

            job = await session.get(JobModel, job_id) if result.rowcount == 1 else None
            if job:
                if job.project_id:
                    project = await session.get(ProjectModel, job.project_id)
                    if project:
                        project.status = ProjectStatus.failed.value
                        project.description = f"[{job.type} failed] {error_text[:200]}"
                
                if job.clip_id:
                    clip = await session.get(ClipModel, job.clip_id)
                    if clip:
                        clip.status = ClipStatus.failed.value

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

    async def cancel_job(self, job_id: str, *, owner_id: Optional[str] = None):
        """Cancel a running or queued job."""
        self._cancelled_jobs.add(job_id)
        task = self._running_jobs.get(job_id)
        if task and task is not asyncio.current_task():
            task.cancel()
            self._running_jobs.pop(job_id, None)
            self._running_types.pop(job_id, None)

        async with async_session() as session:
            from models import ProjectStatus, ClipModel, ClipStatus

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
            result = await session.execute(query)

            # Keep parent project state consistent with the user's cancellation.
            job = await session.get(JobModel, job_id)
            transitioned = result.rowcount == 1
            if transitioned and job and job.project_id:
                project = await session.get(ProjectModel, job.project_id)
                if project and project.status not in (ProjectStatus.failed.value, ProjectStatus.cancelled.value):
                    project.status = ProjectStatus.cancelled.value

            # Best-effort clip state update (mainly for export jobs).
            if transitioned and job and job.clip_id:
                clip = await session.get(ClipModel, job.clip_id)
                if clip and clip.status not in (ClipStatus.exported.value, ClipStatus.failed.value, ClipStatus.rejected.value):
                    clip.status = ClipStatus.failed.value

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
        """Best-effort disk cleanup for a cancelled/failed job's project dir.
        Runs the blocking rmtree in a thread so we don't stall the loop."""
        try:
            async with async_session() as session:
                job = await session.get(JobModel, job_id)
            project_id = job.project_id if job else None
            if not project_id:
                return
            preserve_paths: list[str] = []
            try:
                metadata = json.loads(job.metadata_json or "{}") if job else {}
                if isinstance(metadata, dict):
                    final_path = metadata.get("final_path")
                    if final_path:
                        preserve_paths.append(str(final_path))
                    for result in metadata.get("results") or []:
                        if not isinstance(result, dict):
                            continue
                        if result.get("final_path"):
                            preserve_paths.append(str(result["final_path"]))
                        for part in result.get("parts") or []:
                            if isinstance(part, dict) and part.get("path"):
                                preserve_paths.append(str(part["path"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                logger.warning("job %s has unreadable output metadata", job_id)
            from services.cleanup import cleanup_job_workspace
            loop = asyncio.get_event_loop()
            stats = await loop.run_in_executor(
                None,
                lambda: cleanup_job_workspace(
                    project_id, preserve_paths=preserve_paths
                ),
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

    async def _heartbeat_once(self, job_id: str) -> bool:
        """Renew a lease, returning False when this worker lost ownership."""
        now = datetime.utcnow()
        try:
            async with async_session() as session:
                result = await session.execute(
                    update(JobModel)
                    .where(JobModel.id == job_id)
                    .where(JobModel.status == JobStatus.running.value)
                    .where(JobModel.worker_id == self.worker_id)
                    .where(JobModel.cancellation_requested.is_(False))
                    .values(
                        last_heartbeat=now,
                        lease_expires_at=now + timedelta(seconds=self.LEASE_SECONDS),
                        updated_at=now,
                    )
                )
                await session.commit()
        except Exception:
            logger.exception("Heartbeat failed for job %s", job_id)
            return True

        if result.rowcount == 1:
            return True

        self._lost_ownership_jobs.add(job_id)
        task = self._running_jobs.get(job_id)
        if task and task is not asyncio.current_task():
            task.cancel()
        logger.warning("Worker %s lost ownership of job %s", self.worker_id, job_id)
        return False

    async def _heartbeat_loop(self, job_id: str) -> None:
        try:
            while True:
                await asyncio.sleep(self.HEARTBEAT_SECONDS)
                if not await self._heartbeat_once(job_id):
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Heartbeat loop stopped for job %s", job_id)

    async def _requeue_owned_job(self, job_id: str) -> bool:
        """Release a job during graceful shutdown without cancelling it."""
        now = datetime.utcnow()
        async with async_session() as session:
            result = await session.execute(
                update(JobModel)
                .where(JobModel.id == job_id)
                .where(JobModel.status == JobStatus.running.value)
                .where(JobModel.worker_id == self.worker_id)
                .values(
                    status=JobStatus.queued.value,
                    worker_id=None,
                    lease_expires_at=None,
                    last_heartbeat=None,
                    cancellation_requested=False,
                    progress=func.min(JobModel.progress, 0.05),
                    progress_message="Interrupted by backend shutdown; requeued.",
                    updated_at=now,
                )
            )
            await session.commit()
        if result.rowcount == 1:
            logger.info("Requeued job %s after graceful shutdown", job_id)
            return True
        return False

    async def recover_stuck_jobs(self):
        """
        On startup, recover only jobs whose lease has expired. A job with a
        live lease belongs to another backend process and must remain running.
        Rows from before lease support have no safe owner, so they are failed
        with an explicit retry message instead of being executed twice.
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

            expired = [
                job for job in stuck_jobs
                if not job.worker_id or not job.lease_expires_at
                or job.lease_expires_at <= now
            ]
            recoverable = [
                job for job in expired
                if job.worker_id and job.lease_expires_at
            ]
            if len(recoverable) > self.MAX_RECOVER:
                logger.warning(
                    f"{len(recoverable)} expired jobs — only recovering the first "
                    f"{self.MAX_RECOVER}, failing the rest."
                )

            requeued = 0
            failed = 0
            skipped_live = len(stuck_jobs) - len(expired)
            for job in stuck_jobs:
                if job not in expired:
                    continue

                if not job.worker_id or not job.lease_expires_at:
                    result = await session.execute(
                        update(JobModel)
                        .where(JobModel.id == job.id)
                        .where(JobModel.status == JobStatus.running.value)
                        .where(
                            (JobModel.worker_id.is_(None))
                            | (JobModel.lease_expires_at.is_(None))
                        )
                        .values(
                            status=JobStatus.failed.value,
                            error=(
                                "Job has no lease owner and was not safely recoverable; "
                                "retry it manually."
                            ),
                            progress_message="Recovery stopped: missing lease owner.",
                            worker_id=None,
                            lease_expires_at=None,
                            last_heartbeat=None,
                            updated_at=now,
                        )
                    )
                    failed += result.rowcount == 1
                    continue

                project = await session.get(ProjectModel, job.project_id)
                terminal = project and project.status in (
                    ProjectStatus.cancelled.value, ProjectStatus.failed.value,
                )
                should_fail = terminal or requeued >= self.MAX_RECOVER
                recovery_error = (
                    "Recovered from expired lease; project is terminal."
                    if terminal
                    else "Recovery cap exceeded for expired lease."
                )
                if should_fail:
                    result = await session.execute(
                        update(JobModel)
                        .where(JobModel.id == job.id)
                        .where(JobModel.status == JobStatus.running.value)
                        .where(JobModel.worker_id == job.worker_id)
                        .where(JobModel.lease_expires_at <= now)
                        .values(
                            status=JobStatus.failed.value,
                            error=recovery_error,
                            progress_message="Recovered: not requeued.",
                            worker_id=None,
                            lease_expires_at=None,
                            last_heartbeat=None,
                            updated_at=now,
                        )
                    )
                    failed += result.rowcount == 1
                    continue

                result = await session.execute(
                    update(JobModel)
                    .where(JobModel.id == job.id)
                    .where(JobModel.status == JobStatus.running.value)
                    .where(JobModel.worker_id == job.worker_id)
                    .where(JobModel.lease_expires_at <= now)
                    .values(
                        status=JobStatus.queued.value,
                        progress=func.min(JobModel.progress, 0.05),
                        progress_message=(
                            f"Recovered from backend restart at "
                            f"{now.strftime('%H:%M:%S')} — requeued."
                        ),
                        error=None,
                        worker_id=None,
                        lease_expires_at=None,
                        last_heartbeat=None,
                        cancellation_requested=False,
                        updated_at=now,
                    )
                )
                requeued += result.rowcount == 1

            await session.commit()
            logger.warning(
                f"Stuck-job recovery: requeued {requeued}, failed {failed}, "
                f"left {skipped_live} live leases untouched "
                f"(of {len(stuck_jobs)} running on startup)."
            )

    async def _claim(self, session, job_id: str) -> bool:
        """Take a queued job. True only for the caller that actually got it.

        The conditional UPDATE is the lock. `WHERE status = 'queued'` means the
        second writer matches no rows and gets rowcount 0, whatever order the
        two processes arrived in.
        """
        now = datetime.utcnow()
        result = await session.execute(
            update(JobModel)
            .where(JobModel.id == job_id)
            .where(JobModel.status == JobStatus.queued.value)
            .where(JobModel.cancellation_requested.is_(False))
            .values(
                status=JobStatus.running.value,
                worker_id=self.worker_id,
                lease_expires_at=now + timedelta(seconds=self.LEASE_SECONDS),
                last_heartbeat=now,
                attempt_count=func.coalesce(JobModel.attempt_count, 0) + 1,
                cancellation_requested=False,
                updated_at=now,
            )
        )
        await session.commit()
        return result.rowcount == 1

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
            # `running` and matches nothing. rowcount is the whole signal.
            if not await self._claim(session, job.id):
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
                    await self.complete_job(job_id, owner_id=self.worker_id)
            except asyncio.CancelledError:
                if job_id in self._lost_ownership_jobs:
                    logger.warning("Stopped stale worker for job %s", job_id)
                elif self._stop_event.is_set():
                    await self._requeue_owned_job(job_id)
                else:
                    await self.cancel_job(job_id, owner_id=self.worker_id)
            except JobCancelledError:
                if job_id in self._lost_ownership_jobs:
                    logger.warning("Cancelled stale worker for job %s", job_id)
                elif self._stop_event.is_set():
                    await self._requeue_owned_job(job_id)
                else:
                    await self.cancel_job(job_id, owner_id=self.worker_id)
            except Exception as e:
                logger.exception(f"Job {job_id} failed with exception")
                if job_id not in self._lost_ownership_jobs:
                    await self.fail_job(job_id, str(e), owner_id=self.worker_id)
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
        """Stop the job processor gracefully.

        Marks in-flight jobs as interrupted, then cancels their tasks with a
        short grace period. Tasks that catch the cancellation release their
        lease back to `queued`; a hard process kill leaves the lease to the
        next startup's expiry-based recovery.
        """
        self._ready = False
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
                            .where(JobModel.status == JobStatus.running.value)
                            .where(JobModel.worker_id == self.worker_id)
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
