"""
ClipForge — job leases: startup recovery, heartbeats, shutdown and scratch cleanup.

Moved verbatim from `JobQueue` in job_queue.py, which was over CLAUDE.md's
500-line limit (R5, review-R4b-B3r/REVIEW.md). Each function takes the queue
where the method took `self`; `JobQueue` keeps a thin method of the same name
that delegates here, so callers and instance-level patches
(`queue._cleanup_workspace = AsyncMock()`) are unchanged.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select, update

from database import async_session
from job_attempt import CLAIMED_ATTEMPT, ClaimedAttempt
from models import JobModel, JobStatus, ProjectModel

logger = logging.getLogger("clipforge.queue")


async def _cleanup_workspace(queue, job_id: str):
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


async def _claim(queue, session, job_id: str) -> ClaimedAttempt | None:
    """Take a queued job. Its attempt only for the caller that actually got it, else None.

    The conditional UPDATE is the lock. `WHERE status = 'queued'` means the
    second writer matches no rows, whatever order the two processes arrived in.
    RETURNING gives the attempt number THIS update wrote: the handler's identity
    (BURST R1c), where reading the row again later could return a newer attempt's.
    Moved here from `JobQueue` with R1c, which took job_queue.py past 500 lines.
    """
    now = datetime.utcnow()
    worker = queue.worker_id
    result = await session.execute(
        update(JobModel)
        .where(JobModel.id == job_id)
        .where(JobModel.status == JobStatus.queued.value)
        .where(JobModel.cancellation_requested.is_(False))
        .values(
            status=JobStatus.running.value,
            worker_id=worker,
            lease_expires_at=now + timedelta(seconds=queue.LEASE_SECONDS),
            last_heartbeat=now,
            attempt_count=func.coalesce(JobModel.attempt_count, 0) + 1,
            cancellation_requested=False,
            updated_at=now,
        )
        .returning(JobModel.attempt_count)
    )
    attempt = result.scalar_one_or_none()
    await session.commit()
    return None if attempt is None else ClaimedAttempt(job_id, int(attempt), worker)


def _mine(job_id: str) -> int | None:
    """The attempt this task runs `job_id` as (the claim `_run` set, inherited by the tasks it
    starts), or None when it runs no claim of that job."""
    claimed = CLAIMED_ATTEMPT.get()
    return claimed.attempt if claimed is not None and claimed.job_id == job_id else None


def _as_mine(query, job_id: str):
    """Only the row of THIS task's attempt: an old attempt's heartbeat, progress or requeue
    renews, moves or releases nothing of the attempt that replaced it (AQ1)."""
    attempt = _mine(job_id)
    return query if attempt is None else query.where(JobModel.attempt_count == attempt)


async def update_progress(queue, job_id: str, progress: float, message: str = "") -> None:
    """Update job progress (0.0 - 1.0), which renews the lease too. Moved here from `JobQueue`
    with AQ1, which took job_queue.py past 500 lines."""
    now = datetime.utcnow()
    async with async_session() as session:
        await session.execute(_as_mine(
            update(JobModel)
            .where(JobModel.id == job_id)
            .where(JobModel.status == JobStatus.running.value)
            .where(JobModel.worker_id == queue.worker_id)
            .where(JobModel.cancellation_requested.is_(False))
            .values(
                progress=progress,
                progress_message=message,
                last_heartbeat=now,
                lease_expires_at=now + timedelta(seconds=queue.LEASE_SECONDS),
                updated_at=now,
            ), job_id))
        await session.commit()


async def _heartbeat_once(queue, job_id: str, owner: asyncio.Task | None = None) -> bool:
    """Renew a lease, returning False when this worker lost ownership.

    `owner` is the invocation this heartbeat belongs to: a loss stops IT, never the task
    registered under the same job id now, which may be the attempt that replaced it (AQ1)."""
    now = datetime.utcnow()
    try:
        async with async_session() as session:
            result = await session.execute(_as_mine(
                update(JobModel)
                .where(JobModel.id == job_id)
                .where(JobModel.status == JobStatus.running.value)
                .where(JobModel.worker_id == queue.worker_id)
                .where(JobModel.cancellation_requested.is_(False))
                .values(
                    last_heartbeat=now,
                    lease_expires_at=now + timedelta(seconds=queue.LEASE_SECONDS),
                    updated_at=now,
                ), job_id))
            await session.commit()
    except Exception:
        logger.exception("Heartbeat failed for job %s", job_id)
        return True

    if result.rowcount == 1:
        return True

    queue._lost_ownership_jobs.setdefault(job_id, set()).add(_mine(job_id))
    task = owner if owner is not None else queue._running_jobs.get(job_id)
    # Already cancelled (a person's cancel empties the row too): a second cancel would cut
    # short the wait for its threads that the first one started.
    if task and task is not asyncio.current_task() and not task.cancelling():
        task.cancel()
    logger.warning("Worker %s lost ownership of job %s", queue.worker_id, job_id)
    return False


async def _heartbeat_loop(queue, job_id: str, owner: asyncio.Task | None = None) -> None:
    try:
        while True:
            await asyncio.sleep(queue.HEARTBEAT_SECONDS)
            if not await queue._heartbeat_once(job_id, owner):
                return
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Heartbeat loop stopped for job %s", job_id)


async def _requeue_owned_job(queue, job_id: str) -> bool:
    """Release a job during graceful shutdown without cancelling it — only this task's attempt."""
    now = datetime.utcnow()
    async with async_session() as session:
        result = await session.execute(_as_mine(
            update(JobModel)
            .where(JobModel.id == job_id)
            .where(JobModel.status == JobStatus.running.value)
            .where(JobModel.worker_id == queue.worker_id)
            .values(
                status=JobStatus.queued.value,
                worker_id=None,
                lease_expires_at=None,
                last_heartbeat=None,
                cancellation_requested=False,
                progress=func.min(JobModel.progress, 0.05),
                progress_message="Interrupted by backend shutdown; requeued.",
                updated_at=now,
            ), job_id))
        await session.commit()
    if result.rowcount == 1:
        logger.info("Requeued job %s after graceful shutdown", job_id)
        return True
    return False


async def recover_stuck_jobs(queue):
    """
    On startup, recover only jobs whose lease has expired. A job with a
    live lease belongs to another backend process and must remain running.
    Rows from before lease support have no safe owner, so they are failed
    with an explicit retry message instead of being executed twice.
    """
    from models import ProjectStatus
    from services.clipper.clip_mutations import release_export_claim

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
        if len(recoverable) > queue.MAX_RECOVER:
            logger.warning(
                f"{len(recoverable)} expired jobs — only recovering the first "
                f"{queue.MAX_RECOVER}, failing the rest."
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
                if result.rowcount == 1:        # only a transition this run won
                    await release_export_claim(session, job)
                continue

            project = await session.get(ProjectModel, job.project_id)
            terminal = project and project.status in (
                ProjectStatus.cancelled.value, ProjectStatus.failed.value,
            )
            should_fail = terminal or requeued >= queue.MAX_RECOVER
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
                if result.rowcount == 1:
                    await release_export_claim(session, job)
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


async def stop(queue):
    """Stop the job processor gracefully.

    Marks in-flight jobs as interrupted, then cancels their tasks with a
    short grace period. Tasks that catch the cancellation release their
    lease back to `queued`; a hard process kill leaves the lease to the
    next startup's expiry-based recovery.
    """
    queue._ready = False
    queue._stop_event.set()

    running = list(queue._running_jobs.items())
    if running:
        # 1) Annotate in-flight jobs before cancelling.
        try:
            async with async_session() as session:
                for job_id, _task in running:
                    await session.execute(
                        update(JobModel)
                        .where(JobModel.id == job_id)
                        .where(JobModel.status == JobStatus.running.value)
                        .where(JobModel.worker_id == queue.worker_id)
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

    queue._running_jobs.clear()
    queue._running_types.clear()
    queue._running_attempts.clear()
    logger.info("Job queue processor stopped")
