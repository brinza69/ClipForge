"""
In-memory job tracker for caption-eraser uploads.

These jobs are short-lived, one-shot uploads — no need for full DB persistence.
A dict keyed by job_id is enough: it survives across HTTP requests within the
same process lifetime, which is all the eraser needs.

If the server restarts mid-job, the upload is lost (acceptable — user just
re-uploads). The frontend polls until status is "done" or "failed".
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger("clipforge.erase_jobs")

# Discard jobs older than this many seconds (cleanup pass).
_JOB_TTL_SECONDS = 30 * 60  # 30 minutes


@dataclass
class EraseJob:
    id: str
    status: str = "queued"            # queued | running | done | failed
    progress: float = 0.0             # 0.0 - 1.0
    message: str = ""
    input_path: Optional[str] = None
    output_path: Optional[str] = None
    output_filename: str = "erased.mp4"
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    mode: str = "inpaint"
    # Set by auto-detect runs so the UI can show what was detected.
    detected_segments: Optional[list] = None

    def to_status(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "progress": round(self.progress, 3),
            "message": self.message,
            "error": self.error,
            "filename": self.output_filename if self.status == "done" else None,
            "segments": self.detected_segments,
        }


class EraseJobTracker:
    def __init__(self) -> None:
        self._jobs: Dict[str, EraseJob] = {}
        self._lock = asyncio.Lock()

    def create(self, input_path: str, output_filename: str, mode: str) -> EraseJob:
        job = EraseJob(
            id=uuid.uuid4().hex[:16],
            input_path=input_path,
            output_filename=output_filename,
            mode=mode,
            message="Queued",
        )
        self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Optional[EraseJob]:
        return self._jobs.get(job_id)

    def mark_running(self, job_id: str, message: str = "Processing") -> None:
        job = self._jobs.get(job_id)
        if job:
            job.status = "running"
            job.message = message

    def update_progress(self, job_id: str, progress: float, message: str = "") -> None:
        job = self._jobs.get(job_id)
        if job:
            job.progress = max(0.0, min(1.0, progress))
            if message:
                job.message = message

    def set_segments(self, job_id: str, segments: list) -> None:
        job = self._jobs.get(job_id)
        if job:
            job.detected_segments = segments

    def mark_done(self, job_id: str, output_path: str) -> None:
        job = self._jobs.get(job_id)
        if job:
            job.status = "done"
            job.progress = 1.0
            job.message = "Complete"
            job.output_path = output_path
            job.finished_at = time.time()

    def mark_failed(self, job_id: str, error: str) -> None:
        job = self._jobs.get(job_id)
        if job:
            job.status = "failed"
            job.error = error[-400:]
            job.message = "Failed"
            job.finished_at = time.time()

    def cleanup_stale(self) -> int:
        """Discard finished jobs older than TTL. Returns count removed."""
        now = time.time()
        stale_ids = [
            jid for jid, job in self._jobs.items()
            if job.finished_at and (now - job.finished_at) > _JOB_TTL_SECONDS
        ]
        for jid in stale_ids:
            job = self._jobs.pop(jid, None)
            if job:
                for p in (job.input_path, job.output_path):
                    if p:
                        try:
                            Path(p).unlink(missing_ok=True)
                        except Exception:
                            logger.exception(f"Failed to clean erase job file: {p}")
        if stale_ids:
            logger.info(f"Cleaned {len(stale_ids)} stale erase jobs")
        return len(stale_ids)

    def discard(self, job_id: str) -> None:
        """Remove a job and its files (called after successful download)."""
        job = self._jobs.pop(job_id, None)
        if job:
            for p in (job.input_path, job.output_path):
                if p:
                    try:
                        Path(p).unlink(missing_ok=True)
                    except Exception:
                        logger.exception(f"Failed to clean erase job file: {p}")


# Singleton
erase_jobs = EraseJobTracker()


async def _cleanup_loop(interval: float = 300.0) -> None:
    while True:
        try:
            await asyncio.sleep(interval)
            erase_jobs.cleanup_stale()
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("erase_jobs cleanup loop error")


def start_cleanup_task(loop: Optional[asyncio.AbstractEventLoop] = None) -> asyncio.Task:
    """Schedule the periodic cleanup task on the running loop."""
    loop = loop or asyncio.get_event_loop()
    return loop.create_task(_cleanup_loop())
