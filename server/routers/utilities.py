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
