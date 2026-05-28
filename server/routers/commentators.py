"""
ClipForge — Commentators Router

Endpoints:
  GET    /api/commentators                   — list presets (with thumb URLs)
  POST   /api/commentators                   — multipart upload, creates a preset
  GET    /api/commentators/{id}/video        — stream the raw clip
  GET    /api/commentators/{id}/thumb        — first-frame jpg
  DELETE /api/commentators/{id}
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import json
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from database import async_session
from models import JobModel, JobStatus, JobType
from services import commentators

logger = logging.getLogger("clipforge.routers.commentators")
router = APIRouter(prefix="/api/commentators", tags=["commentators"])


@router.get("")
async def list_all():
    return {"commentators": commentators.list_presets()}


@router.post("")
async def create(
    name: str = Form(...),
    file: UploadFile = File(...),
    default_position: str = Form("bottom-left"),
    default_scale: float = Form(0.30),
    chroma_key: Optional[str] = Form(None),         # "#00FF00" or "" for none
    chroma_similarity: float = Form(0.10),
    chroma_blend: float = Form(0.05),
    preset_id: Optional[str] = Form(None),
):
    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty upload")
    try:
        return commentators.save_preset(
            name=name,
            video_bytes=content,
            video_filename=file.filename or "video.mp4",
            default_position=default_position,
            default_scale=default_scale,
            chroma_key=(chroma_key.strip() if chroma_key and chroma_key.strip() else None),
            chroma_similarity=chroma_similarity,
            chroma_blend=chroma_blend,
            preset_id=preset_id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/{preset_id}/video")
async def get_video(preset_id: str):
    p = commentators._video_path(preset_id)
    if not p.exists():
        raise HTTPException(404, "Video missing for this preset")
    return FileResponse(path=str(p), media_type="video/mp4", filename=p.name)


@router.get("/{preset_id}/thumb")
async def get_thumb(preset_id: str):
    p = commentators._thumb_path(preset_id)
    if not p.exists():
        raise HTTPException(404, "Thumb not available")
    return FileResponse(path=str(p), media_type="image/jpeg")


@router.delete("/{preset_id}")
async def delete(preset_id: str):
    try:
        commentators.delete_preset(preset_id)
    except FileNotFoundError:
        raise HTTPException(404, f"Preset not found: {preset_id}")
    return {"ok": True}


from pydantic import BaseModel  # noqa: E402


class ChromaUpdate(BaseModel):
    chroma_key: Optional[str] = None       # "" to disable keying, "#RRGGBB" to set
    chroma_similarity: Optional[float] = None
    chroma_blend: Optional[float] = None


@router.post("/{preset_id}/process-ai")
async def trigger_ai_processing(preset_id: str):
    """
    Enqueue an AI background-removal job for this preset. Result is written
    as `processed.webm` alongside the source mp4 and used automatically by
    the overlay stage in future runs (no chroma key needed).
    """
    preset = commentators.get_preset(preset_id)
    if not preset:
        raise HTTPException(404, f"Preset not found: {preset_id}")

    job_id = uuid.uuid4().hex[:12]
    payload = {"preset_id": preset_id}
    async with async_session() as session:
        row = JobModel(
            id=job_id,
            project_id="__utility__",
            type=JobType.commentator_bg_remove.value,
            status=JobStatus.queued.value,
            metadata_json=json.dumps(payload),
        )
        session.add(row)
        await session.commit()
    return {"job_id": job_id, "preset_id": preset_id, "status": "queued"}


@router.delete("/{preset_id}/processed")
async def delete_processed(preset_id: str):
    """Remove the AI-processed webm so the preset falls back to chroma key."""
    p = commentators._ai_processed_path(preset_id)
    if not p.exists():
        raise HTTPException(404, "No AI-processed file for this preset")
    p.unlink()
    return {"ok": True}


@router.patch("/{preset_id}/chroma")
async def update_chroma(preset_id: str, req: ChromaUpdate):
    """Persist chroma-key changes onto the preset's meta.json."""
    try:
        return commentators.update_chroma(
            preset_id,
            chroma_key=req.chroma_key,
            chroma_similarity=req.chroma_similarity,
            chroma_blend=req.chroma_blend,
        )
    except FileNotFoundError:
        raise HTTPException(404, f"Preset not found: {preset_id}")
    except ValueError as e:
        raise HTTPException(400, str(e))
