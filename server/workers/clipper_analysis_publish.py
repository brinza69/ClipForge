"""
ClipForge — AI Stream Clipper: publishing ONE analysis attempt (OW1).

`handle_analyze` writes every file into its own generation directory
(services/clipper/analysis_generation.py). This module is the only way that
directory becomes the project's analysis: one short `BEGIN IMMEDIATE`
transaction that re-checks the attempt, selects the generation and schedules
scoring together, so an orphan — a cancelled task's thread, a stale worker, an
older attempt of the same job — can neither select it nor enqueue a score.

THE CURRENT ANALYSIS ATTEMPT (codex-verdict-next-24 §1 (2)). Not
`project_attempts.analysis_state`, which also ranks scoring. Here, over the
project's `clipper_ingest`, `clipper_transcribe` and `clipper_analyze` rows:
  * no OTHER such row was created at or after this job (`created_at`; a tie is
    refused, never guessed) — a newer ingest/transcribe that has not yet made
    its analyze job counts;
  * no other ingest/transcribe row is queued or running, and none was updated
    after the input re-check below — an upstream writer may have replaced the
    proxy, the audio or the transcript under this attempt.
Scoring rows are left out on purpose: a score publishes against the pointer it
was scheduled with and is refused there (clipper_finalize._write_clips).

THE INPUTS. What the attempt read is captured at its start (proxy provenance
identity + proxy/audio stat fingerprints + the transcript row id and its
segments' sha256) and computed again, off the event loop and outside the lock,
right before the transaction. Any difference refuses. The stored identity is
recorded in generation.json; it does not stand in for this re-check.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select, text

from database import async_session
from job_attempt import CLAIMED_ATTEMPT
from job_queue import JobCancelledError
from job_rows import add_job
from models import JobModel, JobStatus, JobType, ProjectModel, ProjectStatus, TranscriptModel
from services.clipper import analysis_generation as ag
from services.clipper import storage

ANALYSIS_TYPES = (JobType.clipper_ingest.value, JobType.clipper_transcribe.value,
                  JobType.clipper_analyze.value)
UPSTREAM_TYPES = (JobType.clipper_ingest.value, JobType.clipper_transcribe.value)
_ACTIVE = (JobStatus.queued.value, JobStatus.running.value)


class PublishRefused(JobCancelledError):
    """This attempt may not publish. Nothing was selected and nothing scheduled."""


@dataclass
class Capture:
    job_id: str
    attempt_count: int
    worker_id: str
    segments: list
    inputs: dict


def _stat(path: Path) -> dict | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}


def input_identity(project_id: str, transcript_id: str | None, segments: Any) -> dict:
    """What this attempt reads, as comparable values. Blocking (hashing)."""
    from services.clipper import scene_address

    paths = storage.paths(project_id)
    prov = scene_address.provenance_now(project_id, str(paths["proxy"]))
    key = json.dumps(scene_address._identity_key(prov), sort_keys=True)
    seg = json.dumps(segments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {"provenance": {"state": prov.get("state"),
                           "identity_sha256": hashlib.sha256(key.encode()).hexdigest()},
            "proxy": _stat(paths["proxy"]), "audio": _stat(paths["audio"]),
            "transcript": {"row_id": transcript_id,
                           "segments_sha256": hashlib.sha256(seg.encode()).hexdigest()}}


async def _transcript(project_id: str) -> tuple[str | None, list]:
    async with async_session() as session:
        row = (await session.execute(
            select(TranscriptModel).where(TranscriptModel.project_id == project_id).limit(1)
        )).scalar_one_or_none()
    segments = (row.segments if row else None) or []
    return (row.id if row else None), (segments if isinstance(segments, list) else [])


async def capture(queue, job_id: str, project_id: str) -> Capture:
    """The attempt's identity and its inputs, read once at its start.

    The identity is the claim the queue set in this task (`job_attempt.CLAIMED_ATTEMPT`, R1c),
    never the row: a takeover between the claim and this read made attempt 1 publish attempt 2's
    `-a2` (codex-verdict-next-27 §4 R1). The row only VALIDATES it; no claim, or a row that is
    no longer this claim, refuses here, before the handler writes anything."""
    claimed = CLAIMED_ATTEMPT.get()
    if claimed is None or claimed.job_id != job_id or claimed.worker != queue.worker_id:
        raise PublishRefused("no claimed attempt of this job runs this handler")
    async with async_session() as session:
        job = await session.get(JobModel, job_id)
    if (job is None or job.status != JobStatus.running.value or job.worker_id != claimed.worker
            or int(job.attempt_count or 0) != claimed.attempt):
        raise PublishRefused(f"attempt {claimed.attempt} of this worker no longer owns the job")
    row_id, segments = await _transcript(project_id)
    inputs = await asyncio.to_thread(input_identity, project_id, row_id, segments)
    return Capture(job_id, claimed.attempt, claimed.worker, segments, inputs)


def write_generation(gdir: Path, sig: dict, regions: Any, by_range: list, faces: dict,
                     header: dict, frames: list[str]) -> dict:
    """Every JSON file of the generation, then its manifest. Blocking: the
    serialisation and the hashing run in a worker thread, outside any lock."""
    ag.write_json(gdir, "signals", sig)
    ag.write_json(gdir, "regions", regions)
    if by_range:
        ag.write_json(gdir, "regions_by_segment", by_range)
    ag.write_json(gdir, "faces", faces)
    return ag.seal(gdir, header, frames, bool(by_range))


@dataclass
class Outcome:
    """What is KNOWN about one publication (codex-verdict-next-27 §4 R2). `commit_started` is set
    right before the COMMIT is sent: from then on the generation may be published — and read by a
    score or a reader pinned to it even after another generation replaced it — whatever the caller
    saw. Only a publication whose COMMIT was never sent is proven unpublished."""
    commit_started: bool = False
    committed: bool = False


async def publish_protected(queue, cap: Capture, project_id: str, gen_id: str, detected: dict,
                            outcome: Outcome) -> None:
    """`publish`, kept and awaited to its end whatever happens to the caller. A cancel of the
    caller mid-transaction is delivered only after the operation ended and `outcome` says what
    it did; it never turns a commit, done or in flight, into permission to delete."""
    op = asyncio.ensure_future(publish(queue, cap, project_id, gen_id, detected, outcome=outcome))
    try:
        await asyncio.shield(op)
    except asyncio.CancelledError:
        if op.done():
            raise                          # the operation itself was cancelled
        while not op.done():               # the caller was: the operation still ends first
            try:
                await asyncio.wait({op})
            except asyncio.CancelledError:
                pass
        if not op.cancelled():
            op.exception()                 # retrieved; what it did is in `outcome`
        raise


async def publish(queue, cap: Capture, project_id: str, gen_id: str, detected: dict, *,
                  outcome: Outcome | None = None) -> None:
    """Select `gen_id` and schedule its scoring, or raise PublishRefused."""
    outcome = outcome if outcome is not None else Outcome()
    row_id = cap.inputs["transcript"]["row_id"]
    checked_at = datetime.utcnow()
    now_row_id, segments = await _transcript(project_id)
    again = await asyncio.to_thread(input_identity, project_id, now_row_id, segments)
    if again != cap.inputs or now_row_id != row_id:
        raise PublishRefused("the inputs this attempt read changed before publication")

    async with async_session() as session:
        await session.execute(text("BEGIN IMMEDIATE"))
        now = datetime.utcnow()
        job = await session.get(JobModel, cap.job_id)
        why = None
        if job is None or job.status != JobStatus.running.value:
            why = "the job is no longer running"
        elif job.cancellation_requested:
            why = "the job was cancelled"
        elif job.worker_id != cap.worker_id or job.worker_id != queue.worker_id:
            why = "another worker owns the job"
        elif int(job.attempt_count or 0) != cap.attempt_count:
            why = f"attempt {job.attempt_count} is running, not {cap.attempt_count}"
        elif job.lease_expires_at is None or job.lease_expires_at <= now:
            why = "the lease expired"
        if why is None:
            other = await session.scalar(
                select(JobModel.id)
                .where(JobModel.project_id == project_id, JobModel.id != cap.job_id,
                       JobModel.type.in_(ANALYSIS_TYPES))
                .where(or_(JobModel.created_at >= job.created_at,
                           JobModel.type.in_(UPSTREAM_TYPES) & (
                               JobModel.status.in_(_ACTIVE) | (JobModel.updated_at >= checked_at))))
                .limit(1))
            if other is not None:
                why = f"job {other} supersedes this analysis attempt"
        project = await session.get(ProjectModel, project_id) if why is None else None
        if why is None and project is None:
            why = "the project is gone"
        elif why is None and project.analysis_generation == gen_id:
            why = f"generation {gen_id} is already published"
        if why is not None:
            await session.rollback()
            raise PublishRefused(f"analysis not published: {why}")
        project.analysis_generation = gen_id
        project.content_type = detected.get("content_type")
        project.content_type_confidence = detected.get("confidence")
        project.status = ProjectStatus.scoring.value
        # `launched_by` is carried to reasoning_run.json; `generation` pins the
        # score to exactly this analysis (next-24 §1 (1)).
        await add_job(session, project_id=project_id, job_type=JobType.clipper_score.value,
                      metadata={"launched_by": "pipeline", "generation": gen_id})
        outcome.commit_started = True
        await session.commit()
        outcome.committed = True
