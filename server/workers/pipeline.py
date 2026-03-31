"""
ClipForge — Processing Pipeline
End-to-end pipeline worker that orchestrates:
  metadata → download → transcribe → score → reframe → caption → export
"""

import logging
import asyncio
from typing import Optional, Dict, Any

from sqlalchemy import select, update
from database import async_session
from models import (
    ProjectModel, ProjectStatus, TranscriptModel, ClipModel, ClipStatus,
    JobType, CaptionPresetModel,
)
from config import settings
from queue import job_queue

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

    result = await download_video(
        url=url,
        project_id=project_id,
        on_progress=on_progress,
        audio_only=metadata.get("audio_only", False),
    )

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

    async with async_session() as session:
        project = await session.get(ProjectModel, project_id)
        if not project or not project.video_path:
            raise RuntimeError("No video file found for project")
        media_path = project.video_path

    await _update_project_status(project_id, ProjectStatus.transcribing)

    async def on_progress(progress, message):
        await queue.update_progress(job_id, progress, message)

    result = await transcribe(media_path=media_path, on_progress=on_progress)

    # Save transcript
    async with async_session() as session:
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
            .values(status=ProjectStatus.transcribed.value)
        )
        await session.commit()

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

    await _update_project_status(project_id, ProjectStatus.scoring)
    await queue.update_progress(job_id, 0.1, "Loading transcript...")

    # Load transcript
    async with async_session() as session:
        result = await session.execute(
            select(TranscriptModel).where(TranscriptModel.project_id == project_id)
        )
        transcript = result.scalar_one_or_none()
        if not transcript:
            raise RuntimeError("No transcript found")

        segments = transcript.segments

    await queue.update_progress(job_id, 0.2, "Analyzing content...")

    candidates = generate_clip_candidates(segments=segments)

    await queue.update_progress(job_id, 0.8, f"Found {len(candidates)} clips, saving...")

    # Save clip candidates
    async with async_session() as session:
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
                status=ClipStatus.candidate.value,
            )
            session.add(clip)

        await session.execute(
            update(ProjectModel)
            .where(ProjectModel.id == project_id)
            .values(status=ProjectStatus.ready.value)
        )
        await session.commit()

    logger.info(f"Scoring complete for project {project_id}: {len(candidates)} candidates")


async def handle_export(
    job_id: str,
    project_id: str,
    clip_id: Optional[str],
    metadata: Dict,
    queue,
):
    """Export a single clip with reframing and captions."""
    from services.reframer import analyze_reframe
    from services.captioner import generate_captions, DEFAULT_PRESETS
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
        start_time = clip.start_time
        end_time = clip.end_time
        reframe_mode = clip.reframe_mode or "auto"

        # Load transcript for captions
        result = await session.execute(
            select(TranscriptModel).where(TranscriptModel.project_id == project_id)
        )
        transcript = result.scalar_one_or_none()

        # Update clip status
        clip.status = ClipStatus.exporting.value
        await session.commit()

    # Step 1: Reframe analysis
    await queue.update_progress(job_id, 0.1, "Analyzing video for reframe...")
    reframe_data = await analyze_reframe(
        video_path=video_path,
        start_time=start_time,
        end_time=end_time,
        mode=reframe_mode,
    )

    # Step 2: Generate captions
    captions_path = None
    if transcript and transcript.segments:
        await queue.update_progress(job_id, 0.3, "Generating captions...")
        preset = DEFAULT_PRESETS.get("bold_impact")
        captions_path = generate_captions(
            segments=transcript.segments,
            clip_start=start_time,
            clip_end=end_time,
            preset=preset,
            output_path=str(settings.temp_dir / project_id / f"captions_{clip_id}.ass"),
        )

    # Step 3: Export
    output_filename = f"clip_{clip_id}.mp4"
    output_path = str(settings.exports_dir / project_id / output_filename)

    async def on_export_progress(progress, message):
        # Map to 0.4 - 1.0 range
        mapped = 0.4 + progress * 0.6
        await queue.update_progress(job_id, mapped, message)

    await export_clip(
        video_path=video_path,
        output_path=output_path,
        start_time=start_time,
        end_time=end_time,
        reframe_data=reframe_data,
        captions_path=captions_path,
        on_progress=on_export_progress,
    )

    # Update clip with export path
    async with async_session() as session:
        await session.execute(
            update(ClipModel)
            .where(ClipModel.id == clip_id)
            .values(
                status=ClipStatus.exported.value,
                export_path=output_path,
                reframe_data=reframe_data,
            )
        )
        await session.commit()

    logger.info(f"Export complete: {output_path}")


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
        # Create a progress wrapper that maps to the stage range
        async def scoped_progress(progress, message, s=start_pct, e=end_pct):
            mapped = s + progress * (e - s)
            await queue.update_progress(job_id, mapped, message)

        # Temporarily override queue's update_progress
        original_update = queue.update_progress

        async def stage_update(jid, prog, msg, s=start_pct, e=end_pct):
            if jid == job_id:
                mapped = s + prog * (e - s)
                await original_update(jid, mapped, msg)
            else:
                await original_update(jid, prog, msg)

        queue.update_progress = stage_update

        try:
            await handler(
                job_id=job_id,
                project_id=project_id,
                clip_id=clip_id,
                metadata=metadata,
                queue=queue,
            )
        finally:
            queue.update_progress = original_update


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
