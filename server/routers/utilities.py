"""
ClipForge — Utilities Router
Quick-download: paste a URL and kick off the full pipeline immediately.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from models import ProjectModel, ProjectStatus, JobType
from services.downloader import validate_url, detect_source_type, fetch_metadata
from job_queue import job_queue

logger = logging.getLogger("clipforge.routers.utilities")
router = APIRouter(prefix="/api/utilities", tags=["utilities"])


class QuickDownloadRequest(BaseModel):
    url: str
    title: Optional[str] = None


@router.post("/download")
async def quick_download(
    data: QuickDownloadRequest,
    session: AsyncSession = Depends(get_session),
):
    """
    Create a project from a URL and immediately enqueue the full pipeline.
    Fetches metadata inline, then queues download → transcribe → score.
    """
    check = await validate_url(data.url)
    if not check["valid"]:
        raise HTTPException(400, check.get("error", "Invalid or unsupported URL"))

    project = ProjectModel(
        title=data.title or "Quick Download",
        source_url=data.url,
        source_type=detect_source_type(data.url),
        status=ProjectStatus.fetching_metadata.value,
    )
    session.add(project)
    await session.commit()
    await session.refresh(project)

    # Fetch metadata inline (fast)
    meta = await fetch_metadata(data.url, project.id)
    if "error" in meta:
        project.status = ProjectStatus.failed.value
        project.description = f"[{meta.get('error_code', 'unknown')}] {meta['error']}"
        await session.commit()
        raise HTTPException(422, meta["error"])

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

    # Enqueue full pipeline immediately
    job_id = await job_queue.enqueue(
        project_id=project.id,
        job_type=JobType.full_pipeline.value,
    )

    logger.info(f"Quick-download queued: project={project.id} job={job_id} url={data.url}")
    return {"project_id": project.id, "job_id": job_id, "title": project.title}
