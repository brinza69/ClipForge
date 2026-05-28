"""
ClipForge — Captions Router (Caption Studio)

Endpoints powering the new Caption Studio surface:

  Templates
    GET    /api/captions/templates
    POST   /api/captions/templates           (create or update by id)
    GET    /api/captions/templates/{id}
    DELETE /api/captions/templates/{id}

  Fonts
    GET    /api/captions/fonts
    POST   /api/captions/fonts/upload        (multipart .ttf/.otf/.ttc)
    DELETE /api/captions/fonts/{filename}

  Preview + Burn
    POST   /api/captions/upload-source       (returns a session_id + dims)
    POST   /api/captions/preview-frame       (returns PNG)
    POST   /api/captions/burn                (enqueue burn-in job)
    GET    /api/captions/burn/{job_id}/download
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from config import settings
from database import async_session
from models import JobModel, JobStatus, JobType
from services import caption_overlays, caption_templates, font_manager

logger = logging.getLogger("clipforge.routers.captions")
router = APIRouter(prefix="/api/captions", tags=["captions"])


_CAPTION_SOURCE_PROJECT_ID = "__utility__"


def _session_dir(session_id: str) -> Path:
    return Path(settings.temp_dir) / "caption_sessions" / session_id


# ── Templates ──────────────────────────────────────────────────────────────


@router.get("/templates")
async def list_templates():
    return {"templates": caption_templates.list_templates()}


class TemplatePayload(BaseModel):
    id: str
    name: str
    font_family: str = "Arial Black"
    font_size: int = 64
    font_weight: str = "Bold"
    italic: bool = False
    text_color: str = "#FFFFFF"
    highlight_color: Optional[str] = None
    highlight_bg_color: Optional[str] = None
    outline_color: str = "#000000"
    outline_width: float = 4
    shadow_offset: float = 2
    shadow_color: str = "#00000080"
    position: str = "bottom"
    uppercase: bool = False
    animation: Optional[str] = "phrase"
    max_words_per_line: int = 3
    borderstyle: Optional[int] = None


@router.post("/templates")
async def save_template(payload: TemplatePayload):
    try:
        saved = caption_templates.save_template(payload.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return saved


@router.get("/templates/{template_id}")
async def get_template(template_id: str):
    t = caption_templates.get_template(template_id)
    if not t:
        raise HTTPException(404, f"Template not found: {template_id}")
    return t


@router.delete("/templates/{template_id}")
async def delete_template(template_id: str):
    try:
        caption_templates.delete_template(template_id)
    except FileNotFoundError:
        raise HTTPException(404, f"Template not found: {template_id}")
    except PermissionError as e:
        raise HTTPException(403, str(e))
    return {"ok": True}


# ── Fonts ──────────────────────────────────────────────────────────────────


@router.get("/fonts")
async def list_fonts():
    return font_manager.list_fonts()


@router.post("/fonts/upload")
async def upload_font(file: UploadFile = File(...)):
    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty upload")
    try:
        entry = font_manager.save_uploaded_font(file.filename or "", content)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return entry


@router.delete("/fonts/{filename}")
async def delete_font(filename: str):
    try:
        font_manager.delete_font(filename)
    except FileNotFoundError:
        raise HTTPException(404, f"Font not found: {filename}")
    return {"ok": True}


# ── Source upload (one-shot, kept around for preview + burn) ────────────────


@router.post("/upload-source")
async def upload_source(file: UploadFile = File(...)):
    """
    Accept the source video the user wants to add captions to. Returns a
    session_id; subsequent preview-frame and burn calls reference it instead
    of re-uploading the bytes every time.
    """
    suffix = Path(file.filename or "video").suffix.lower() or ".mp4"
    if suffix not in {".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi"}:
        raise HTTPException(400, f"Unsupported video format: {suffix}")
    content = await file.read()
    if len(content) > 500 * 1024 * 1024:
        raise HTTPException(413, "File too large. Maximum 500 MB.")
    if len(content) < 1000:
        raise HTTPException(400, "File appears to be empty.")

    session_id = uuid.uuid4().hex[:12]
    sdir = _session_dir(session_id)
    sdir.mkdir(parents=True, exist_ok=True)
    src = sdir / f"source{suffix}"
    src.write_bytes(content)

    try:
        w, h = caption_overlays.probe_video_dims(str(src))
    except Exception as e:
        raise HTTPException(422, f"Could not probe video: {e}")

    # Persist a tiny metadata file so subsequent calls can resolve paths
    # without us keeping in-memory session state.
    (sdir / "meta.json").write_text(json.dumps({
        "session_id": session_id,
        "filename": file.filename,
        "suffix": suffix,
        "width": w,
        "height": h,
    }), encoding="utf-8")

    logger.info(f"caption source uploaded: session={session_id} {w}x{h} {file.filename!r}")
    return {"session_id": session_id, "width": w, "height": h, "filename": file.filename}


def _resolve_source(session_id: str) -> Path:
    sdir = _session_dir(session_id)
    meta_path = sdir / "meta.json"
    if not meta_path.exists():
        raise HTTPException(404, f"Session not found: {session_id}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    src = sdir / f"source{meta['suffix']}"
    if not src.exists():
        raise HTTPException(410, f"Source file no longer available for {session_id}")
    return src


# ── Preview frame ───────────────────────────────────────────────────────────


class PreviewRequest(BaseModel):
    session_id: str
    time_s: float = 0.5
    overlays: List[dict] = Field(default_factory=list)


@router.post("/preview-frame")
async def preview_frame(req: PreviewRequest):
    src = _resolve_source(req.session_id)
    try:
        png = caption_overlays.render_preview_frame(
            str(src), req.overlays, time_s=req.time_s
        )
    except Exception as e:
        raise HTTPException(500, f"Preview render failed: {e}")
    return Response(content=png, media_type="image/png")


# ── Auto-transcribe ────────────────────────────────────────────────────────


class AutoTranscribeRequest(BaseModel):
    session_id: str
    template_id: str = "bold_impact"
    words_per_chunk: int = 4
    x_pct: float = 0.5
    y_pct: float = 0.85
    scale: float = 1.0
    language: Optional[str] = None  # whisper auto-detect when None


@router.post("/auto-transcribe")
async def auto_transcribe(req: AutoTranscribeRequest):
    """
    Run whisper on the uploaded source's audio, return a list of caption
    overlays with word-level timing ready to paste into the overlays list.

    Same alignment quality as the remix pipeline's forced-alignment path,
    but here we don't have a "ground truth" cleaned text to correct against —
    we trust whisper's transcription directly. Good enough for native-language
    captioning of a video you uploaded.
    """
    from services.caption_aligner import group_into_caption_chunks
    from services.transcriber import transcribe

    src = _resolve_source(req.session_id)
    try:
        result = await transcribe(str(src), language=req.language)
    except Exception as e:
        raise HTTPException(500, f"Transcription failed: {e}")

    # Flatten word stream from whisper's segments (mirrors caption_aligner).
    words: List[dict] = []
    for seg in result.get("segments", []):
        for w in seg.get("words") or []:
            text = (w.get("word") or "").strip()
            if not text:
                continue
            words.append({
                "word": text,
                "start": float(w.get("start") or 0.0),
                "end": float(w.get("end") or 0.0),
            })

    if not words:
        # No word-level data — fall back to segment-level chunks so the user
        # at least gets something to start editing from.
        for seg in result.get("segments", []):
            text = (seg.get("text") or "").strip()
            if text:
                words.append({
                    "word": text,
                    "start": float(seg.get("start") or 0.0),
                    "end": float(seg.get("end") or (seg.get("start") or 0.0) + 2.0),
                })

    chunks = group_into_caption_chunks(words, words_per_chunk=req.words_per_chunk)

    overlays = [{
        "text": c["text"],
        "template_id": req.template_id,
        "start_t": round(float(c["start"]), 3),
        "end_t": round(float(c["end"]), 3),
        "x_pct": float(req.x_pct),
        "y_pct": float(req.y_pct),
        "scale": float(req.scale),
    } for c in chunks]

    logger.info(
        f"auto-transcribe session={req.session_id}: "
        f"{len(words)} words → {len(overlays)} chunks "
        f"(lang={result.get('language')})"
    )
    return {
        "overlays": overlays,
        "word_count": len(words),
        "language": result.get("language"),
        "full_text": result.get("full_text"),
    }


# ── Burn-in export ──────────────────────────────────────────────────────────


class BurnRequest(BaseModel):
    session_id: str
    overlays: List[dict] = Field(default_factory=list)
    output_format: str = "mp4"


@router.post("/burn")
async def enqueue_burn(req: BurnRequest):
    src = _resolve_source(req.session_id)
    sdir = _session_dir(req.session_id)

    meta = json.loads((sdir / "meta.json").read_text(encoding="utf-8"))
    output_path = sdir / f"output.{req.output_format.lstrip('.').lower() or 'mp4'}"
    out_filename = (Path(meta.get("filename") or "video").stem + "_captioned.mp4")

    # Pre-bake the ASS file once; the worker just shells out to ffmpeg.
    ass_path = sdir / "overlays.ass"
    caption_overlays.build_overlays_ass(
        req.overlays, int(meta["width"]), int(meta["height"]), str(ass_path)
    )

    job_id = uuid.uuid4().hex[:12]
    payload = {
        "input_path": str(src),
        "output_path": str(output_path),
        "output_filename": out_filename,
        "ass_path": str(ass_path),
        "fonts_dir": str(font_manager.fonts_dir()),
        "session_id": req.session_id,
    }
    async with async_session() as session:
        row = JobModel(
            id=job_id,
            project_id=_CAPTION_SOURCE_PROJECT_ID,
            type=JobType.caption_burn.value,
            status=JobStatus.queued.value,
            metadata_json=json.dumps(payload),
        )
        session.add(row)
        await session.commit()

    logger.info(f"caption_burn {job_id} enqueued for session={req.session_id}")
    return {"job_id": job_id, "status": "queued", "output_filename": out_filename}


@router.get("/burn/{job_id}/download")
async def download_burn(job_id: str):
    async with async_session() as session:
        job = await session.get(JobModel, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.type != JobType.caption_burn.value:
        raise HTTPException(400, "Not a caption-burn job")
    if job.status != JobStatus.done.value:
        raise HTTPException(409, f"Job not done (status={job.status})")
    meta = json.loads(job.metadata_json or "{}")
    out = Path(meta.get("output_path", ""))
    if not out.exists():
        raise HTTPException(410, "Output no longer available")
    filename = meta.get("output_filename") or out.name
    return FileResponse(
        path=str(out),
        media_type="video/mp4",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
