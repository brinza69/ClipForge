"""
ClipForge — Clips Router
API endpoints for clip candidates, editing, and export triggering.
"""

from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from database import get_session
from models import ClipModel, ClipStatus, JobType, ProjectModel, TranscriptModel
from schemas import ClipResponse
from job_queue import job_queue
from config import settings

router = APIRouter(prefix="/api/clips", tags=["clips"])


def _to_response(clip: ClipModel) -> ClipResponse:
    """Build a ClipResponse and compute whether the export file actually exists on disk."""
    resp = ClipResponse.model_validate(clip)
    if clip.export_path:
        try:
            resp.export_file_exists = Path(clip.export_path).exists()
        except Exception:
            resp.export_file_exists = False
    else:
        resp.export_file_exists = False
    return resp


class ClipUpdate(BaseModel):
    title: Optional[str] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    reframe_mode: Optional[str] = None
    status: Optional[str] = None
    caption_preset_id: Optional[str] = None
    caption_style: Optional[dict] = None
    hook_text: Optional[str] = None
    caption_y_pct: Optional[float] = None
    caption_align: Optional[str] = None
    hook_y_pct: Optional[float] = None
    hook_align: Optional[str] = None
    caption_font_size: Optional[float] = None
    caption_text_color: Optional[str] = None
    hook_font_size: Optional[float] = None
    hook_text_color: Optional[str] = None
    hook_bg_color: Optional[str] = None
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
    hook_box_size: Optional[int] = None
    hook_box_width: Optional[int] = None
    hook_duration_seconds: Optional[float] = None
    hook_x: Optional[int] = None
    hook_y: Optional[int] = None
    subtitle_x: Optional[int] = None
    subtitle_y: Optional[int] = None
    export_resolution: Optional[str] = None
    split_mode: Optional[str] = None
    split_parts_count: Optional[int] = None
    part_label_font_size: Optional[int] = None
    part_label_box_size: Optional[int] = None
    part_label_text_color: Optional[str] = None
    part_label_bg_color: Optional[str] = None
    part_label_x: Optional[int] = None
    part_label_y: Optional[int] = None
    hook_bg_enabled: Optional[bool] = None
    title_text: Optional[str] = None
    title_font_size: Optional[int] = None
    title_x: Optional[int] = None
    title_y: Optional[int] = None
    title_box_size: Optional[int] = None
    title_box_width: Optional[int] = None
    title_bg_enabled: Optional[bool] = None


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
    return [_to_response(c) for c in clips]


@router.get("/{clip_id}", response_model=ClipResponse)
async def get_clip(clip_id: str, session: AsyncSession = Depends(get_session)):
    """Get a single clip by ID."""
    clip = await session.get(ClipModel, clip_id)
    if not clip:
        raise HTTPException(404, "Clip not found")
    return _to_response(clip)


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
    return _to_response(clip)


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


@router.get("/{clip_id}/preview")
async def preview_clip(clip_id: str, session: AsyncSession = Depends(get_session)):
    """Render a quick low-res preview MP4 and serve it for playback in a new tab.

    Uses the same export pipeline (reframe + captions + audio) at 540x960 with
    ultrafast encoding so the result matches the final export visually.
    """
    import logging
    logger = logging.getLogger("clipforge.preview")

    clip = await session.get(ClipModel, clip_id)
    if not clip:
        raise HTTPException(404, "Clip not found")
    project = await session.get(ProjectModel, clip.project_id)
    if not project or not project.video_path:
        raise HTTPException(400, "Project video not found")

    # Check for a cached preview
    previews_dir = settings.data_dir / "previews"
    previews_dir.mkdir(parents=True, exist_ok=True)
    preview_path = previews_dir / f"{clip_id}_preview.mp4"

    # If the cached preview is older than the clip's last update, re-render
    needs_render = True
    if preview_path.exists() and preview_path.stat().st_size > 1000:
        from datetime import timezone
        clip_mtime = clip.updated_at.replace(tzinfo=timezone.utc) if clip.updated_at else None
        preview_mtime_ts = preview_path.stat().st_mtime
        from datetime import datetime
        preview_mtime = datetime.fromtimestamp(preview_mtime_ts, tz=timezone.utc)
        if clip_mtime and preview_mtime > clip_mtime:
            needs_render = False

    if needs_render:
        logger.info(f"Rendering preview for clip {clip_id}")
        from services.reframer import analyze_reframe
        from services.captioner import generate_captions, DEFAULT_PRESETS
        from services.exporter import export_clip as run_export

        video_path = project.video_path
        start_time = clip.start_time
        end_time = clip.end_time
        # Limit preview to 10 seconds for quick positioning check
        preview_duration = min(10.0, end_time - start_time)
        preview_end = start_time + preview_duration
        reframe_mode = clip.reframe_mode or "auto"

        # Get transcript
        result = await session.execute(
            select(TranscriptModel)
            .where(TranscriptModel.project_id == clip.project_id)
            .order_by(TranscriptModel.id.desc())
        )
        transcript = result.scalars().first()

        # Reframe
        import asyncio
        try:
            reframe_data = await asyncio.wait_for(
                analyze_reframe(video_path, start_time, preview_end, mode=reframe_mode),
                timeout=max(60, int(preview_duration) + 30),
            )
        except Exception:
            reframe_data = {"mode": reframe_mode, "keyframes": []}

        # Style overrides
        style_overrides = {
            "caption_font_size": clip.caption_font_size,
            "caption_text_color": clip.caption_text_color,
            "caption_highlight_color": clip.caption_highlight_color,
            "caption_outline_color": clip.caption_outline_color,
            "caption_y_position": clip.caption_y_position,
            "hook_font_size": clip.hook_font_size,
            "hook_text_color": clip.hook_text_color,
            "hook_bg_color": clip.hook_bg_color,
            "hook_bg_enabled": clip.hook_bg_enabled if clip.hook_bg_enabled is not None else True,
            "hook_y_position": clip.hook_y_position,
            "hook_box_size": clip.hook_box_size,
            "hook_box_width": clip.hook_box_width,
            "hook_duration_seconds": clip.hook_duration_seconds,
            "hook_x": clip.hook_x,
            "hook_y": clip.hook_y,
            "subtitle_x": clip.subtitle_x,
            "subtitle_y": clip.subtitle_y,
            "title_font_size": clip.title_font_size,
            "title_x": clip.title_x,
            "title_y": clip.title_y,
            "title_box_size": clip.title_box_size,
            "title_box_width": clip.title_box_width,
            "title_bg_enabled": clip.title_bg_enabled if clip.title_bg_enabled is not None else True,
        }
        style_overrides = {k: v for k, v in style_overrides.items() if v is not None}

        # Captions
        segments = clip.transcript_segments or (transcript.segments if transcript else [])
        preset_id = clip.caption_preset_id or "bold_impact"
        preset = dict(DEFAULT_PRESETS.get(preset_id, DEFAULT_PRESETS["bold_impact"]))
        is_full_video = (project.processing_mode or "clipping") == "full_video_parts"
        captions_path = generate_captions(
            segments=segments,
            clip_start=start_time,
            clip_end=preview_end,
            preset=preset,
            hook_text=clip.hook_text if not is_full_video else None,
            style_overrides=style_overrides,
            hook_bg_enabled=style_overrides.get("hook_bg_enabled", True),
            title_text=clip.title_text if is_full_video else None,
        )

        # Export at 540x960 with ultrafast for speed
        try:
            await run_export(
                video_path=video_path,
                output_path=str(preview_path),
                start_time=start_time,
                end_time=preview_end,
                reframe_data=reframe_data,
                captions_path=captions_path if captions_path else None,
                width=540,
                height=960,
                fps=24,
                bitrate="1500k",
                encoding_preset="ultrafast",
            )
        except Exception as e:
            logger.error(f"Preview render failed: {e}")
            raise HTTPException(500, f"Preview render failed: {str(e)[:200]}")

    return FileResponse(
        str(preview_path),
        media_type="video/mp4",
        filename=f"preview_{clip_id}.mp4",
    )


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
