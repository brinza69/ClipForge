"""
ClipForge — Remix Pipeline Router

Three endpoints powering the /remix UI:

  POST /api/remix/preview        — validate URL, return thumbnail + dims so
                                   the user can draw the erase + caption rects.
  POST /api/remix/start          — enqueue a remix_pipeline job. Returns job_id.
  GET  /api/remix/{job_id}/download  — stream the final captioned mp4.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from database import async_session
from models import JobModel, JobStatus, JobType, ProjectModel, ProjectStatus
from services.downloader import detect_source_type, fetch_metadata, validate_url

logger = logging.getLogger("clipforge.routers.remix")
router = APIRouter(prefix="/api/remix", tags=["remix"])


def _safe_filename(name: str) -> str:
    """Strip emojis / non-ASCII / unsafe chars for use in HTTP headers.
    uvicorn's header serializer uses latin-1; anything outside that range
    crashes the response."""
    import re
    cleaned = re.sub(r"[^A-Za-z0-9._\- ]+", "_", name or "video")
    cleaned = " ".join(cleaned.split()).strip(" ._-")
    return cleaned or "video"


class PreviewRequest(BaseModel):
    url: str


@router.post("/preview")
async def remix_preview(req: PreviewRequest):
    """Validate the URL and return enough info for the UI to render the two
    region-pickers on the thumbnail. Does NOT download the video."""
    check = await validate_url(req.url)
    if not check.get("valid"):
        raise HTTPException(400, check.get("error", "Invalid URL"))
    meta = await fetch_metadata(req.url, None)
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


class Zone(BaseModel):
    x: int
    y: int
    w: int = Field(gt=0)
    h: int = Field(gt=0)
    src_w: int = Field(gt=0)  # dims of the image the user clicked on
    src_h: int = Field(gt=0)


class StartRequest(BaseModel):
    url: str
    title: Optional[str] = None

    erase_zone: Zone
    caption_zone: Zone

    erase_mode: str = "inpaint"                  # inpaint | blur
    erase_algorithm: str = "telea"               # telea | ns  (only used when mode=inpaint)
    erase_auto_detect: bool = False              # if True, OCR the video and inpaint only the time-varying caption boxes
                                                  # (ignores erase_zone except as a clamp)

    transcript_engine: str = "ollama"            # ollama | openai | anthropic
    transcript_target_lang: Optional[str] = None  # "en" / "ro" / null = keep original

    tts_engine: str = "xtts"                     # xtts | elevenlabs | local_clone
    tts_voice_id: str
    tts_language: str = "en"
    tts_speed: float = 1.0                       # 0.7-1.2 for ElevenLabs, 0.5-2.0 for XTTS

    caption_template_id: str = "bold_impact"
    # Optional caption style overrides. Anything set here merges over the
    # template's default for every auto-generated overlay.
    caption_font_family: Optional[str] = None       # e.g. "Inter Black"
    caption_scale: float = 1.0                       # multiplier on font_size
    caption_text_color: Optional[str] = None        # "#RRGGBB" or "#RRGGBBAA"
    caption_outline_color: Optional[str] = None
    caption_outline_width: Optional[float] = None
    caption_uppercase: Optional[bool] = None
    caption_italic: Optional[bool] = None
    # 1 = one word per chunk (TikTok style), higher = grouped phrases.
    caption_words_per_chunk: int = 1
    # Strip .,!?;:'"()[] etc from each chunk for cleaner look.
    caption_strip_punct: bool = True

    # Commentator overlay — runs AFTER caption burn. null = no commentator.
    commentator_preset_id: Optional[str] = None
    # Per-run chroma key override (null = use preset's saved value).
    # `commentator_chroma_color` can be "" (empty string) to explicitly
    # disable chroma keying for this run only.
    commentator_chroma_color: Optional[str] = None
    commentator_chroma_similarity: Optional[float] = None
    commentator_chroma_blend: Optional[float] = None


@router.post("/start")
async def remix_start(req: StartRequest):
    """Create the project, enqueue the remix_pipeline job."""
    check = await validate_url(req.url)
    if not check.get("valid"):
        raise HTTPException(400, check.get("error", "Invalid URL"))

    # Pull metadata so we can carry width/height/title onto the project row.
    metdat = await fetch_metadata(req.url, None)
    if "error" in metdat:
        raise HTTPException(422, metdat["error"])

    async with async_session() as session:
        project = ProjectModel(
            title=req.title or metdat.get("title") or "Remix",
            source_url=req.url,
            source_type=detect_source_type(req.url),
            status=ProjectStatus.metadata_ready.value,
        )
        for f in ("duration", "width", "height", "fps", "thumbnail_url"):
            if metdat.get(f) is not None:
                setattr(project, f, metdat[f])
        session.add(project)
        await session.commit()
        await session.refresh(project)
        project_id = project.id

    job_id = uuid.uuid4().hex[:12]
    job_meta = {
        "url": req.url,
        "title": req.title or metdat.get("title"),
        "erase_zone": req.erase_zone.model_dump(),
        "caption_zone": req.caption_zone.model_dump(),
        "erase_mode": req.erase_mode,
        "erase_algorithm": req.erase_algorithm,
        "erase_auto_detect": req.erase_auto_detect,
        "transcript_engine": req.transcript_engine,
        "transcript_target_lang": req.transcript_target_lang,
        "tts_engine": req.tts_engine,
        "tts_voice_id": req.tts_voice_id,
        "tts_language": req.tts_language,
        "tts_speed": req.tts_speed,
        "caption_template_id": req.caption_template_id,
        "caption_font_family": req.caption_font_family,
        "caption_scale": req.caption_scale,
        "caption_text_color": req.caption_text_color,
        "caption_outline_color": req.caption_outline_color,
        "caption_outline_width": req.caption_outline_width,
        "caption_uppercase": req.caption_uppercase,
        "caption_italic": req.caption_italic,
        "caption_words_per_chunk": req.caption_words_per_chunk,
        "caption_strip_punct": req.caption_strip_punct,
        "commentator_preset_id": req.commentator_preset_id,
        "commentator_chroma_color": req.commentator_chroma_color,
        "commentator_chroma_similarity": req.commentator_chroma_similarity,
        "commentator_chroma_blend": req.commentator_chroma_blend,
    }

    async with async_session() as session:
        row = JobModel(
            id=job_id,
            project_id=project_id,
            type=JobType.remix_pipeline.value,
            status=JobStatus.queued.value,
            metadata_json=json.dumps(job_meta),
        )
        session.add(row)
        await session.commit()

    logger.info(f"remix_pipeline {job_id} enqueued for project {project_id}")
    return {"job_id": job_id, "project_id": project_id}


@router.get("/recent")
async def remix_recent(limit: int = 10, offset: int = 0):
    """
    Return completed remix_pipeline jobs, newest first, with enough metadata
    to show download buttons. Paginated: `limit` (1–50) + `offset`. Also
    returns `total` so the UI can render "showing X–Y of Z".
    """
    from sqlalchemy import select, desc, func

    lim = max(1, min(50, int(limit)))
    off = max(0, int(offset))

    async with async_session() as session:
        total = (
            await session.execute(
                select(func.count())
                .select_from(JobModel)
                .where(JobModel.type == JobType.remix_pipeline.value)
                .where(JobModel.status == JobStatus.done.value)
            )
        ).scalar() or 0
        rows = (
            await session.execute(
                select(JobModel)
                .where(JobModel.type == JobType.remix_pipeline.value)
                .where(JobModel.status == JobStatus.done.value)
                .order_by(desc(JobModel.updated_at))
                .limit(lim)
                .offset(off)
            )
        ).scalars().all()

    out: list[dict] = []
    for row in rows:
        meta = {}
        try:
            meta = json.loads(row.metadata_json or "{}")
        except Exception:
            pass
        final_path = meta.get("final_path", "")
        size_bytes = 0
        exists = False
        if final_path:
            try:
                p = Path(final_path)
                if p.exists():
                    exists = True
                    size_bytes = p.stat().st_size
            except Exception:
                pass
        out.append({
            "job_id": row.id,
            "project_id": row.project_id,
            "title": meta.get("title") or "Remix",
            "url": meta.get("url"),
            "output_filename": meta.get("output_filename") or f"remix-{row.id}.mp4",
            "file_size": size_bytes,
            "file_available": exists,
            "finished_at": row.updated_at.isoformat() if row.updated_at else None,
            "tts_engine": meta.get("tts_engine"),
            "transcript_target_lang": meta.get("transcript_target_lang"),
        })
    return {"runs": out, "count": len(out), "total": int(total), "offset": off, "limit": lim}


@router.get("/{job_id}/result")
async def remix_result(job_id: str):
    """Return the per-stage stats once the job is done."""
    async with async_session() as session:
        job = await session.get(JobModel, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.type != JobType.remix_pipeline.value:
        raise HTTPException(400, "Not a remix job")
    if job.status != JobStatus.done.value:
        raise HTTPException(409, f"Job not done (status={job.status})")
    meta = json.loads(job.metadata_json or "{}")
    return {
        "job_id": job_id,
        "project_id": job.project_id,
        "output_filename": meta.get("output_filename"),
        "cleaned_text": meta.get("cleaned_text"),
        "speed_match_stats": meta.get("speed_match_stats"),
        "descriptions": meta.get("descriptions") or {
            "original_translated": "",
            "ai_generated": "",
        },
    }


@router.get("/{job_id}/download")
async def remix_download(job_id: str):
    """Stream the finished remix mp4."""
    async with async_session() as session:
        job = await session.get(JobModel, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.type != JobType.remix_pipeline.value:
        raise HTTPException(400, "Not a remix job")
    if job.status != JobStatus.done.value:
        raise HTTPException(409, f"Job not done (status={job.status})")
    meta = json.loads(job.metadata_json or "{}")
    out = Path(meta.get("final_path", ""))
    if not out.exists():
        raise HTTPException(410, "Final video no longer available")
    raw_name = meta.get("output_filename") or out.name
    safe = _safe_filename(Path(raw_name).stem) + (Path(raw_name).suffix or ".mp4")
    return FileResponse(
        path=str(out),
        media_type="video/mp4",
        filename=safe,
        headers={"Content-Disposition": f'attachment; filename="{safe}"'},
    )


@router.delete("/{job_id}")
async def remix_delete(job_id: str):
    """Delete a finished remix run: its media files + the job row.

    Removes data/media/<project_id> (the final video + scratch) and the
    JobModel row so it disappears from the Past Runs list. Returns the
    number of bytes freed."""
    async with async_session() as session:
        job = await session.get(JobModel, job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        if job.type != JobType.remix_pipeline.value:
            raise HTTPException(400, "Not a remix job")
        project_id = job.project_id

        # Free the on-disk workspace (final video lives under media/<project>).
        freed = 0
        if project_id:
            try:
                from services.cleanup import cleanup_job_workspace
                import asyncio
                loop = asyncio.get_event_loop()
                stats = await loop.run_in_executor(
                    None, lambda: cleanup_job_workspace(project_id)
                )
                freed = stats.get("freed_bytes", 0)
            except Exception:
                logger.exception(f"could not clean media for {job_id}")

        await session.delete(job)
        await session.commit()

    logger.info(f"remix run {job_id} deleted (freed {freed} bytes)")
    return {"ok": True, "job_id": job_id, "freed_bytes": freed}
