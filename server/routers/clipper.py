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
from services.clipper import ANALYSIS_VERSION, reasoning_mode
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


# The settings contract lives in clipper_settings.py — re-exported because the
# tests reach for these by their old names and both entry points below use them.
from routers.clipper_settings import (  # noqa: E402,F401
    _NOT_AVAILABLE_YET, _default_settings, _err, _normalise_settings,
    _rig_reasoning_mode,
)


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


def _staged_upload(raw: str) -> Path:
    """Resolve a client-supplied upload reference to a file we actually staged.

    `create_project` used to take `upload_path` as a PATH, check only that it
    existed, and then `.replace()` it into the project directory. That is a
    move of any file the server process can reach — the client picked the
    source, the server carried it out. There is no authentication anywhere in
    ClipForge, so this was not privilege escalation over an already-open
    perimeter; it was still a filesystem primitive handed out for free, and the
    fix is small enough that arguing about its priority costs more than doing
    it.

    Only the NAME is taken from the client, never the location. `Path(...).name`
    strips every directory component, so `..`, an absolute path and a UNC share
    all collapse to a bare filename that is then looked up in the staging dir
    this server wrote it to. The containment re-check after `resolve()` is
    belt and braces: it also catches a symlink planted inside staging, which
    resolves outward.
    """
    name = Path(raw).name
    if not name or name in (".", ".."):
        raise _err(400, "missing_upload", "Upload the video file first.")
    if Path(name).suffix.lower() not in _ALLOWED_UPLOAD_SUFFIXES:
        raise _err(400, "unsupported_upload",
                   "That file type is not a supported video container.")

    staging = (settings.clipper_dir / "_uploads").resolve()
    candidate = (staging / name).resolve()
    if not candidate.is_relative_to(staging):
        raise _err(400, "missing_upload", "Upload the video file first.")
    if not candidate.is_file():
        raise _err(400, "missing_upload", "Upload the video file first.",
                   "The staged file is gone — upload it again.")
    return candidate


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
        if not upload_path:
            raise _err(400, "missing_upload", "Upload the video file first.")
        staged = _staged_upload(upload_path)
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
        dest = storage.paths(project.id)["source_dir"] / staged.name
        try:
            staged.replace(dest)
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


async def _board(session: AsyncSession, project: ProjectModel) -> list[dict]:
    """The project's clips in board order, each with its effective caption policy."""
    clips = await session.execute(
        select(ClipModel)
        .where(ClipModel.project_id == project.id)
        .order_by(
            # NULLs last so unranked candidates don't jump to the top.
            ClipModel.rank_position.is_(None),
            ClipModel.rank_position,
            ClipModel.start_time,
        )
    )
    return [clip_to_dict(c, project) for c in clips.scalars().all()]


@router.get("/projects/{project_id}")
async def get_project(project_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    """Full project: candidates in rank order plus the job currently running,
    which is everything the detail page needs to re-derive its state after a
    reload."""
    project = await _load_project(session, project_id)

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
    payload["clips"] = await _board(session, project)
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

    `source_has_burned_captions` is classified apart from every other setting
    (B2, PRPs/clipper-master-plan-2026-09-24.md §5): scoring never reads it, so
    changing it alone queues no rescore, and the clips whose render it flips
    are invalidated here, in the same transaction, or the request is refused
    whole — see `apply_project_answer`.
    """
    from routers.clipper_caption_source import apply_project_answer
    from services.clipper import caption_policy
    from services.clipper.clip_mutations import begin_write

    # B1's pattern (`clip_mutations.lock_clip`): SQLite's write lock BEFORE the
    # read, so the `exporting` check on the affected clips sees the rows the
    # write lands on — two backends share this DB, a claim can come from either.
    # ONE BEGIN for the project and every inheriting clip (C4), never a
    # `lock_clip` per clip; a lock held elsewhere is 503 `database_busy` (R4a).
    await begin_write(session)
    project = await session.get(ProjectModel, project_id, populate_existing=True)
    if not project:
        raise _err(404, "project_not_found", "That clip project no longer exists.")

    body = dict(payload or {})
    scoring_changed = False
    caption_source = {"changed": False, "affected_clip_ids": [],
                      "invalidated_clip_ids": [], "export_cleared_clip_ids": []}
    if "settings" in body:
        stored = project.clipper_settings or {}
        # Merged over what the project ALREADY has, not over the defaults. A
        # PATCH is allowed to be partial, and normalising the partial dict on
        # its own silently reverted every key it did not mention — including the
        # two whose whole design is that an unrelated edit must not move them.
        # The browser happens to post the entire object, which is why this went
        # unseen; a hand-rolled call is not obliged to.
        new = _normalise_settings({**stored, **dict(body.pop("settings") or {})})
        # The EFFECTIVE delta, both sides normalised: a stored dict that merely
        # lacks a key the rig now defaults is not a change. One that no longer
        # normalises (a mode since withdrawn) counts as every key changed —
        # the rescore it used to get.
        try:
            old = _normalise_settings(stored)
        except HTTPException:
            old = {}
        delta = {k for k in set(new) | set(old) if k not in old or old[k] != new.get(k)}
        scoring_changed = bool(delta - {caption_policy.SETTING})
        if delta:
            body["clipper_settings"] = new
            caption_source = await apply_project_answer(
                session, project, stored.get(caption_policy.SETTING),
                new[caption_policy.SETTING])

    changed = apply_patch(project, body, PROJECT_PATCHABLE, PROJECT_PATCHABLE_JSON)
    # Unconditional: it also ends the write lock. With nothing changed it is an
    # empty transaction and writes nothing.
    await session.commit()

    # Overriding the content type has to actually change something. The profile
    # picks the scoring weights AND the default layout, both of which were
    # frozen onto the existing clips at score time — so without this the UI
    # offers a control that visibly does nothing. Re-scoring is cheap because it
    # reuses the cached transcript, signals and candidates: seconds on a short
    # source, a few minutes on a 6-hour one, and no re-download or re-transcribe.
    rescored_job: str | None = None
    if "content_type_override" in changed or scoring_changed:
        from services.clipper import storage

        has_analysis = storage.artifact_exists(project_id, "candidates")
        busy = await session.execute(
            select(JobModel)
            .where(JobModel.project_id == project_id)
            .where(JobModel.status.in_([JobStatus.queued.value, JobStatus.running.value]))
            .limit(1)
        )
        if has_analysis and busy.scalar_one_or_none() is None:
            rescored_job = await job_queue.enqueue(
                project_id=project_id,
                job_type=JobType.clipper_score.value,
                metadata={"stage": "rescore", "launched_by": "api"},
            )

    return {
        "project": project_to_dict(project),
        "changed": changed,
        "rescore_job_id": rescored_job,
        "caption_source": caption_source,
        "clips": await _board(session, project),
    }


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
