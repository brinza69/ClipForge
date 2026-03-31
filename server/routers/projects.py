"""
ClipForge — Projects Router
API endpoints for project CRUD, metadata preview, and actions.
"""

import logging
import shutil
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from models import (
    ProjectModel, ProjectCreate, ProjectResponse, ProjectMetadata,
    ProjectAction, ProjectStatus, ClipModel, ClipStatus,
    JobType, SourceType,
)
from queue import job_queue
from config import settings
from services.metadata import validate_url, detect_source_type

logger = logging.getLogger("clipforge.routers.projects")
router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("/", response_model=list[ProjectResponse])
async def list_projects(session: AsyncSession = Depends(get_session)):
    """List all projects, newest first."""
    result = await session.execute(
        select(ProjectModel).order_by(ProjectModel.created_at.desc())
    )
    projects = result.scalars().all()

    responses = []
    for p in projects:
        # Count clips
        clip_result = await session.execute(
            select(func.count()).where(ClipModel.project_id == p.id)
        )
        clip_count = clip_result.scalar() or 0

        exported_result = await session.execute(
            select(func.count()).where(
                ClipModel.project_id == p.id,
                ClipModel.status == ClipStatus.exported.value,
            )
        )
        exported_count = exported_result.scalar() or 0

        resp = ProjectResponse(
            id=p.id,
            title=p.title,
            source_url=p.source_url,
            source_type=p.source_type,
            status=p.status,
            duration=p.duration,
            width=p.width,
            height=p.height,
            thumbnail_url=p.thumbnail_url,
            thumbnail_path=p.thumbnail_path,
            channel_name=p.channel_name,
            estimated_size=p.estimated_size,
            total_storage=p.total_storage or 0,
            clip_count=clip_count,
            exported_count=exported_count,
            created_at=p.created_at,
            updated_at=p.updated_at,
        )
        responses.append(resp)

    return responses


@router.post("/", response_model=ProjectResponse)
async def create_project(
    data: ProjectCreate,
    session: AsyncSession = Depends(get_session),
):
    """
    Create a new project from a URL.
    Only fetches metadata + thumbnail — does NOT download the video.
    """
    if data.source_url:
        validation = await validate_url(data.source_url)
        if not validation["valid"]:
            raise HTTPException(400, validation.get("error", "Invalid URL"))

    project = ProjectModel(
        title=data.title or "New Project",
        source_url=data.source_url,
        source_type=detect_source_type(data.source_url) if data.source_url else SourceType.local.value,
        status=ProjectStatus.fetching_metadata.value if data.source_url else ProjectStatus.pending.value,
    )
    session.add(project)
    await session.commit()
    await session.refresh(project)

    # Auto-fetch metadata if URL provided
    if data.source_url:
        await job_queue.enqueue(
            project_id=project.id,
            job_type=JobType.fetch_metadata.value,
        )

    return ProjectResponse(
        id=project.id,
        title=project.title,
        source_url=project.source_url,
        source_type=project.source_type,
        status=project.status,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get a single project by ID with clip counts."""
    project = await session.get(ProjectModel, project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    clip_result = await session.execute(
        select(func.count()).where(ClipModel.project_id == project_id)
    )
    clip_count = clip_result.scalar() or 0

    exported_result = await session.execute(
        select(func.count()).where(
            ClipModel.project_id == project_id,
            ClipModel.status == ClipStatus.exported.value,
        )
    )
    exported_count = exported_result.scalar() or 0

    return ProjectResponse(
        id=project.id,
        title=project.title,
        source_url=project.source_url,
        source_type=project.source_type,
        status=project.status,
        duration=project.duration,
        width=project.width,
        height=project.height,
        thumbnail_url=project.thumbnail_url,
        thumbnail_path=project.thumbnail_path,
        channel_name=project.channel_name,
        estimated_size=project.estimated_size,
        total_storage=project.total_storage or 0,
        clip_count=clip_count,
        exported_count=exported_count,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.post("/{project_id}/action")
async def project_action(
    project_id: str,
    action: ProjectAction,
    session: AsyncSession = Depends(get_session),
):
    """
    Execute a user action on a project after metadata preview.
    Actions: download_process, download_only, audio_only, transcribe, score, cancel
    """
    project = await session.get(ProjectModel, project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    act = action.action

    if act == "download_process":
        # Full pipeline: download → transcribe → score
        job_id = await job_queue.enqueue(
            project_id=project_id,
            job_type=JobType.full_pipeline.value,
        )
        return {"job_id": job_id, "action": act, "status": "queued"}

    elif act == "download_only":
        job_id = await job_queue.enqueue(
            project_id=project_id,
            job_type=JobType.download.value,
        )
        return {"job_id": job_id, "action": act, "status": "queued"}

    elif act == "audio_only":
        job_id = await job_queue.enqueue(
            project_id=project_id,
            job_type=JobType.download.value,
            metadata={"audio_only": True},
        )
        return {"job_id": job_id, "action": act, "status": "queued"}

    elif act == "transcribe":
        if not project.video_path:
            raise HTTPException(400, "Video must be downloaded first")
        job_id = await job_queue.enqueue(
            project_id=project_id,
            job_type=JobType.transcribe.value,
        )
        return {"job_id": job_id, "action": act, "status": "queued"}

    elif act == "score":
        job_id = await job_queue.enqueue(
            project_id=project_id,
            job_type=JobType.score.value,
        )
        return {"job_id": job_id, "action": act, "status": "queued"}

    elif act == "cancel":
        project.status = ProjectStatus.cancelled.value
        await session.commit()
        return {"action": act, "status": "cancelled"}

    else:
        raise HTTPException(400, f"Unknown action: {act}")


@router.delete("/{project_id}")
async def delete_project(
    project_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Delete a project and all associated files."""
    project = await session.get(ProjectModel, project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    # Delete files
    for dir_path in [
        settings.media_dir / project_id,
        settings.exports_dir / project_id,
        settings.thumbnails_dir / project_id,
        settings.temp_dir / project_id,
    ]:
        if dir_path.exists():
            shutil.rmtree(dir_path, ignore_errors=True)

    # Delete from database
    await session.execute(delete(ClipModel).where(ClipModel.project_id == project_id))
    await session.delete(project)
    await session.commit()

    return {"deleted": project_id}


@router.get("/{project_id}/metadata", response_model=ProjectMetadata)
async def get_project_metadata(
    project_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get the lightweight metadata preview for a project."""
    from services.metadata import format_duration, format_filesize

    project = await session.get(ProjectModel, project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    return ProjectMetadata(
        title=project.title,
        channel_name=project.channel_name,
        duration=project.duration,
        duration_formatted=format_duration(project.duration),
        source_type=project.source_type,
        width=project.width,
        height=project.height,
        fps=project.fps,
        thumbnail_url=project.thumbnail_url,
        thumbnail_path=project.thumbnail_path,
        estimated_size=project.estimated_size,
        estimated_size_formatted=format_filesize(project.estimated_size),
        upload_date=project.upload_date,
        description=project.description,
    )
