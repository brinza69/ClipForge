"""
ClipForge Worker — AI Stream Clipper: project-level API.

Clip-level operations live in routers/clipper_clips.py so neither file grows
past the repo's 500-line limit.

Conventions followed from the rest of the app: raw dicts rather than response
models (the same choice doodle/tiktok make), structured
{detail:{error,message,details}} errors, and progress delivered over the
EXISTING job SSE endpoint at /api/jobs/{id}/stream rather than a new transport.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_session
from job_queue import job_queue
from models import (
    ClipModel,
    ClipStatus,
    JobModel,
    JobStatus,
    JobType,
    ProjectModel,
    ProjectStatus,
)
from services.clipper import ANALYSIS_VERSION
from services.clipper.serialize import (
    PROJECT_PATCHABLE,
    PROJECT_PATCHABLE_JSON,
    apply_patch,
    clip_to_dict,
    job_to_dict,
    project_to_dict,
)

logger = logging.getLogger("clipforge.clipper.api")

router = APIRouter(prefix="/api/clipper", tags=["clipper"])

# Terminal project states — the frontend stops polling on these.
_TERMINAL = {ProjectStatus.ready.value, ProjectStatus.failed.value, ProjectStatus.cancelled.value}

_ALLOWED_UPLOAD_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".ts", ".flv"}


def _err(status: int, code: str, message: str, details: str = "") -> HTTPException:
    """Structured error matching main.py's unhandled-exception shape, so the
    frontend's readApiError() reads both the same way."""
    return HTTPException(status, {"error": code, "message": message, "details": details})


def _default_settings() -> dict[str, Any]:
    """Mirrors DEFAULT_SETTINGS in src/types/clipper.ts, sourced from config so
    the operator can retune the rig without touching the frontend."""
    return {
        "clip_count": settings.clipper_default_clip_count,
        "min_clip_s": settings.clipper_min_clip_s,
        "max_clip_s": settings.clipper_max_clip_s,
        "platform": "tiktok",
        "fps": settings.clipper_export_fps,
        "language": "auto",
        "caption_preset_id": "bold_impact",
        "caption_position": "bottom",
        "caption_highlight": True,
        "headline_enabled": True,
        "headline_auto": True,
        "emoji_enabled": False,
        "profanity_mask": False,
        "trim_silence": True,
        "jump_cuts": False,
        "auto_zoom": True,
        "reaction_zoom": True,
        "facecam_emphasis": True,
        "include_chat": False,
        "watermark_text": "",
        "min_score": 0,
        "layout_mode": "auto",
        "face_pct": settings.clipper_face_pct,
    }


def _normalise_settings(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Merge client settings over the defaults and clamp everything.

    Clamping happens here, once, rather than in each consumer: an out-of-range
    value from a hand-rolled API call must never reach the pipeline (a
    max_clip_s of 10 hours would make the renderer try to encode the whole VOD).
    """
    out = _default_settings()
    for key, value in (raw or {}).items():
        if key in out and value is not None:
            out[key] = value

    def _num(key: str, lo: float, hi: float, cast=float):
        try:
            out[key] = cast(max(lo, min(hi, cast(out[key]))))
        except (TypeError, ValueError):
            out[key] = cast(_default_settings()[key])

    _num("clip_count", 1, 20, int)
    _num("min_clip_s", 3, 600)
    _num("max_clip_s", 5, 900)
    _num("face_pct", 0.15, 0.6)
    _num("min_score", 0, 100)

    # A min above max would produce zero candidates with no visible reason.
    if out["min_clip_s"] >= out["max_clip_s"]:
        out["min_clip_s"], out["max_clip_s"] = (
            settings.clipper_min_clip_s,
            settings.clipper_max_clip_s,
        )

    if out.get("platform") not in {"tiktok", "youtube_shorts", "instagram_reels", "facebook_reels"}:
        out["platform"] = "tiktok"
    if out.get("caption_position") not in {"bottom", "center", "top"}:
        out["caption_position"] = "bottom"
    if out.get("fps") not in {"source", 30, 60}:
        out["fps"] = settings.clipper_export_fps
    out["watermark_text"] = str(out.get("watermark_text") or "")[:80]
    return out


# ── Source preview + upload ─────────────────────────────────────────────────


@router.post("/preview")
async def preview_source(payload: dict) -> dict:
    """Metadata WITHOUT downloading. Runs the URL policy check first, so a
    blocked URL costs nothing and returns an actionable reason."""
    from services.clipper.ingest import probe_source

    url = (payload or {}).get("url") or ""
    if not str(url).strip():
        raise _err(400, "empty_url", "Paste a video URL first.")

    result = await probe_source(str(url).strip())
    if result.get("error"):
        # Returned as 200 with an error body: the frontend renders this inline
        # next to the field (with the suggestion as help text), not as a toast.
        return result
    return result


@router.post("/upload")
async def upload_source(file: UploadFile = File(...)) -> dict:
    """Accept a local video and park it in a staging dir.

    The file is NOT attached to a project yet — the client posts the returned
    path back with the project payload. That keeps the create call uniform
    between the url and upload paths.
    """
    name = Path(file.filename or "upload.mp4").name  # strip any directory part
    suffix = Path(name).suffix.lower()
    if suffix not in _ALLOWED_UPLOAD_SUFFIXES:
        raise _err(
            400,
            "unsupported_upload",
            f"{suffix or 'That file type'} is not a supported video container.",
            f"Supported: {', '.join(sorted(_ALLOWED_UPLOAD_SUFFIXES))}",
        )

    staging = settings.clipper_dir / "_uploads"
    staging.mkdir(parents=True, exist_ok=True)
    # Server-generated name: the user's filename never reaches the filesystem.
    dest = staging / f"{uuid.uuid4().hex[:12]}{suffix}"

    size = 0
    limit = settings.clipper_max_upload_bytes
    try:
        with dest.open("wb") as fh:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise _err(
                        413,
                        "upload_too_large",
                        f"That file is over the {limit // (1024**3)} GB upload limit.",
                    )
                fh.write(chunk)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    except Exception as exc:
        dest.unlink(missing_ok=True)
        logger.exception("upload failed")
        raise _err(500, "upload_failed", "Could not save the uploaded file.", str(exc)[:200])

    return {"upload_path": str(dest), "filename": name, "size": size}


# ── Projects ────────────────────────────────────────────────────────────────


@router.post("/projects")
async def create_project(payload: dict, session: AsyncSession = Depends(get_session)) -> dict:
    """Create a clipper project. Does NOT start analysis — the client calls
    /analyze separately so the settings screen can be revisited first."""
    from services.clipper import storage

    source_kind = (payload or {}).get("source_kind") or "url"
    url = (payload.get("url") or "").strip() or None
    upload_path = (payload.get("upload_path") or "").strip() or None

    if not payload.get("rights_confirmed"):
        raise _err(
            400,
            "rights_not_confirmed",
            "Confirm you own this content or have permission to process it.",
        )

    if source_kind == "url":
        if not url:
            raise _err(400, "empty_url", "A source URL is required.")
        from services.clipper.urlguard import UrlRejected, check_url

        try:
            checked = check_url(url)
        except UrlRejected as exc:
            raise _err(400, exc.code, exc.message, exc.suggestion) from exc
        source_type = checked["source_type"]
    elif source_kind == "upload":
        if not upload_path or not Path(upload_path).exists():
            raise _err(400, "missing_upload", "Upload the video file first.")
        source_type = "local"
    else:
        raise _err(
            400,
            "unsupported_source_kind",
            "Only URL and upload sources are wired up right now.",
        )

    project = ProjectModel(
        title=(payload.get("title") or "").strip()[:400] or "Untitled clip project",
        source_url=url,
        source_type=source_type,
        source_kind=source_kind,
        status=ProjectStatus.pending.value,
        processing_mode="clipping",
        clipper_settings=_normalise_settings(payload.get("settings")),
        rights_confirmed=True,
        analysis_version=ANALYSIS_VERSION,
    )
    session.add(project)
    await session.commit()

    storage.ensure_dirs(project.id)
    # The staged upload moves under the project so cleanup is a single rmtree.
    if source_kind == "upload" and upload_path:
        dest = storage.paths(project.id)["source_dir"] / Path(upload_path).name
        try:
            Path(upload_path).replace(dest)
            project.video_path = str(dest)
            await session.commit()
        except OSError:
            logger.exception("could not move staged upload into the project dir")
            raise _err(500, "upload_move_failed", "Could not stage the uploaded file.")

    logger.info(f"clipper project created: {project.id} ({source_kind})")
    return project_to_dict(project)


@router.get("/projects")
async def list_projects(session: AsyncSession = Depends(get_session)) -> list[dict]:
    """Summaries, newest first. Clip counts come from one grouped query rather
    than N per-project queries."""
    result = await session.execute(
        select(ProjectModel)
        .where(ProjectModel.processing_mode == "clipping")
        .order_by(ProjectModel.created_at.desc())
        .limit(200)
    )
    projects = list(result.scalars().all())
    if not projects:
        return []

    ids = [p.id for p in projects]
    counts = await session.execute(
        select(ClipModel.project_id, ClipModel.status, func.count(ClipModel.id))
        .where(ClipModel.project_id.in_(ids))
        .group_by(ClipModel.project_id, ClipModel.status)
    )
    tally: dict[str, dict[str, int]] = {}
    for project_id, status, count in counts.all():
        bucket = tally.setdefault(project_id, {})
        bucket[status] = count

    out = []
    for project in projects:
        bucket = tally.get(project.id, {})
        row = project_to_dict(project)
        row["clip_count"] = sum(bucket.values())
        row["approved_count"] = bucket.get(ClipStatus.approved.value, 0)
        row["exported_count"] = bucket.get(ClipStatus.exported.value, 0)
        out.append(row)
    return out


async def _load_project(session: AsyncSession, project_id: str) -> ProjectModel:
    project = await session.get(ProjectModel, project_id)
    if not project:
        raise _err(404, "project_not_found", "That clip project no longer exists.")
    return project


@router.get("/projects/{project_id}")
async def get_project(project_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    """Full project: candidates in rank order plus the job currently running,
    which is everything the detail page needs to re-derive its state after a
    reload."""
    project = await _load_project(session, project_id)

    clips = await session.execute(
        select(ClipModel)
        .where(ClipModel.project_id == project_id)
        .order_by(
            # NULLs last so unranked candidates don't jump to the top.
            ClipModel.rank_position.is_(None),
            ClipModel.rank_position,
            ClipModel.start_time,
        )
    )
    active = await session.execute(
        select(JobModel)
        .where(JobModel.project_id == project_id)
        .where(JobModel.status.in_([JobStatus.queued.value, JobStatus.running.value]))
        .order_by(JobModel.created_at.desc())
        .limit(1)
    )
    last_failed = await session.execute(
        select(JobModel)
        .where(JobModel.project_id == project_id)
        .where(JobModel.status == JobStatus.failed.value)
        .order_by(JobModel.created_at.desc())
        .limit(1)
    )
    failed_job = last_failed.scalar_one_or_none()
    active_job = active.scalar_one_or_none()

    payload = project_to_dict(project)
    payload["clips"] = [clip_to_dict(c) for c in clips.scalars().all()]
    payload["active_job"] = job_to_dict(active_job) if active_job else None
    payload["error"] = failed_job.error if (failed_job and not active_job) else None
    return payload


@router.patch("/projects/{project_id}/settings")
async def patch_settings(
    project_id: str, payload: dict, session: AsyncSession = Depends(get_session)
) -> dict:
    """Update settings and/or the content-type override.

    The override is intentionally free of validation against detection: the
    user is always allowed to disagree with the classifier (brief §16).
    """
    project = await _load_project(session, project_id)

    body = dict(payload or {})
    if "settings" in body:
        body["clipper_settings"] = _normalise_settings(body.pop("settings"))

    changed = apply_patch(project, body, PROJECT_PATCHABLE, PROJECT_PATCHABLE_JSON)
    if changed:
        await session.commit()
    return {"project": project_to_dict(project), "changed": changed}


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    """Delete the project, its candidates, its feedback and every artifact.

    Disk first, rows second: an orphaned row is recoverable, an orphaned
    multi-GB directory is what actually fills the drive.
    """
    from sqlalchemy import delete as sql_delete

    from models import ClipFeedbackModel
    from services.clipper import storage

    project = await _load_project(session, project_id)

    for job in (
        await session.execute(
            select(JobModel)
            .where(JobModel.project_id == project_id)
            .where(JobModel.status.in_([JobStatus.queued.value, JobStatus.running.value]))
        )
    ).scalars():
        await job_queue.cancel_job(job.id)

    try:
        storage.delete_project(project_id)
    except Exception:
        logger.exception(f"artifact cleanup failed for {project_id}")

    await session.execute(sql_delete(ClipFeedbackModel).where(ClipFeedbackModel.project_id == project_id))
    await session.execute(sql_delete(ClipModel).where(ClipModel.project_id == project_id))
    await session.execute(sql_delete(JobModel).where(JobModel.project_id == project_id))
    await session.delete(project)
    await session.commit()
    return {"deleted": project_id}


# ── Pipeline control ────────────────────────────────────────────────────────


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
    """
    from services.clipper import storage

    project = await _load_project(session, project_id)

    paths = storage.paths(project_id)
    if storage.artifact_exists(project_id, "signals") and paths["proxy"].exists():
        resume = JobType.clipper_score.value if storage.artifact_exists(
            project_id, "candidates"
        ) else JobType.clipper_analyze.value
    elif paths["proxy"].exists() and paths["audio"].exists():
        resume = JobType.clipper_transcribe.value
    else:
        resume = JobType.clipper_ingest.value

    project.status = ProjectStatus.pending.value
    await session.commit()

    job_id = await job_queue.enqueue(
        project_id=project_id, job_type=resume, metadata={"stage": "resume"}
    )
    logger.info(f"clipper retry for {project_id} resuming at {resume}")
    return {"job_id": job_id, "resumed_at": resume}


@router.get("/projects/{project_id}/artifacts/{name}")
async def get_artifact(
    project_id: str, name: str, session: AsyncSession = Depends(get_session)
) -> Any:
    """Read one cached analysis artifact. `name` is checked against a fixed
    allowlist inside storage, so this cannot be walked into another directory."""
    from services.clipper import storage

    await _load_project(session, project_id)
    try:
        data = storage.read_artifact(project_id, name)
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
