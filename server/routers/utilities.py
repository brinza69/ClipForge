"""
ClipForge — Utilities Router
Quick-download: paste a URL and kick off the full pipeline immediately.
Caption Eraser: upload a video and blur/erase a rectangular region using FFmpeg.
"""

import logging
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
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


_ERASE_WORK_PROJECT_ID = "__utility__"


def _erase_workdir(job_id: str) -> Path:
    return Path(settings.temp_dir) / "erase" / job_id


@router.post("/erase")
async def erase_region(
    file: UploadFile = File(...),
    x: int = Form(0),
    y: int = Form(0),
    w: int = Form(100),
    h: int = Form(50),
    mode: str = Form("inpaint"),       # "inpaint" (OpenCV TELEA) or "blur" (ffmpeg avgblur)
    algorithm: str = Form("telea"),    # "telea" or "ns" — only used when mode=inpaint
):
    """
    Enqueue an erase job and return the job id immediately. The browser then
    polls GET /api/jobs/{id} for progress and GET /api/utilities/erase/{id}/download
    for the result. This avoids long-running synchronous HTTP requests that
    can drop ("Failed to fetch") on slow connections, sleeping tabs, or HMR.
    """
    if w <= 0 or h <= 0:
        raise HTTPException(400, "Region width and height must be greater than 0")

    content = await file.read()
    if len(content) > 500 * 1024 * 1024:
        raise HTTPException(413, "File too large. Maximum 500 MB.")
    if len(content) < 1000:
        raise HTTPException(400, "File appears to be empty or invalid.")

    # Reserve a job_id up front so we can lay out files under it.
    job_id = uuid.uuid4().hex[:12]
    workdir = _erase_workdir(job_id)
    workdir.mkdir(parents=True, exist_ok=True)

    suffix = Path(file.filename or "video.mp4").suffix.lower() or ".mp4"
    if suffix not in {".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi"}:
        suffix = ".mp4"
    input_path = workdir / f"input{suffix}"
    output_path = workdir / "output.mp4"
    input_path.write_bytes(content)

    stem = Path(file.filename or "video").stem
    out_filename = f"{stem}_erased.mp4"

    metadata = {
        "input_path": str(input_path),
        "output_path": str(output_path),
        "output_filename": out_filename,
        "x": x, "y": y, "w": w, "h": h,
        "mode": mode,
        "algorithm": algorithm,
    }

    # Use the queue's enqueue helper but pin our pre-chosen id by inserting
    # the row directly so the workdir name matches the job row.
    from database import async_session
    from models import JobModel, JobStatus, JobType
    import json as _json
    async with async_session() as session:
        row = JobModel(
            id=job_id,
            project_id=_ERASE_WORK_PROJECT_ID,
            type=JobType.erase.value,
            status=JobStatus.queued.value,
            metadata_json=_json.dumps(metadata),
        )
        session.add(row)
        await session.commit()

    logger.info(
        f"Erase job {job_id} enqueued mode={mode} algo={algorithm}: "
        f"region x={x} y={y} w={w} h={h}"
    )

    return {
        "job_id": job_id,
        "status": "queued",
        "output_filename": out_filename,
    }


@router.get("/erase/{job_id}/download")
async def download_erase_result(job_id: str):
    """Stream the finished erase output to the browser."""
    from database import async_session
    from models import JobModel, JobStatus
    import json as _json
    import asyncio as _asyncio

    async with async_session() as session:
        job = await session.get(JobModel, job_id)

    if not job:
        raise HTTPException(404, "Job not found")
    if job.type != "erase":
        raise HTTPException(400, "Not an erase job")
    if job.status != JobStatus.done.value:
        raise HTTPException(409, f"Job is not done (status={job.status})")

    meta = _json.loads(job.metadata_json or "{}")
    out = Path(meta.get("output_path", ""))
    if not out.exists():
        raise HTTPException(410, "Output file no longer available")

    filename = meta.get("output_filename") or out.name

    # Schedule a deferred cleanup of the workdir 5 minutes after the user
    # picks up the file, so re-download works briefly but disk doesn't grow.
    async def _delayed_cleanup():
        await _asyncio.sleep(300)
        try:
            workdir = _erase_workdir(job_id)
            for p in workdir.glob("*"):
                p.unlink(missing_ok=True)
            workdir.rmdir()
        except Exception:
            pass

    _asyncio.create_task(_delayed_cleanup())

    return FileResponse(
        path=str(out),
        media_type="video/mp4",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
