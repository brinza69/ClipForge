"""
ClipForge Worker - Projects Router

Endpoints:
  POST /api/projects          — create project from URL (auto-fetches metadata)
  GET  /api/projects          — list all projects
  GET  /api/projects/{id}     — get single project
  GET  /api/projects/{id}/metadata — get structured metadata preview
  POST /api/projects/{id}/action   — user action (download, cancel, etc.)
  DELETE /api/projects/{id}   — delete project + files
"""

import logging
import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from models import ProjectModel, ProjectStatus, JobModel, JobType, JobStatus, ClipModel, TranscriptModel
from schemas import (
    ProjectCreate, ProjectResponse, ProjectAction,
    MetadataPreview, MetadataError,
)
from services.downloader import validate_url, detect_source_type, fetch_metadata
from config import settings
from job_queue import job_queue

logger = logging.getLogger("clipforge.routers.projects")
router = APIRouter(prefix="/api/projects", tags=["projects"])


# ── LIST ─────────────────────────────────────────────────────────────────────

@router.get("/", response_model=list[ProjectResponse])
async def list_projects(session: AsyncSession = Depends(get_session)):
    """List all projects, newest first."""
    result = await session.execute(
        select(ProjectModel).order_by(ProjectModel.created_at.desc())
    )
    projects = result.scalars().all()

    # Auto-heal projects whose source video was deleted from disk, or whose
    # pipeline failed at a later stage while the video is still present.
    needs_commit = False
    for project in projects:
        if project.video_path and not Path(project.video_path).exists():
            if project.status in (
                ProjectStatus.downloaded.value,
                ProjectStatus.transcribed.value,
                ProjectStatus.ready.value,
                ProjectStatus.failed.value,
            ):
                project.status = ProjectStatus.metadata_ready.value
                project.video_path = None
                needs_commit = True
        elif (
            project.video_path
            and Path(project.video_path).exists()
            and project.status == ProjectStatus.failed.value
        ):
            project.status = ProjectStatus.downloaded.value
            needs_commit = True
    if needs_commit:
        await session.commit()

    return projects


# ── CREATE ───────────────────────────────────────────────────────────────────

@router.post("/", response_model=ProjectResponse, status_code=201)
async def create_project(
    data: ProjectCreate,
    session: AsyncSession = Depends(get_session),
):
    """
    Create a new project from a URL.
    Immediately kicks off metadata extraction (no download).
    """
    if data.source_url:
        check = await validate_url(data.source_url)
        if not check["valid"]:
            raise HTTPException(400, check.get("error", "Invalid URL"))

    project = ProjectModel(
        title=data.title or "New Project",
        source_url=data.source_url,
        source_type=detect_source_type(data.source_url) if data.source_url else "local",
        status=ProjectStatus.fetching_metadata.value if data.source_url else ProjectStatus.pending.value,
    )
    session.add(project)
    await session.commit()
    await session.refresh(project)

    # Fetch metadata in background — but since this is Phase 1 and we have
    # no job queue yet, we do it inline (fast enough for metadata-only).
    if data.source_url:
        meta = await fetch_metadata(data.source_url, project.id)

        if "error" in meta:
            # Metadata fetch failed — store the error but don't crash
            project.status = ProjectStatus.failed.value
            project.description = f"[{meta.get('error_code', 'unknown')}] {meta['error']}"
            await session.commit()
            await session.refresh(project)
            return project

        # Store successful metadata
        project.title = meta.get("title") or project.title
        project.channel_name = meta.get("channel_name")
        project.duration = meta.get("duration")
        project.width = meta.get("width")
        project.height = meta.get("height")
        project.fps = meta.get("fps")
        project.thumbnail_url = meta.get("thumbnail_url")
        project.estimated_size = meta.get("estimated_size")
        project.upload_date = meta.get("upload_date")
        project.description = meta.get("description")
        project.webpage_url = meta.get("webpage_url")
        project.extractor = meta.get("extractor")
        project.is_live = meta.get("is_live")
        project.was_live = meta.get("was_live")
        project.availability = meta.get("availability")
        project.status = ProjectStatus.metadata_ready.value

        await session.commit()
        await session.refresh(project)

    return project


# ── GET SINGLE ───────────────────────────────────────────────────────────────

@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get a single project by ID."""
    project = await session.get(ProjectModel, project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    # Auto-heal stale project state based on actual files on disk.
    needs_commit = False
    if project.video_path and not Path(project.video_path).exists():
        # Video file was deleted — reset so user can re-download.
        if project.status in (
            ProjectStatus.downloaded.value,
            ProjectStatus.transcribed.value,
            ProjectStatus.ready.value,
            ProjectStatus.failed.value,
        ):
            project.status = ProjectStatus.metadata_ready.value
            project.video_path = None
            needs_commit = True
    elif (
        project.video_path
        and Path(project.video_path).exists()
        and project.status == ProjectStatus.failed.value
    ):
        # Video exists but pipeline failed at a later stage — reset to
        # downloaded so the user can retry transcription/scoring.
        project.status = ProjectStatus.downloaded.value
        needs_commit = True

    if needs_commit:
        await session.commit()
        await session.refresh(project)

    return project


# ── METADATA PREVIEW ─────────────────────────────────────────────────────────

@router.get("/{project_id}/metadata")
async def get_project_metadata(
    project_id: str,
    session: AsyncSession = Depends(get_session),
):
    """
    Get the lightweight metadata preview for a project.
    Returns MetadataPreview on success, MetadataError on failure.
    """
    project = await session.get(ProjectModel, project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    # If the project failed, return the structured error
    if project.status == ProjectStatus.failed.value:
        desc = project.description or ""
        code = "unknown"
        if desc.startswith("[") and "]" in desc:
            code = desc.split("]")[0].lstrip("[")
            desc = desc.split("]", 1)[1].strip()
        return MetadataError(
            error=desc,
            error_code=code,
            suggestion=_suggestion_for_code(code),
            url=project.source_url,
        )

    # Format helpers
    duration_fmt = None
    if project.duration:
        h, rem = divmod(int(project.duration), 3600)
        m, s = divmod(rem, 60)
        duration_fmt = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    size_fmt = None
    if project.estimated_size:
        sz = project.estimated_size
        if sz >= 1_073_741_824:
            size_fmt = f"{sz / 1_073_741_824:.1f} GB"
        elif sz >= 1_048_576:
            size_fmt = f"{sz / 1_048_576:.0f} MB"
        else:
            size_fmt = f"{sz / 1024:.0f} KB"

    return MetadataPreview(
        title=project.title,
        channel_name=project.channel_name,
        duration=project.duration,
        duration_formatted=duration_fmt,
        source_type=project.source_type,
        extractor=project.extractor,
        webpage_url=project.webpage_url,
        width=project.width,
        height=project.height,
        fps=project.fps,
        thumbnail_url=project.thumbnail_url,
        estimated_size=project.estimated_size,
        estimated_size_formatted=size_fmt,
        upload_date=project.upload_date,
        description=project.description,
        is_live=project.is_live,
        was_live=project.was_live,
        availability=project.availability,
    )


# ── ACTION ───────────────────────────────────────────────────────────────────

@router.post("/{project_id}/action")
async def project_action(
    project_id: str,
    action: ProjectAction,
    session: AsyncSession = Depends(get_session),
):
    """
    Execute a user action after metadata preview.
    Phase 1 stub — actual download/transcribe will be added later.
    """
    project = await session.get(ProjectModel, project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    act = action.action

    if act == "cancel":
        result = await session.execute(
            select(JobModel)
            .where(JobModel.project_id == project_id)
            .where(JobModel.status.in_([JobStatus.queued.value, JobStatus.running.value]))
        )
        for j in result.scalars().all():
            await job_queue.cancel_job(j.id)

        project.status = ProjectStatus.cancelled.value
        await session.commit()
        return {"action": act, "status": "cancelled"}

    job_metadata = {}
    job_type = None

    if act == "download_process":
        job_type = JobType.full_pipeline.value
    elif act == "download_only":
        job_type = JobType.download.value
    elif act == "audio_only":
        job_type = JobType.download.value
        job_metadata = {"audio_only": True}
    elif act == "transcribe":
        job_type = JobType.transcribe.value
    elif act == "score":
        job_type = JobType.score.value
    else:
        raise HTTPException(400, f"Unknown action: {act}")

    job_id = await job_queue.enqueue(
        project_id=project_id,
        job_type=job_type,
        metadata=job_metadata,
    )

    return {
        "action": act,
        "status": "queued",
        "job_id": job_id,
        "message": f"Action '{act}' has been queued for background processing.",
    }


# ── DOWNLOAD SOURCE VIDEO ────────────────────────────────────────────────────

@router.get("/{project_id}/source")
async def download_source_video(
    project_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Stream the downloaded source video file back to the browser."""
    project = await session.get(ProjectModel, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    if not project.video_path:
        raise HTTPException(404, "Project has no downloaded source video")

    video_path = Path(project.video_path)
    if not video_path.exists():
        raise HTTPException(404, f"Source file missing on disk: {project.video_path}")

    # Build a clean filename: "{title}.{ext}" stripped of filesystem-hostile chars
    ext = video_path.suffix or ".mp4"
    safe_title = "".join(
        c if (c.isalnum() or c in " _-.") else "_"
        for c in (project.title or project_id)
    ).strip() or project_id
    filename = f"{safe_title}{ext}"

    return FileResponse(
        path=video_path,
        filename=filename,
        media_type="video/mp4",
    )


# ── DELETE ───────────────────────────────────────────────────────────────────

@router.delete("/{project_id}")
async def delete_project(
    project_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Delete a project and all associated files."""
    project = await session.get(ProjectModel, project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    # Cancel any running/queued jobs first
    result = await session.execute(
        select(JobModel).where(JobModel.project_id == project_id)
    )
    for j in result.scalars().all():
        if j.status in (JobStatus.queued.value, JobStatus.running.value):
            await job_queue.cancel_job(j.id)

    # Remove associated rows so we don't leave "zombie" queued jobs.
    await session.execute(delete(JobModel).where(JobModel.project_id == project_id))
    await session.execute(delete(ClipModel).where(ClipModel.project_id == project_id))
    await session.execute(delete(TranscriptModel).where(TranscriptModel.project_id == project_id))

    # Clean up directories
    for subdir in [settings.media_dir, settings.exports_dir,
                   settings.thumbnails_dir, settings.temp_dir]:
        target = subdir / project_id
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)

    await session.delete(project)
    await session.commit()
    return {"deleted": project_id}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _suggestion_for_code(code: str) -> str:
    suggestions = {
        "geo_blocked": "Try using a VPN, or download the file manually and use local upload.",
        "login_required": "Download manually using yt-dlp with --cookies, then upload the file.",
        "drm_protected": "DRM content cannot be downloaded. Try screen-recording or local upload.",
        "private_video": "This video is private or deleted. Nothing can be done.",
        "live_stream": "Wait for the VOD to become available, then retry.",
        "age_restricted": "Configure yt-dlp cookies from a logged-in browser session.",
        "unsupported_site": "Try a direct MP4 link or local file upload instead.",
        "network_error": "Check your internet connection and retry.",
        "forbidden": "The link may be expired or restricted. Try getting a fresh URL.",
        "not_found": "Double-check the URL. The video may have been removed.",
    }
    return suggestions.get(code, "Try a different link or use local file upload.")
