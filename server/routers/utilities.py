"""
ClipForge — Utilities Router
Quick-download: paste a URL and kick off the full pipeline immediately.
Caption Eraser: upload a video and blur/erase a rectangular region using FFmpeg.
"""

import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import async_session, get_session
from models import JobModel, JobStatus, JobType, ProjectModel, ProjectStatus
from services.downloader import validate_url, detect_source_type, fetch_metadata
from job_queue import job_queue

logger = logging.getLogger("clipforge.routers.utilities")
router = APIRouter(prefix="/api/utilities", tags=["utilities"])


_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._\- ]+")


def _safe_filename(base: str, suffix: str = "") -> str:
    """
    Build a Content-Disposition-safe filename. Strips newlines, hashtags,
    non-ASCII glyphs etc. that would crash uvicorn's header serializer
    ("Invalid HTTP header value.").
    """
    cleaned = _FILENAME_SAFE_RE.sub("_", (base or "video"))
    cleaned = " ".join(cleaned.split())  # collapse whitespace runs (incl. newlines)
    cleaned = cleaned.strip(" ._-") or "video"
    return f"{cleaned}{suffix}"


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
    mode: str = Form("inpaint"),       # "inpaint" (LaMa GPU / OpenCV TELEA) or "blur" (ffmpeg avgblur)
    algorithm: str = Form("telea"),    # "telea" or "ns" — only used when mode=inpaint
    auto_detect: bool = Form(False),   # if true, OCR-detects captions and ignores x/y/w/h
):
    """
    Enqueue an erase job and return the job id immediately. The browser then
    polls GET /api/jobs/{id} for progress and GET /api/utilities/erase/{id}/download
    for the result.

    If `auto_detect=True`, the worker scans the video with OCR, clusters
    detected captions into time-varying segments (caption zones that follow
    the captions even when they move during the clip), and inpaints each
    only during the frames it appears in.
    """
    if auto_detect and mode == "blur":
        raise HTTPException(400, "Auto-detect only supports inpaint mode")
    if not auto_detect and (w <= 0 or h <= 0):
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
        "auto_detect": auto_detect,
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


# ─────────────────────────────────────────────────────────────────────────────
# Silence Remover — strip silence from an audio or video file.
# Algorithm matches the NeuralFalcon HF Space; see services/silence_remover.py.
# ─────────────────────────────────────────────────────────────────────────────


def _silence_workdir(job_id: str) -> Path:
    return Path(settings.temp_dir) / "silence" / job_id


_SILENCE_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"}
_SILENCE_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi"}


@router.post("/silence-remove")
async def silence_remove(
    file: UploadFile = File(...),
    min_silence_ms: int = Form(100),
    silence_thresh_db: float = Form(-45.0),
    keep_silence_ms: int = Form(50),
    output_format: Optional[str] = Form(None),  # for audio: mp3/wav/etc. None = keep input ext.
):
    """
    Enqueue a silence-removal job. Audio inputs preserve format (or convert
    to `output_format` if given); video inputs always output mp4.
    Poll GET /api/jobs/{id} for progress, then GET /api/utilities/silence-remove/{id}/download.
    """
    suffix = Path(file.filename or "input").suffix.lower()
    if suffix not in _SILENCE_AUDIO_EXTS and suffix not in _SILENCE_VIDEO_EXTS:
        raise HTTPException(
            400,
            f"Unsupported file type {suffix or '(none)'}. "
            f"Audio: {sorted(_SILENCE_AUDIO_EXTS)}  Video: {sorted(_SILENCE_VIDEO_EXTS)}",
        )

    content = await file.read()
    if len(content) > 500 * 1024 * 1024:
        raise HTTPException(413, "File too large. Maximum 500 MB.")
    if len(content) < 100:
        raise HTTPException(400, "File appears to be empty.")

    if min_silence_ms < 20:
        raise HTTPException(400, "min_silence_ms must be ≥ 20")
    if keep_silence_ms < 0:
        raise HTTPException(400, "keep_silence_ms must be ≥ 0")
    if not (-80.0 <= silence_thresh_db <= 0.0):
        raise HTTPException(400, "silence_thresh_db must be in [-80, 0]")

    is_video = suffix in _SILENCE_VIDEO_EXTS
    mode = "video" if is_video else "audio"

    job_id = uuid.uuid4().hex[:12]
    workdir = _silence_workdir(job_id)
    workdir.mkdir(parents=True, exist_ok=True)

    input_path = workdir / f"input{suffix}"
    input_path.write_bytes(content)

    if is_video:
        out_suffix = ".mp4"
    else:
        out_suffix = f".{output_format.lower().lstrip('.')}" if output_format else suffix
        if out_suffix not in _SILENCE_AUDIO_EXTS:
            raise HTTPException(400, f"Unsupported output_format {output_format!r}")

    stem = Path(file.filename or "audio").stem
    out_filename = _safe_filename(stem, suffix=f"_nosilence{out_suffix}")
    output_path = workdir / f"output{out_suffix}"

    metadata = {
        "input_path": str(input_path),
        "output_path": str(output_path),
        "output_filename": out_filename,
        "mode": mode,
        "min_silence_ms": min_silence_ms,
        "silence_thresh_db": silence_thresh_db,
        "keep_silence_ms": keep_silence_ms,
    }

    async with async_session() as session:
        row = JobModel(
            id=job_id,
            project_id=_ERASE_WORK_PROJECT_ID,
            type=JobType.silence_remove.value,
            status=JobStatus.queued.value,
            metadata_json=json.dumps(metadata),
        )
        session.add(row)
        await session.commit()

    logger.info(
        f"silence-remove {job_id} enqueued: mode={mode} suffix={suffix} "
        f"thresh={silence_thresh_db}dB keep={keep_silence_ms}ms min={min_silence_ms}ms"
    )
    return {"job_id": job_id, "status": "queued", "output_filename": out_filename}


@router.get("/silence-remove/{job_id}/download")
async def download_silence_remove_result(job_id: str):
    """Stream the finished silence-removed output."""
    import asyncio as _asyncio

    async with async_session() as session:
        job = await session.get(JobModel, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.type != JobType.silence_remove.value:
        raise HTTPException(400, "Not a silence-remove job")
    if job.status != JobStatus.done.value:
        raise HTTPException(409, f"Job is not done (status={job.status})")

    meta = json.loads(job.metadata_json or "{}")
    out = Path(meta.get("output_path", ""))
    if not out.exists():
        raise HTTPException(410, "Output file no longer available")

    filename = meta.get("output_filename") or out.name
    mode = meta.get("mode", "audio")
    media_type = "video/mp4" if mode == "video" else "audio/mpeg"
    if out.suffix.lower() == ".wav":
        media_type = "audio/wav"
    elif out.suffix.lower() == ".flac":
        media_type = "audio/flac"
    elif out.suffix.lower() == ".ogg":
        media_type = "audio/ogg"

    async def _delayed_cleanup():
        await _asyncio.sleep(300)
        try:
            wd = _silence_workdir(job_id)
            for p in wd.glob("*"):
                p.unlink(missing_ok=True)
            wd.rmdir()
        except Exception:
            pass

    _asyncio.create_task(_delayed_cleanup())

    return FileResponse(
        path=str(out),
        media_type=media_type,
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/silence-remove/{job_id}/result")
async def silence_remove_result(job_id: str):
    """Return job stats (before/after duration etc.) once the job is done."""
    async with async_session() as session:
        job = await session.get(JobModel, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.type != JobType.silence_remove.value:
        raise HTTPException(400, "Not a silence-remove job")
    if job.status != JobStatus.done.value:
        raise HTTPException(409, f"Job is not done (status={job.status})")
    meta = json.loads(job.metadata_json or "{}")
    return {
        "job_id": job_id,
        "stats": meta.get("stats") or {},
        "output_filename": meta.get("output_filename"),
        "mode": meta.get("mode"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Batch processing: many URLs, shared erase rectangle.
# ─────────────────────────────────────────────────────────────────────────────

class BatchPreviewRequest(BaseModel):
    url: str


@router.post("/batch/preview")
async def batch_preview(data: BatchPreviewRequest):
    """
    Return enough metadata about a URL for the frontend to render the region
    picker, without creating a project or downloading the video. The frontend
    draws the rectangle on the thumbnail and we scale it later when erasing
    each item.
    """
    check = await validate_url(data.url)
    if not check.get("valid"):
        raise HTTPException(400, check.get("error", "Invalid URL"))
    meta = await fetch_metadata(data.url, None)
    if "error" in meta:
        raise HTTPException(422, meta["error"])
    return {
        "title": meta.get("title"),
        "thumbnail_url": meta.get("thumbnail_url"),
        "width": meta.get("width"),
        "height": meta.get("height"),
        "duration": meta.get("duration"),
        "channel_name": meta.get("channel_name"),
    }


class _Region(BaseModel):
    x: int
    y: int
    w: int = Field(gt=0)
    h: int = Field(gt=0)


class _SourceDims(BaseModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class BatchSubmitRequest(BaseModel):
    urls: List[str]
    mode: str = "inpaint"           # "inpaint" or "blur"
    algorithm: str = "telea"        # only used when mode=inpaint
    region: _Region                 # in pixel coordinates of the *first* video
    source_dimensions: _SourceDims  # dimensions of the first video


@router.post("/batch")
async def batch_submit(
    data: BatchSubmitRequest,
    session: AsyncSession = Depends(get_session),
):
    """
    Submit N URLs as a batch. For each:
      1. Fetch metadata
      2. Create a ProjectModel pre-loaded with erase_params and a shared batch_id
      3. Enqueue full_pipeline (which will run erase_project at the end)

    The erase rectangle is captured once (in video 1's coordinate space) and
    scaled per-video by handle_erase_project when target dims differ.
    """
    if not data.urls:
        raise HTTPException(400, "Provide at least one URL.")
    if data.mode not in ("inpaint", "blur"):
        raise HTTPException(400, "mode must be 'inpaint' or 'blur'.")

    batch_id = uuid.uuid4().hex[:12]
    erase_params = {
        "region": data.region.model_dump(),
        "mode": data.mode,
        "algorithm": data.algorithm,
        "source_dimensions": data.source_dimensions.model_dump(),
    }

    created: List[dict] = []
    for idx, raw_url in enumerate(data.urls, start=1):
        url = raw_url.strip()
        if not url:
            continue
        check = await validate_url(url)
        if not check.get("valid"):
            created.append({
                "index": idx,
                "url": url,
                "project_id": None,
                "error": check.get("error", "invalid URL"),
            })
            continue

        # Probe metadata cheaply (no download yet). Skip on failure but record it.
        meta = await fetch_metadata(url, None)
        if "error" in meta:
            created.append({
                "index": idx,
                "url": url,
                "project_id": None,
                "error": meta["error"],
            })
            continue

        project = ProjectModel(
            title=meta.get("title") or f"Batch #{idx}",
            source_url=url,
            source_type=detect_source_type(url),
            status=ProjectStatus.metadata_ready.value,
            channel_name=meta.get("channel_name"),
            duration=meta.get("duration"),
            width=meta.get("width"),
            height=meta.get("height"),
            fps=meta.get("fps"),
            thumbnail_url=meta.get("thumbnail_url"),
            estimated_size=meta.get("estimated_size"),
            upload_date=meta.get("upload_date"),
            description=meta.get("description"),
            webpage_url=meta.get("webpage_url"),
            extractor=meta.get("extractor"),
            is_live=meta.get("is_live"),
            was_live=meta.get("was_live"),
            availability=meta.get("availability"),
            batch_id=batch_id,
            batch_index=idx,
            erase_params=erase_params,
        )
        session.add(project)
        await session.commit()
        await session.refresh(project)

        job_id = await job_queue.enqueue(
            project_id=project.id,
            job_type=JobType.full_pipeline.value,
        )
        created.append({
            "index": idx,
            "url": url,
            "project_id": project.id,
            "job_id": job_id,
        })

    logger.info(
        f"Batch {batch_id} submitted: {len([c for c in created if c.get('project_id')])} projects "
        f"({len(created) - len([c for c in created if c.get('project_id')])} rejected)"
    )

    return {
        "batch_id": batch_id,
        "mode": data.mode,
        "region": data.region.model_dump(),
        "source_dimensions": data.source_dimensions.model_dump(),
        "items": created,
    }


@router.get("/batch/{batch_id}")
async def batch_status(batch_id: str, session: AsyncSession = Depends(get_session)):
    """Aggregate status of every project in a batch (latest job + erase output)."""
    result = await session.execute(
        select(ProjectModel)
        .where(ProjectModel.batch_id == batch_id)
        .order_by(ProjectModel.batch_index)
    )
    projects = result.scalars().all()
    if not projects:
        raise HTTPException(404, "Batch not found")

    items = []
    for p in projects:
        # Latest job for this project (any type). Most recent updated_at.
        latest = await session.execute(
            select(JobModel)
            .where(JobModel.project_id == p.id)
            .order_by(JobModel.created_at.desc())
            .limit(1)
        )
        job = latest.scalar_one_or_none()

        items.append({
            "index": p.batch_index,
            "project_id": p.id,
            "title": p.title,
            "status": p.status,
            "width": p.width,
            "height": p.height,
            "duration": p.duration,
            "thumbnail_url": p.thumbnail_url,
            "job_id": job.id if job else None,
            "job_type": job.type if job else None,
            "job_status": job.status if job else None,
            "progress": (job.progress if job else 0.0) or 0.0,
            "progress_message": (job.progress_message if job else "") or "",
            "job_error": job.error if job else None,
            "has_erased_video": bool(p.erased_video_path) and Path(p.erased_video_path).exists() if p.erased_video_path else False,
            "transcript_available": False,  # filled below
        })

    # Fast pass to flag transcript availability without N+1 fetches of full segments.
    from models import TranscriptModel
    for it in items:
        tr = await session.execute(
            select(TranscriptModel.id).where(TranscriptModel.project_id == it["project_id"]).limit(1)
        )
        it["transcript_available"] = tr.scalar_one_or_none() is not None

    done = sum(1 for it in items if it["status"] in ("ready", "transcribed") and it["has_erased_video"])
    failed = sum(1 for it in items if it["status"] == "failed")
    return {
        "batch_id": batch_id,
        "total": len(items),
        "done": done,
        "failed": failed,
        "items": items,
    }


@router.get("/batch/{batch_id}/items/{project_id}/erased")
async def batch_download_erased(batch_id: str, project_id: str, session: AsyncSession = Depends(get_session)):
    """Download the erased mp4 for one item in a batch."""
    project = await session.get(ProjectModel, project_id)
    if not project or project.batch_id != batch_id:
        raise HTTPException(404, "Item not found in this batch")
    if not project.erased_video_path or not Path(project.erased_video_path).exists():
        raise HTTPException(409, "Erased video not ready yet.")
    filename = _safe_filename((project.title or "video")[:50], suffix="_erased.mp4")
    return FileResponse(
        path=project.erased_video_path,
        media_type="video/mp4",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/batch/{batch_id}/items/{project_id}/transcript")
async def batch_get_transcript(batch_id: str, project_id: str, session: AsyncSession = Depends(get_session)):
    """Return the transcript for one item in a batch (segments + full text)."""
    from models import TranscriptModel
    project = await session.get(ProjectModel, project_id)
    if not project or project.batch_id != batch_id:
        raise HTTPException(404, "Item not found in this batch")
    result = await session.execute(
        select(TranscriptModel).where(TranscriptModel.project_id == project_id).limit(1)
    )
    tr = result.scalar_one_or_none()
    if not tr:
        raise HTTPException(404, "Transcript not ready yet.")
    return {
        "project_id": project_id,
        "language": tr.language,
        "full_text": tr.full_text,
        "word_count": tr.word_count,
        "segments": tr.segments,
    }
