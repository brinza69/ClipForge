"""
ClipForge — Parallel Processing Router

Endpoints powering the /parallel UI. One source link → N output videos
that share download/transcribe/erase/clean-transcript and differ only in
voice, captions and commentator.

  POST /api/parallel/start             — enqueue a parallel_pipeline job.
  GET  /api/parallel/{job}/result      — per-variant results once done.
  GET  /api/parallel/{job}/download/{i} — stream variant i's final mp4.
  GET  /api/parallel/recent            — last N completed parallel runs.

Preview / thumbnail reuses the existing POST /api/remix/preview.
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
from routers.remix import Zone, _safe_filename
from services.downloader import detect_source_type, fetch_metadata, validate_url

logger = logging.getLogger("clipforge.routers.parallel")
router = APIRouter(prefix="/api/parallel", tags=["parallel"])


class VariantConfig(BaseModel):
    """One output video's per-variant settings. Voice + captions + commentator."""
    name: Optional[str] = None  # label shown in results ("Grinch", "Narrator", …)

    tts_engine: str = "xtts"
    tts_voice_id: str
    tts_language: str = "en"
    tts_speed: float = 1.0

    caption_template_id: str = "bold_impact"
    caption_font_family: Optional[str] = None
    caption_scale: float = 1.0
    caption_text_color: Optional[str] = None
    caption_outline_color: Optional[str] = None
    caption_outline_width: Optional[float] = None
    caption_uppercase: Optional[bool] = None
    caption_italic: Optional[bool] = None
    caption_words_per_chunk: int = 1
    caption_strip_punct: bool = True

    commentator_preset_id: Optional[str] = None
    commentator_chroma_color: Optional[str] = None
    commentator_chroma_similarity: Optional[float] = None
    commentator_chroma_blend: Optional[float] = None

    # Optional Google Drive folder link — when set, the finished video is
    # uploaded there in addition to staying downloadable.
    drive_folder: Optional[str] = None

    # Split the finished video into equal parts for multi-part posting.
    split_into_parts: bool = False

    # Fit the voice to the SOURCE video duration (atempo) so the output keeps
    # the original length instead of time-stretching the video to the voice.
    match_to_source_duration: bool = False


class StartRequest(BaseModel):
    url: str
    title: Optional[str] = None

    # Shared across all variants.
    erase_zone: Zone
    caption_zone: Zone
    erase_mode: str = "inpaint"
    erase_algorithm: str = "telea"
    erase_auto_detect: bool = False
    erase_coverage: str = "tight"   # tight | band | thorough (T20)
    transcript_engine: str = "ollama"
    transcript_target_lang: Optional[str] = None

    variants: List[VariantConfig] = Field(min_length=2, max_length=4)

    # Optional Sheets integration — when set, the job is tied to a Sheets row.
    # The pipeline overrides the output filename to use `sheets_number` and,
    # after variant #0 succeeds, writes the AI-generated description back into
    # the row's description column (and advances next_row).
    sheets_row: Optional[int] = None
    sheets_number: Optional[str] = None


@router.post("/start")
async def parallel_start(req: StartRequest):
    """Create the project, enqueue the parallel_pipeline job."""
    check = await validate_url(req.url)
    if not check.get("valid"):
        raise HTTPException(400, check.get("error", "Invalid URL"))

    metdat = await fetch_metadata(req.url, None)
    if "error" in metdat:
        raise HTTPException(422, metdat["error"])

    async with async_session() as session:
        project = ProjectModel(
            title=req.title or metdat.get("title") or "Parallel",
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
        "erase_coverage": req.erase_coverage,
        "transcript_engine": req.transcript_engine,
        "transcript_target_lang": req.transcript_target_lang,
        "variants": [v.model_dump() for v in req.variants],
        "sheets_row": req.sheets_row,
        "sheets_number": (req.sheets_number or "").strip() or None,
    }

    async with async_session() as session:
        row = JobModel(
            id=job_id,
            project_id=project_id,
            type=JobType.parallel_pipeline.value,
            status=JobStatus.queued.value,
            metadata_json=json.dumps(job_meta),
        )
        session.add(row)
        await session.commit()

    logger.info(
        f"parallel_pipeline {job_id} enqueued for project {project_id} "
        f"({len(req.variants)} variants)"
    )
    return {"job_id": job_id, "project_id": project_id}


def _variant_view(r: dict) -> dict:
    """Shape one stored result for the API, including file availability."""
    fp = r.get("final_path", "")
    exists, size = False, 0
    if fp:
        try:
            p = Path(fp)
            if p.exists():
                exists, size = True, p.stat().st_size
        except Exception:
            pass
    return {
        "index": r.get("index"),
        "name": r.get("name"),
        "label": r.get("label"),
        "commentator_preset_id": r.get("commentator_preset_id"),
        "tts_engine": r.get("tts_engine"),
        "caption_template_id": r.get("caption_template_id"),
        "output_filename": r.get("output_filename"),
        "file_size": size,
        "file_available": exists,
        "drive": r.get("drive"),
        "parts": [
            {
                "part": p.get("part"), "of": p.get("of"),
                "filename": p.get("filename"),
                "start": p.get("start"), "duration": p.get("duration"),
                "available": bool(p.get("path") and Path(p["path"]).exists()),
            }
            for p in (r.get("parts") or [])
        ],
    }


@router.get("/{job_id}/result")
async def parallel_result(job_id: str):
    """Return per-variant results once the job is done."""
    async with async_session() as session:
        job = await session.get(JobModel, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.type != JobType.parallel_pipeline.value:
        raise HTTPException(400, "Not a parallel job")
    if job.status != JobStatus.done.value:
        raise HTTPException(409, f"Job not done (status={job.status})")
    meta = json.loads(job.metadata_json or "{}")
    results = meta.get("results") or []
    return {
        "job_id": job_id,
        "project_id": job.project_id,
        "title": meta.get("title"),
        "descriptions": meta.get("descriptions") or {
            "original_translated": "",
            "ai_generated": "",
        },
        "variants": [_variant_view(r) for r in results],
        "sheets_commit": meta.get("sheets_commit"),
        "sheets_row": meta.get("sheets_row"),
        "sheets_number": meta.get("sheets_number"),
        "cleaned_text": meta.get("cleaned_text") or "",
        "transcript_text": meta.get("transcript_text") or "",
    }


@router.get("/{job_id}/download/{index}")
async def parallel_download(job_id: str, index: int):
    """Stream the finished mp4 for variant `index`."""
    async with async_session() as session:
        job = await session.get(JobModel, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.type != JobType.parallel_pipeline.value:
        raise HTTPException(400, "Not a parallel job")
    if job.status != JobStatus.done.value:
        raise HTTPException(409, f"Job not done (status={job.status})")
    meta = json.loads(job.metadata_json or "{}")
    results = meta.get("results") or []
    match = next((r for r in results if int(r.get("index", -1)) == index), None)
    if not match:
        raise HTTPException(404, f"Variant {index} not found")
    out = Path(match.get("final_path", ""))
    if not out.exists():
        raise HTTPException(410, "Variant video no longer available")
    raw_name = match.get("output_filename") or out.name
    safe = _safe_filename(Path(raw_name).stem) + (Path(raw_name).suffix or ".mp4")
    return FileResponse(
        path=str(out),
        media_type="video/mp4",
        filename=safe,
        headers={"Content-Disposition": f'attachment; filename="{safe}"'},
    )


@router.get("/{job_id}/download/{index}/part/{part}")
async def parallel_download_part(job_id: str, index: int, part: int):
    """Stream part `part` (1-based) of variant `index`."""
    async with async_session() as session:
        job = await session.get(JobModel, job_id)
    if not job or job.type != JobType.parallel_pipeline.value:
        raise HTTPException(404, "Job not found")
    if job.status != JobStatus.done.value:
        raise HTTPException(409, f"Job not done (status={job.status})")
    meta = json.loads(job.metadata_json or "{}")
    match = next((r for r in (meta.get("results") or [])
                  if int(r.get("index", -1)) == index), None)
    if not match:
        raise HTTPException(404, f"Variant {index} not found")
    pmatch = next((p for p in (match.get("parts") or [])
                   if int(p.get("part", -1)) == part), None)
    if not pmatch:
        raise HTTPException(404, f"Part {part} not found")
    out = Path(pmatch.get("path", ""))
    if not out.exists():
        raise HTTPException(410, "Part no longer available")
    safe = _safe_filename(out.stem) + ".mp4"
    return FileResponse(
        path=str(out), media_type="video/mp4", filename=safe,
        headers={"Content-Disposition": f'attachment; filename="{safe}"'},
    )


@router.get("/recent")
async def parallel_recent(limit: int = 10):
    """Return the last `limit` completed parallel runs, newest first."""
    from sqlalchemy import select, desc

    async with async_session() as session:
        rows = (
            await session.execute(
                select(JobModel)
                .where(JobModel.type == JobType.parallel_pipeline.value)
                .where(JobModel.status == JobStatus.done.value)
                .order_by(desc(JobModel.updated_at))
                .limit(max(1, min(50, int(limit))))
            )
        ).scalars().all()

    out: list[dict] = []
    for row in rows:
        try:
            meta = json.loads(row.metadata_json or "{}")
        except Exception:
            meta = {}
        results = meta.get("results") or []
        out.append({
            "job_id": row.id,
            "project_id": row.project_id,
            "title": meta.get("title") or "Parallel",
            "url": meta.get("url"),
            "variant_count": len(results),
            "variants": [_variant_view(r) for r in results],
            "finished_at": row.updated_at.isoformat() if row.updated_at else None,
        })
    return {"runs": out, "count": len(out)}
