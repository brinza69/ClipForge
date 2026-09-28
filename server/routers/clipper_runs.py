"""
ClipForge Worker — AI Stream Clipper: starting, stopping and inspecting a run.

Split from routers/clipper.py so neither file grows past the repo's 500-line
limit — the same reason clipper_clips.py exists, and mounted the same way,
under the same /api/clipper prefix. Nothing about the URLs changed.

Everything here acts on a project that already exists: kick off the analysis,
cancel it, resume it from the furthest stage whose artifacts can still be
trusted, read those artifacts back, and the ranker's own status.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_session
from job_queue import job_queue
from models import JobModel, JobStatus, JobType, ProjectModel, ProjectStatus, TranscriptModel
from routers.clipper import _TERMINAL, _load_project
from routers.clipper_settings import _err
from services.clipper import ANALYSIS_VERSION
from services.clipper.serialize import job_to_dict, project_to_dict

logger = logging.getLogger("clipforge.clipper.api")

router = APIRouter(prefix="/api/clipper", tags=["clipper"])


@router.post("/projects/{project_id}/analyze")
async def start_analysis(project_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    """Enqueue the analysis pipeline. One run at a time per project."""
    project = await _load_project(session, project_id)

    running = await session.execute(
        select(JobModel)
        .where(JobModel.project_id == project_id)
        .where(JobModel.status.in_([JobStatus.queued.value, JobStatus.running.value]))
        .limit(1)
    )
    if (existing := running.scalar_one_or_none()) is not None:
        return {"job_id": existing.id, "already_running": True}

    project.status = ProjectStatus.pending.value
    project.analysis_version = ANALYSIS_VERSION
    await session.commit()

    job_id = await job_queue.enqueue(
        project_id=project_id,
        job_type=JobType.clipper_ingest.value,
        metadata={"stage": "full"},
    )
    return {"job_id": job_id, "already_running": False}


@router.post("/projects/{project_id}/cancel")
async def cancel_analysis(project_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    await _load_project(session, project_id)
    jobs = await session.execute(
        select(JobModel)
        .where(JobModel.project_id == project_id)
        .where(JobModel.status.in_([JobStatus.queued.value, JobStatus.running.value]))
    )
    cancelled = []
    for job in jobs.scalars():
        await job_queue.cancel_job(job.id)
        cancelled.append(job.id)
    return {"cancelled": cancelled}


@router.post("/projects/{project_id}/retry")
async def retry_analysis(project_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    """Retry from the furthest stage whose artifacts already exist.

    Re-downloading a 4 GB VOD because scoring crashed would be indefensible, so
    each completed stage's output on disk acts as a checkpoint.

    Only a failed or cancelled analysis is resumed, by the predicate the page's
    `retry_allowed` is (`project_attempts.analysis_state`). A ready project or a
    running analysis is 409 with no job and no status change (D2r-3): this
    resumes at `clipper_score` once the candidates exist, so a stale button or a
    direct call used to rescore a ready project. Rescoring is the settings action.

    OW1 (codex-verdict-next-24 §1 (3), Q2): scoring resumes only on the project's
    SELECTED generation, verified in full and written by today's analysis code —
    whether or not its first score got as far as candidates.json. A legacy flat
    analysis, or a generation that does not verify, never resumes at scoring
    because files exist: it is re-analysed from the proxy and audio into a new
    generation. No transcript means the transcribe stage, not analyze.
    """
    from services.clipper import analysis_generation, storage
    from services.clipper.project_attempts import analysis_state

    project = await _load_project(session, project_id)
    state = await analysis_state(session, project)
    if not state["retry_allowed"]:
        attempt = state["analysis_attempt"] or {}
        raise _err(409, "retry_not_applicable",
                   "There is no failed or cancelled analysis to resume.",
                   f"project {project.status}; latest analysis attempt "
                   f"{attempt.get('status') or 'none'}")

    paths = storage.paths(project_id)

    # Existence was the whole test until 2026-08-17, so a signals.json written
    # by different code — or against a different file — resumed exactly like a
    # good one. `stale_artifacts` reads the version and the source size that
    # meta.json has recorded since the clipper shipped and that nothing had
    # ever consumed.
    stale = storage.stale_artifacts(project_id, project.video_path)
    if stale:
        logger.info("clipper retry for %s: ignoring cached analysis (%s)",
                    project_id, stale)

    ctx = await asyncio.to_thread(analysis_generation.open_for, project)
    if ctx.state != analysis_generation.VERIFIED or not ctx.current:
        logger.info("clipper retry for %s: analysis %s is %s (%s)", project_id,
                    ctx.generation, ctx.state, ctx.reason or "not today's analysis_version")
    transcribed = bool(await session.scalar(
        select(TranscriptModel.segments).where(TranscriptModel.project_id == project_id).limit(1)))
    # The code moved, not the media: the proxy and the audio are still a
    # faithful copy of the same file, and re-downloading a 4 GB VOD to
    # recompute signals would be the indefensible half of this endpoint.
    source_moved = bool(stale) and "analysis_version" not in stale
    media = paths["proxy"].exists() and paths["audio"].exists()
    metadata: dict[str, Any] = {"stage": "resume"}
    if not source_moved and media and transcribed and ctx.current:
        resume = JobType.clipper_score.value
        metadata["generation"] = ctx.generation
    elif not source_moved and media:
        resume = (JobType.clipper_analyze.value if transcribed
                  else JobType.clipper_transcribe.value)
    else:
        resume = JobType.clipper_ingest.value

    project.status = ProjectStatus.pending.value
    await session.commit()

    job_id = await job_queue.enqueue(project_id=project_id, job_type=resume, metadata=metadata)
    logger.info(f"clipper retry for {project_id} resuming at {resume}")
    return {"job_id": job_id, "resumed_at": resume}


@router.get("/projects/{project_id}/artifacts/{name}")
async def get_artifact(
    project_id: str, name: str, session: AsyncSession = Depends(get_session)
) -> Any:
    """Read one cached analysis artifact. `name` is checked against a fixed
    allowlist inside storage, so this cannot be walked into another directory."""
    from services.clipper import analysis_generation, storage

    project = await _load_project(session, project_id)
    try:
        if name in analysis_generation.OWNED:
            # OW1: the selected generation's copy, verified; never a partial one.
            ctx = await asyncio.to_thread(analysis_generation.open_for, project)
            data = ctx.read(name)
        else:
            data = storage.read_artifact(project_id, name)
    except analysis_generation.GenerationUnavailable as exc:
        raise _err(409, "analysis_unavailable", str(exc)) from exc
    except ValueError as exc:
        raise _err(400, "unknown_artifact", str(exc)) from exc
    if data is None:
        raise _err(404, "artifact_not_found", f"No {name} artifact for this project yet.")
    return data


# ── Presets + ranker ────────────────────────────────────────────────────────


@router.get("/presets")
async def list_presets() -> dict:
    """Caption presets, straight from the existing preset store so the clipper
    and the rest of the app never drift apart."""
    from services.captioner_presets import DEFAULT_PRESETS

    return {
        "presets": [
            {
                "id": key,
                "name": preset.get("name", key),
                "font_family": preset.get("font_family", ""),
                "font_size": preset.get("font_size", 64),
                "text_color": preset.get("text_color", "#FFFFFF"),
                "highlight_color": preset.get("highlight_color", "#FFD700"),
                "uppercase": bool(preset.get("uppercase", False)),
                "position": preset.get("position", "bottom"),
            }
            for key, preset in DEFAULT_PRESETS.items()
        ]
    }


@router.get("/ranker")
async def ranker_status(session: AsyncSession = Depends(get_session)) -> dict:
    from services.clipper import feedback, ranker

    model = ranker.load_model()
    rows = await feedback.training_rows(session)
    return {
        "enabled": settings.clipper_ranker_enabled,
        "version": (model or {}).get("version"),
        "trained_at": (model or {}).get("trained_at"),
        "training_examples": len(rows),
        "min_training_examples": ranker.MIN_TRAINING_EXAMPLES,
        "active": bool(
            settings.clipper_ranker_enabled and ranker.should_use_learned(model, len(rows))
        ),
        "metrics": (model or {}).get("metrics"),
    }


@router.post("/ranker/train")
async def train_ranker(session: AsyncSession = Depends(get_session)) -> dict:
    """Train the baseline ranker from stored feedback.

    Deliberately synchronous: on any realistic dataset this is a sub-second
    numpy fit, and a job would add more machinery than it saves.
    """
    from services.clipper import feedback, ranker

    rows = await feedback.training_rows(session)
    if len(rows) < ranker.MIN_TRAINING_EXAMPLES:
        return {
            "trained": False,
            "reason": (
                f"Need at least {ranker.MIN_TRAINING_EXAMPLES} reviewed clips to train; "
                f"there are {len(rows)}."
            ),
            "training_examples": len(rows),
        }

    model = ranker.train(rows)
    ranker.save_model(model)
    logger.info(f"clipper ranker trained on {len(rows)} rows: {model.get('metrics')}")
    return {"trained": True, "training_examples": len(rows), "metrics": model.get("metrics"),
            "version": model.get("version")}
