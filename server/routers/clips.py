"""
ClipForge — Clips Router
API endpoints for clip candidates, editing, and export triggering.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from database import get_session
from models import ClipModel, ClipStatus, JobType, TranscriptModel
from schemas import ClipResponse
from job_queue import job_queue

router = APIRouter(prefix="/api/clips", tags=["clips"])


class ClipUpdate(BaseModel):
    title: Optional[str] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    reframe_mode: Optional[str] = None
    status: Optional[str] = None
    caption_preset_id: Optional[str] = None
    hook_text: Optional[str] = None
    # Caption editing: allow replacing the caption source segments for export.
    # If `words` are omitted, the caption renderer will split segment text evenly.
    transcript_text: Optional[str] = None
    transcript_segments: Optional[list | dict] = None
    # Style overrides (null means "use preset default")
    caption_font_size: Optional[int] = None
    caption_text_color: Optional[str] = None
    caption_highlight_color: Optional[str] = None
    caption_outline_color: Optional[str] = None
    caption_y_position: Optional[str] = None
    hook_font_size: Optional[int] = None
    hook_text_color: Optional[str] = None
    hook_bg_color: Optional[str] = None
    hook_y_position: Optional[str] = None
    export_resolution: Optional[str] = None


@router.get("/", response_model=list[ClipResponse])
async def list_clips(
    project_id: str,
    session: AsyncSession = Depends(get_session),
):
    """List all clips for a project, sorted by Momentum Score."""
    result = await session.execute(
        select(ClipModel)
        .where(ClipModel.project_id == project_id)
        .order_by(ClipModel.momentum_score.desc())
    )
    clips = result.scalars().all()
    return [ClipResponse.model_validate(c) for c in clips]


@router.get("/{clip_id}", response_model=ClipResponse)
async def get_clip(clip_id: str, session: AsyncSession = Depends(get_session)):
    """Get a single clip by ID."""
    clip = await session.get(ClipModel, clip_id)
    if not clip:
        raise HTTPException(404, "Clip not found")
    return ClipResponse.model_validate(clip)


@router.patch("/{clip_id}", response_model=ClipResponse)
async def update_clip(
    clip_id: str,
    data: ClipUpdate,
    session: AsyncSession = Depends(get_session),
):
    """Update clip properties (e.g., trim points, title, status)."""
    clip = await session.get(ClipModel, clip_id)
    if not clip:
        raise HTTPException(404, "Clip not found")

    update_data = data.model_dump(exclude_unset=True)

    if "start_time" in update_data or "end_time" in update_data:
        start = update_data.get("start_time", clip.start_time)
        end = update_data.get("end_time", clip.end_time)
        update_data["duration"] = end - start

    for key, value in update_data.items():
        setattr(clip, key, value)

    await session.commit()
    await session.refresh(clip)
    return ClipResponse.model_validate(clip)


@router.post("/{clip_id}/export")
async def export_clip(clip_id: str, session: AsyncSession = Depends(get_session)):
    """Trigger export for a single clip."""
    clip = await session.get(ClipModel, clip_id)
    if not clip:
        raise HTTPException(404, "Clip not found")

    job_id = await job_queue.enqueue(
        project_id=clip.project_id,
        clip_id=clip_id,
        job_type=JobType.export.value,
    )

    return {"job_id": job_id, "clip_id": clip_id, "status": "queued"}


@router.post("/{clip_id}/reject")
async def reject_clip(clip_id: str, session: AsyncSession = Depends(get_session)):
    """Reject / hide a clip candidate."""
    clip = await session.get(ClipModel, clip_id)
    if not clip:
        raise HTTPException(404, "Clip not found")

    clip.status = ClipStatus.rejected.value
    await session.commit()
    return {"clip_id": clip_id, "status": "rejected"}


@router.post("/{clip_id}/approve")
async def approve_clip(clip_id: str, session: AsyncSession = Depends(get_session)):
    """Approve a clip candidate."""
    clip = await session.get(ClipModel, clip_id)
    if not clip:
        raise HTTPException(404, "Clip not found")

    clip.status = ClipStatus.approved.value
    await session.commit()
    return {"clip_id": clip_id, "status": "approved"}


@router.get("/{clip_id}/guidance")
async def get_clip_guidance(clip_id: str, session: AsyncSession = Depends(get_session)):
    """Generate upload guidance for a specific clip."""
    from services.campaigns import generate_upload_guidance, Campaign, _load_local_campaigns
    from services.categories import detect_category

    clip = await session.get(ClipModel, clip_id)
    if not clip:
        raise HTTPException(404, "Clip not found")

    # Auto-detect category from clip transcript
    category = detect_category(transcript_text=clip.transcript_text or "")

    # Find the best matching campaign
    campaigns = _load_local_campaigns()
    best_campaign = Campaign(
        target_platforms=["tiktok", "youtube_shorts"],
        min_duration_sec=15,
        max_duration_sec=180,
    )

    if campaigns:
        # Pick highest priority active campaign that fits our duration
        for c in sorted(campaigns, key=lambda x: x.priority_score, reverse=True):
            if c.status == "active" and c.min_duration_sec <= clip.duration <= c.max_duration_sec:
                best_campaign = c
                break

    guidance = generate_upload_guidance(
        campaign=best_campaign,
        clip_title=clip.title or "",
        clip_hook=clip.hook_text or "",
        category=category,
    )
    guidance["detected_category"] = category
    return guidance


@router.get("/project/{project_id}/transcript")
async def get_transcript(
    project_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get the full transcript for a project."""
    result = await session.execute(
        select(TranscriptModel).where(TranscriptModel.project_id == project_id).order_by(TranscriptModel.id.desc())
    )
    transcript = result.scalars().first()
    if not transcript:
        raise HTTPException(404, "Transcript not found")

    return {
        "id": transcript.id,
        "project_id": transcript.project_id,
        "language": transcript.language,
        "segments": transcript.segments,
        "full_text": transcript.full_text,
        "word_count": transcript.word_count,
    }
