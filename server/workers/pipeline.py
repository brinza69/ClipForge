"""
ClipForge — Processing Pipeline
End-to-end pipeline worker that orchestrates:
  metadata → download → transcribe → score → reframe → caption → export
"""

import logging
import asyncio
import math
from pathlib import Path
from typing import Optional, Dict, Any

from sqlalchemy import select, update, delete
from database import async_session
from models import (
    ProjectModel, ProjectStatus, TranscriptModel, ClipModel, ClipStatus,
    JobType,
)
from config import settings
from job_queue import job_queue


def _ensure_file(path: str, context: str) -> Path:
    """Verify a file exists on disk before processing. Raises RuntimeError if missing."""
    p = Path(path)
    if not p.exists():
        raise RuntimeError(
            f"{context}: File not found at {path}. "
            "The source video may have been deleted. Please re-download the project."
        )
    return p

logger = logging.getLogger("clipforge.pipeline")


async def handle_fetch_metadata(
    job_id: str,
    project_id: str,
    clip_id: Optional[str],
    metadata: Dict,
    queue,
):
    """Fetch metadata and thumbnail for a project without downloading."""
    from services.metadata import fetch_metadata

    await queue.update_progress(job_id, 0.1, "Fetching metadata...")

    async with async_session() as session:
        project = await session.get(ProjectModel, project_id)
        if not project or not project.source_url:
            raise RuntimeError("Project or source URL not found")

        url = project.source_url

    meta = await fetch_metadata(url, project_id)

    await queue.update_progress(job_id, 0.8, "Saving metadata...")

    async with async_session() as session:
        await session.execute(
            update(ProjectModel)
            .where(ProjectModel.id == project_id)
            .values(
                title=meta.get("title", "Untitled"),
                channel_name=meta.get("channel_name"),
                duration=meta.get("duration"),
                width=meta.get("width"),
                height=meta.get("height"),
                fps=meta.get("fps"),
                thumbnail_url=meta.get("thumbnail_url"),
                thumbnail_path=meta.get("thumbnail_path"),
                estimated_size=meta.get("estimated_size"),
                upload_date=meta.get("upload_date"),
                description=meta.get("description"),
                status=ProjectStatus.metadata_ready.value,
            )
        )
        await session.commit()

    logger.info(f"Metadata fetched for project {project_id}: {meta.get('title')}")


async def handle_download(
    job_id: str,
    project_id: str,
    clip_id: Optional[str],
    metadata: Dict,
    queue,
):
    """Download source video."""
    from services.downloader import download_video

    async with async_session() as session:
        project = await session.get(ProjectModel, project_id)
        if not project or not project.source_url:
            raise RuntimeError("Project or source URL not found")
        url = project.source_url

    await _update_project_status(project_id, ProjectStatus.downloading)

    async def on_progress(progress, message):
        await queue.update_progress(job_id, progress * 0.9, message)

    # Avoid rare "yt-dlp never finishes" situations by putting a hard ceiling on this stage.
    # Use project.duration as a rough scale (falls back to a safe 3h ceiling).
    duration_sec = float(getattr(project, "duration", 0.0) or 0.0)
    download_timeout = max(1800, int(duration_sec * 10) + 900) if duration_sec > 0 else 10800  # 3h fallback

    try:
        result = await asyncio.wait_for(
            download_video(
                url=url,
                project_id=project_id,
                on_progress=on_progress,
                audio_only=metadata.get("audio_only", False),
                is_cancelled=lambda: queue.is_cancelled(job_id),
            ),
            timeout=download_timeout,
        )
    except asyncio.TimeoutError:
        raise RuntimeError(f"Download timed out after {download_timeout // 60} minutes.")

    async with async_session() as session:
        await session.execute(
            update(ProjectModel)
            .where(ProjectModel.id == project_id)
            .values(
                video_path=result["video_path"],
                duration=result.get("duration"),
                width=result.get("width"),
                height=result.get("height"),
                fps=result.get("fps"),
                filesize=result.get("filesize"),
                status=ProjectStatus.downloaded.value,
            )
        )
        await session.commit()

    logger.info(f"Download complete for project {project_id}")


async def handle_transcribe(
    job_id: str,
    project_id: str,
    clip_id: Optional[str],
    metadata: Dict,
    queue,
):
    """Transcribe the source video/audio."""
    from services.transcriber import transcribe
    from job_queue import JobCancelledError

    async with async_session() as session:
        project = await session.get(ProjectModel, project_id)
        if not project or not project.video_path:
            raise RuntimeError("No video file found for project")
        media_path = project.video_path
        _ensure_file(media_path, "Transcription")

    await _update_project_status(project_id, ProjectStatus.transcribing)

    async def on_progress(progress, message):
        await queue.update_progress(job_id, progress, message)

    duration_sec = project.duration or 300.0
    timeout_secs = max(1800, min(7200, int(duration_sec * 3))) # Between 30m and 2h
    
    try:
        result = await asyncio.wait_for(
            transcribe(
                media_path=media_path,
                duration=duration_sec,
                is_cancelled=lambda: queue.is_cancelled(job_id),
                on_progress=on_progress,
            ),
            timeout=timeout_secs
        )
    except asyncio.TimeoutError:
        logger.error(f"Transcription timeout out after {timeout_secs}s for project {project_id}.")
        raise RuntimeError(f"Transcription timed out after {timeout_secs//60} minutes. File too large or system overloaded.")
    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        raise RuntimeError(f"Transcription failed: {e}")

    # Save transcript (clean up old ones first for retry support)
    cancelled = bool(result.get("cancelled"))
    async with async_session() as session:
        await session.execute(
            delete(TranscriptModel).where(TranscriptModel.project_id == project_id)
        )
        transcript = TranscriptModel(
            project_id=project_id,
            language=result["language"],
            segments=result["segments"],
            full_text=result["full_text"],
            word_count=result["word_count"],
        )
        session.add(transcript)

        await session.execute(
            update(ProjectModel)
            .where(ProjectModel.id == project_id)
            .values(
                status=(
                    ProjectStatus.cancelled.value if cancelled else ProjectStatus.transcribed.value
                )
            )
        )
        await session.commit()

    if cancelled:
        logger.info(f"Transcription cancelled for project {project_id}.")
        # Let job_queue mark the job as cancelled (not done/failed).
        raise JobCancelledError("Transcription cancelled by user.")

    logger.info(f"Transcription complete for project {project_id}: {result['word_count']} words")


async def handle_score(
    job_id: str,
    project_id: str,
    clip_id: Optional[str],
    metadata: Dict,
    queue,
):
    """Score transcript and generate clip candidates."""
    from services.scorer import generate_clip_candidates
    from services.exporter import generate_thumbnail
    from services.categories import detect_category
    from pathlib import Path

    await _update_project_status(project_id, ProjectStatus.scoring)
    await queue.update_progress(job_id, 0.1, "Loading transcript...")

    # Load transcript
    async with async_session() as session:
        project = await session.get(ProjectModel, project_id)
        result = await session.execute(
            select(TranscriptModel).where(TranscriptModel.project_id == project_id).order_by(TranscriptModel.id.desc())
        )
        transcript = result.scalars().first()
        if not transcript:
            raise RuntimeError("No transcript found")

        segments = transcript.segments
        if not project or not project.video_path:
            raise RuntimeError("No video file found for thumbnail generation")
        video_path = project.video_path
        _ensure_file(video_path, "Scoring/Thumbnails")

    # Auto-detect content category
    detected_category = detect_category(
        transcript_text=transcript.full_text or "",
        title=project.title or "",
        description=project.description or "",
        duration=project.duration or 0,
        channel_name=project.channel_name or "",
    )
    logger.info(f"Auto-detected content category: {detected_category}")

    await queue.update_progress(job_id, 0.2, f"Analyzing content ({detected_category})...")

    candidates = generate_clip_candidates(segments=segments)

    await queue.update_progress(job_id, 0.8, f"Found {len(candidates)} clips, saving...")

    # Save clip candidates (clean up old ones first for retry support)
    created_clips = []
    async with async_session() as session:
        await session.execute(
            delete(ClipModel).where(ClipModel.project_id == project_id)
        )
        for candidate in candidates:
            clip = ClipModel(
                project_id=project_id,
                title=candidate.title,
                start_time=candidate.start_time,
                end_time=candidate.end_time,
                duration=candidate.duration,
                momentum_score=candidate.momentum_score,
                hook_strength=candidate.hook_strength,
                narrative_completeness=candidate.narrative_completeness,
                curiosity_score=candidate.curiosity_score,
                emotional_intensity=candidate.emotional_intensity,
                caption_readability=candidate.caption_readability,
                confidence=candidate.confidence,
                transcript_text=candidate.transcript_text,
                transcript_segments=candidate.transcript_segments,
                hook_text=candidate.hook_text,
                explanation=candidate.explanation,
                status=ClipStatus.candidate.value,
                thumbnail_path=None,
            )
            session.add(clip)
            created_clips.append(clip)

        await session.execute(
            update(ProjectModel)
            .where(ProjectModel.id == project_id)
            .values(status=ProjectStatus.ready.value)
        )
        await session.commit()

    # Generate thumbnails for top candidates so the frontend can render preview cards.
    await queue.update_progress(job_id, 0.9, "Generating clip thumbnails...")
    thumbs_dir = settings.thumbnails_dir / project_id
    thumbs_dir.mkdir(parents=True, exist_ok=True)

    for idx, clip in enumerate(created_clips):
        # Use a small offset to avoid black frame on some sources.
        timestamp = max(0.0, float(clip.start_time) + 0.15)
        thumb_path = thumbs_dir / f"clip_{clip.id}.jpg"
        await generate_thumbnail(
            video_path=video_path,
            output_path=str(thumb_path),
            timestamp=timestamp,
        )
        async with async_session() as session:
            await session.execute(
                update(ClipModel)
                .where(ClipModel.id == clip.id)
                .values(thumbnail_path=str(thumb_path))
            )
            await session.commit()
        # Heartbeat progress for the UI
        await queue.update_progress(job_id, 0.9 + (idx + 1) / max(len(created_clips), 1) * 0.1, "Thumbnails ready...")

    logger.info(f"Scoring complete for project {project_id}: {len(candidates)} candidates")


def _compute_split_parts(clip_duration: float, split_mode: str, requested_parts: int) -> list:
    """Compute time segments for splitting a clip.

    Returns list of (part_start_offset, part_end_offset) relative to clip start.
    Each part is at most 180 seconds.
    """
    MAX_PART_DURATION = 180.0

    if split_mode == "auto":
        num_parts = max(1, math.ceil(clip_duration / MAX_PART_DURATION))
    elif split_mode == "manual" and requested_parts and requested_parts >= 1:
        num_parts = requested_parts
        # Enforce 180s max: increase parts if needed
        min_parts_needed = max(1, math.ceil(clip_duration / MAX_PART_DURATION))
        num_parts = max(num_parts, min_parts_needed)
    else:
        return [(0.0, clip_duration)]

    part_duration = clip_duration / num_parts
    parts = []
    for i in range(num_parts):
        p_start = i * part_duration
        p_end = min((i + 1) * part_duration, clip_duration)
        parts.append((p_start, p_end))
    return parts


async def handle_export(
    job_id: str,
    project_id: str,
    clip_id: Optional[str],
    metadata: Dict,
    queue,
):
    """Export a single clip with reframing, captions, and optional splitting."""
    from services.reframer import analyze_reframe
    from services.captioner import generate_captions, _add_part_label_event, DEFAULT_PRESETS
    from services.exporter import export_clip

    if not clip_id:
        raise RuntimeError("No clip_id provided for export job")

    async with async_session() as session:
        project = await session.get(ProjectModel, project_id)
        clip = await session.get(ClipModel, clip_id)
        if not project or not clip:
            raise RuntimeError("Project or clip not found")
        if not project.video_path:
            raise RuntimeError("No video file found")

        video_path = project.video_path
        _ensure_file(video_path, "Export")
        start_time = clip.start_time
        end_time = clip.end_time
        reframe_mode = clip.reframe_mode or "auto"
        result = await session.execute(
            select(TranscriptModel)
            .where(TranscriptModel.project_id == project_id)
            .order_by(TranscriptModel.id.desc())
        )
        transcript = result.scalars().first()

        clip.status = ClipStatus.exporting.value
        await session.commit()

    clip_duration = end_time - start_time

    # Determine split parts
    split_mode = clip.split_mode or "off"
    split_parts = _compute_split_parts(clip_duration, split_mode, clip.split_parts_count or 0)
    total_parts = len(split_parts)
    is_split = total_parts > 1

    # Step 1: Reframe analysis (once for entire clip)
    await queue.update_progress(job_id, 0.1, "Analyzing video for reframe...")
    analyze_timeout = max(120, int(clip_duration * 2) + 60)
    reframe_data = await asyncio.wait_for(
        analyze_reframe(
            video_path=video_path,
            start_time=start_time,
            end_time=end_time,
            mode=reframe_mode,
        ),
        timeout=analyze_timeout,
    )

    # Build style overrides dict
    style_overrides = {
        "caption_font_size": clip.caption_font_size,
        "caption_text_color": clip.caption_text_color,
        "caption_highlight_color": clip.caption_highlight_color,
        "caption_outline_color": clip.caption_outline_color,
        "caption_y_position": clip.caption_y_position,
        "hook_font_size": clip.hook_font_size,
        "hook_text_color": clip.hook_text_color,
        "hook_bg_color": clip.hook_bg_color,
        "hook_y_position": clip.hook_y_position,
        "hook_box_size": clip.hook_box_size,
        "hook_duration_seconds": clip.hook_duration_seconds,
        "hook_x": clip.hook_x,
        "hook_y": clip.hook_y,
        "subtitle_x": clip.subtitle_x,
        "subtitle_y": clip.subtitle_y,
        "part_label_font_size": clip.part_label_font_size,
        "part_label_box_size": clip.part_label_box_size,
        "part_label_text_color": clip.part_label_text_color,
        "part_label_bg_color": clip.part_label_bg_color,
        "part_label_x": clip.part_label_x,
        "part_label_y": clip.part_label_y,
    }
    style_overrides = {k: v for k, v in style_overrides.items() if v is not None}

    # Parse export resolution
    export_w, export_h = None, None
    if clip.export_resolution:
        try:
            parts = clip.export_resolution.lower().split("x")
            export_w, export_h = int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            pass

    preset = DEFAULT_PRESETS.get(clip.caption_preset_id or "bold_impact") or DEFAULT_PRESETS.get("bold_impact")

    # Caption segments source
    caption_segments = None
    if clip.transcript_segments:
        caption_segments = clip.transcript_segments
    elif transcript and transcript.segments:
        caption_segments = transcript.segments

    output_paths = []

    for part_idx, (part_offset_start, part_offset_end) in enumerate(split_parts):
        part_num = part_idx + 1
        part_abs_start = start_time + part_offset_start
        part_abs_end = start_time + part_offset_end
        part_duration = part_abs_end - part_abs_start

        progress_base = 0.2 + (part_idx / total_parts) * 0.7
        progress_range = 0.7 / total_parts

        if is_split:
            await queue.update_progress(
                job_id, progress_base, f"Part {part_num}/{total_parts}: generating captions..."
            )
        else:
            await queue.update_progress(job_id, 0.3, "Generating captions...")

        # Generate captions for this part
        captions_path = None
        if caption_segments:
            # For split parts, only show hook on part 1
            part_hook_text = clip.hook_text if (not is_split or part_num == 1) else None

            captions_path = generate_captions(
                segments=caption_segments,
                clip_start=part_abs_start,
                clip_end=part_abs_end,
                preset=preset,
                output_path=str(settings.temp_dir / project_id / f"captions_{clip_id}_p{part_num}.ass"),
                hook_text=part_hook_text,
                style_overrides=style_overrides or None,
            )

            # Add part label overlay for split videos
            if is_split and captions_path:
                import pysubs2
                subs = pysubs2.load(captions_path, encoding="utf-8")
                _add_part_label_event(
                    subs, part_num, total_parts,
                    duration_ms=int(part_duration * 1000),
                    style_overrides=style_overrides,
                )
                subs.save(captions_path, encoding="utf-8")

        # If no caption segments but we still need a part label
        if is_split and not captions_path:
            import pysubs2
            from services.captioner import hex_to_ass_color
            subs = pysubs2.SSAFile()
            subs.info["PlayResX"] = str(settings.export_width)
            subs.info["PlayResY"] = str(settings.export_height)
            _add_part_label_event(
                subs, part_num, total_parts,
                duration_ms=int(part_duration * 1000),
                style_overrides=style_overrides,
            )
            captions_path = str(settings.temp_dir / project_id / f"partlabel_{clip_id}_p{part_num}.ass")
            Path(captions_path).parent.mkdir(parents=True, exist_ok=True)
            subs.save(captions_path, encoding="utf-8")

        # Build output path
        if is_split:
            output_filename = f"clip_{clip_id}_part{part_num}.mp4"
        else:
            output_filename = f"clip_{clip_id}.mp4"
        output_path = str(settings.exports_dir / project_id / output_filename)

        async def on_export_progress(progress, message, _base=progress_base, _range=progress_range):
            mapped = _base + progress * _range
            await queue.update_progress(job_id, mapped, message)

        export_timeout = max(300, int(part_duration * 6) + 120)
        await asyncio.wait_for(
            export_clip(
                video_path=video_path,
                output_path=output_path,
                start_time=part_abs_start,
                end_time=part_abs_end,
                reframe_data=reframe_data,
                captions_path=captions_path,
                on_progress=on_export_progress,
                width=export_w,
                height=export_h,
            ),
            timeout=export_timeout,
        )
        output_paths.append(output_path)

    # Update clip with export path (first part or single file)
    final_export_path = output_paths[0] if output_paths else None
    async with async_session() as session:
        await session.execute(
            update(ClipModel)
            .where(ClipModel.id == clip_id)
            .values(
                status=ClipStatus.exported.value,
                export_path=final_export_path,
                reframe_data=reframe_data,
            )
        )
        await session.commit()

    if is_split:
        logger.info(f"Export complete: {total_parts} parts for clip {clip_id}")
    else:
        logger.info(f"Export complete: {final_export_path}")


async def handle_full_pipeline(
    job_id: str,
    project_id: str,
    clip_id: Optional[str],
    metadata: Dict,
    queue,
):
    """Full pipeline: download → transcribe → score."""
    stages = [
        (0.0, 0.4, handle_download),
        (0.4, 0.8, handle_transcribe),
        (0.8, 1.0, handle_score),
    ]

    for start_pct, end_pct, handler in stages:
        # Create a localized proxy queue that maps the progress range
        class ScopedQueueProxy:
            def __init__(self, base_queue, s, e):
                self._base = base_queue
                self._s = s
                self._e = e

            def __getattr__(self, name):
                return getattr(self._base, name)

            async def update_progress(self, jid, prog, msg):
                if jid == job_id:
                    mapped = self._s + prog * (self._e - self._s)
                    await self._base.update_progress(jid, mapped, msg)
                else:
                    await self._base.update_progress(jid, prog, msg)
                    
            def is_cancelled(self, jid):
                return self._base.is_cancelled(jid)

        proxy = ScopedQueueProxy(queue, start_pct, end_pct)

        await handler(
            job_id=job_id,
            project_id=project_id,
            clip_id=clip_id,
            metadata=metadata,
            queue=proxy,
        )


async def _update_project_status(project_id: str, status: ProjectStatus):
    """Update project status in database."""
    async with async_session() as session:
        await session.execute(
            update(ProjectModel)
            .where(ProjectModel.id == project_id)
            .values(status=status.value)
        )
        await session.commit()


def register_pipeline_handlers(queue):
    """Register all pipeline handlers with the job queue."""
    queue.register_handler(JobType.fetch_metadata.value, handle_fetch_metadata)
    queue.register_handler(JobType.download.value, handle_download)
    queue.register_handler(JobType.transcribe.value, handle_transcribe)
    queue.register_handler(JobType.score.value, handle_score)
    queue.register_handler(JobType.export.value, handle_export)
    queue.register_handler(JobType.full_pipeline.value, handle_full_pipeline)
    logger.info("Pipeline handlers registered")
