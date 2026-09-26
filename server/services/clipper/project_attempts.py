"""
ClipForge — AI Stream Clipper: what the job rows say about the latest attempts.

Two readings, both taken from the EXISTING job rows (D2r-3, closure-4 §2): the
project's latest analysis attempt, and each clip's latest preview. Nothing new is
stored for either, and neither relaunches anything.

`get_project` used to put the latest FAILED job of ANY type into `error`, and the
page offered a Retry on it that resumes the analysis — at `clipper_score` once
the artefacts exist. So a discarded preview (routine since D2r-2 K7) offered a
rescore of a `ready` project, and an old analysis failure stayed on the page
after a later success, because "the last failed job" never moves on.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import ClipModel, JobModel, JobStatus, JobType, ProjectModel, ProjectStatus
from services.clipper.serialize import clip_to_dict

# The analysis, by explicit allowlist. Excluding preview/export instead would let
# the next job type that lands on a project decide the project's error.
PIPELINE_TYPES = frozenset({
    JobType.clipper_ingest.value, JobType.clipper_transcribe.value,
    JobType.clipper_analyze.value, JobType.clipper_score.value,
})

# Why `_publish_preview` refused a render — written on the job row by the
# handler, read here. Never inferred from the error text.
DISCARD_INPUTS_CHANGED = "inputs_changed"
DISCARD_NEWER_EXPORT = "newer_export"
DISCARD_CAUSES = frozenset({DISCARD_INPUTS_CHANGED, DISCARD_NEWER_EXPORT})

_ACTIVE = (JobStatus.queued.value, JobStatus.running.value)
_RESUMABLE = (JobStatus.failed.value, JobStatus.cancelled.value)


def _when(job: JobModel) -> str | None:
    return job.created_at.isoformat() if job.created_at else None


async def analysis_state(session: AsyncSession, project: ProjectModel) -> dict[str, Any]:
    """`analysis_attempt`, `error` and `retry_allowed` for the project payload —
    and THE predicate `/retry` refuses by. One function, so the button the page
    shows and the request it sends cannot disagree.

    The attempt is the latest pipeline job by `created_at`: the order the
    attempts were made in, not which old job happened to finish last.

    - `error` is that attempt's error when it `failed`, whatever the project's
      status says: a newer clip job cannot hide it, and an older failure cannot
      outlive a newer attempt (done, cancelled, queued or running).
    - `retry_allowed` needs that attempt `failed` or `cancelled`, no pipeline job
      queued or running (an older one included), and a project that is not
      `ready`: a ready project's board exists, and rescoring it is the settings
      action, never a recovery.
    """
    latest = await session.scalar(
        select(JobModel)
        .where(JobModel.project_id == project.id, JobModel.type.in_(PIPELINE_TYPES))
        .order_by(JobModel.created_at.desc())
        .limit(1))
    active = await session.scalar(
        select(JobModel.id)
        .where(JobModel.project_id == project.id, JobModel.type.in_(PIPELINE_TYPES),
               JobModel.status.in_(_ACTIVE))
        .limit(1))
    if latest is None:
        return {"analysis_attempt": None, "error": None, "retry_allowed": False}
    return {
        "analysis_attempt": {"job_id": latest.id, "type": latest.type, "status": latest.status,
                             "error": latest.error, "created_at": _when(latest)},
        "error": latest.error if latest.status == JobStatus.failed.value else None,
        "retry_allowed": (latest.status in _RESUMABLE and active is None
                          and project.status != ProjectStatus.ready.value),
    }


def _last_preview(job: JobModel) -> dict[str, Any]:
    try:
        meta = json.loads(job.metadata_json or "{}")
    except ValueError:
        meta = {}
    cause = meta.get("discarded") if isinstance(meta, dict) else None
    return {"job_id": job.id, "status": job.status, "error": job.error,
            "discarded": cause if isinstance(cause, str) and cause in DISCARD_CAUSES else None,
            "created_at": _when(job)}


async def clip_cards(session: AsyncSession, clips: list[ClipModel],
                     project: ProjectModel | None) -> list[dict[str, Any]]:
    """`clip_to_dict` for each clip plus `last_preview`: its latest
    `clipper_preview` job by `created_at`, or None — from ONE query for the
    whole list. The latest, not the latest failed: a later successful preview is
    what replaces an earlier failure on the card."""
    ids = [c.id for c in clips]
    previews: dict[str, dict[str, Any]] = {}
    if ids:
        ranked = (
            select(JobModel.id, func.row_number().over(
                partition_by=JobModel.clip_id,
                order_by=JobModel.created_at.desc()).label("n"))
            .where(JobModel.type == JobType.clipper_preview.value, JobModel.clip_id.in_(ids))
            .subquery())
        for job in await session.scalars(
                select(JobModel).join(ranked, ranked.c.id == JobModel.id)
                .where(ranked.c.n == 1)):
            previews[job.clip_id] = _last_preview(job)
    return [{**clip_to_dict(c, project), "last_preview": previews.get(c.id)} for c in clips]
