"""
ClipForge — AI Stream Clipper pipeline: ingest → transcribe → analyze.

The candidate-building stage lives in workers/clipper_build.py and the render
jobs in workers/clipper_render_jobs.py; this module registers all six.

Design notes worth keeping in mind when editing:

  * Each stage writes its output to disk BEFORE enqueuing the next one, so a
    crash or a restart resumes from the last completed stage instead of
    re-downloading a 4 GB VOD. /projects/{id}/retry reads exactly those
    artifacts to decide where to restart.
  * Progress messages are not decoration — the frontend matches them against
    PIPELINE_STAGES to light up its step list. Change a string here and change
    it in src/types/clipper.ts too.
  * Everything expensive reads the 480p proxy, never the original. The source
    file is opened again only when a clip is actually exported.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select, update

from config import settings
from database import async_session
from job_queue import JobCancelledError
from models import JobType, ProjectModel, ProjectStatus, TranscriptModel
from services.clipper import ANALYSIS_VERSION, storage

logger = logging.getLogger("clipforge.clipper.pipeline")


# ── Shared helpers ──────────────────────────────────────────────────────────


async def _set_status(project_id: str, status: str) -> None:
    async with async_session() as session:
        await session.execute(
            update(ProjectModel).where(ProjectModel.id == project_id).values(status=status)
        )
        await session.commit()


async def _load_project(project_id: str) -> ProjectModel:
    async with async_session() as session:
        project = await session.get(ProjectModel, project_id)
    if not project:
        raise RuntimeError(f"project {project_id} disappeared mid-pipeline")
    return project


def _guard(queue, job_id: str) -> None:
    """Raise the queue's cancellation error if the user pressed Cancel.

    Called between stages rather than only at the start: a long ffmpeg run can
    finish after the user gave up, and we should not enqueue the next stage.
    """
    if queue.is_cancelled(job_id):
        raise JobCancelledError("Cancelled by user.")


def _settings_for(project: ProjectModel) -> dict[str, Any]:
    return dict(project.clipper_settings or {})


# ── Stage 1: ingest ─────────────────────────────────────────────────────────


async def handle_ingest(job_id: str, project_id: str, clip_id, metadata, queue) -> None:
    """Validate → metadata → download/copy → proxy → audio → thumbnail."""
    from services.clipper import ingest

    project = await _load_project(project_id)
    storage.ensure_dirs(project_id)
    paths = storage.paths(project_id)

    await queue.update_progress(job_id, 0.02, "Validating source")
    await _set_status(project_id, ProjectStatus.fetching_metadata.value)

    async def _download_progress(fraction: float, message: str) -> None:
        # The download owns 10%→55% of this job's bar.
        await queue.update_progress(job_id, 0.10 + 0.45 * max(0.0, min(1.0, fraction)), message)

    await queue.update_progress(job_id, 0.06, "Reading metadata")
    _guard(queue, job_id)

    # An upload already sits on disk from the create call; a URL needs fetching.
    already_local = bool(project.video_path) and project.source_kind == "upload"
    await _set_status(project_id, ProjectStatus.downloading.value)
    await queue.update_progress(job_id, 0.10, "Downloading")

    info = await ingest.ingest_source(
        project_id,
        url=None if already_local else project.source_url,
        local_path=project.video_path if already_local else None,
        max_duration_s=settings.clipper_max_source_duration_s,
        min_free_bytes=settings.clipper_min_free_bytes,
        on_progress=_download_progress,
        is_cancelled=lambda: queue.is_cancelled(job_id),
    )
    _guard(queue, job_id)

    async with async_session() as session:
        await session.execute(
            update(ProjectModel)
            .where(ProjectModel.id == project_id)
            .values(
                video_path=info["video_path"],
                duration=info.get("duration"),
                width=info.get("width"),
                height=info.get("height"),
                fps=info.get("fps"),
                filesize=info.get("filesize"),
                status=ProjectStatus.downloaded.value,
                analysis_version=ANALYSIS_VERSION,
            )
        )
        await session.commit()

    await queue.update_progress(job_id, 0.60, "Creating proxy")
    await ingest.build_proxy(
        project_id,
        info["video_path"],
        width=settings.clipper_proxy_width,
        fps=settings.clipper_proxy_fps,
    )
    _guard(queue, job_id)

    await queue.update_progress(job_id, 0.85, "Extracting audio")
    await ingest.extract_audio(project_id, info["video_path"])
    _guard(queue, job_id)

    # A poster frame a quarter of the way in beats frame 0, which is usually
    # black or a title card.
    try:
        thumb_t = max(1.0, float(info.get("duration") or 4.0) * 0.25)
        await ingest.extract_thumbnail(project_id, info["video_path"], thumb_t, "poster.jpg")
    except Exception:
        logger.warning("poster thumbnail failed; continuing without one", exc_info=True)

    storage.write_artifact(
        project_id,
        "meta",
        {
            "analysis_version": ANALYSIS_VERSION,
            "source": {k: info.get(k) for k in ("duration", "width", "height", "fps", "filesize")},
            "proxy": {"width": settings.clipper_proxy_width, "fps": settings.clipper_proxy_fps},
        },
    )

    await queue.update_progress(job_id, 1.0, "Downloaded")
    await queue.enqueue(project_id=project_id, job_type=JobType.clipper_transcribe.value)
    logger.info(f"clipper ingest done for {project_id} ({paths['proxy'].name})")


# ── Stage 2: transcribe ─────────────────────────────────────────────────────


async def handle_transcribe(job_id: str, project_id: str, clip_id, metadata, queue) -> None:
    """Word-level transcription WITH punctuation.

    keep_punctuation=True is the whole point: the segmentation and hook
    heuristics run on sentence boundaries and question marks, and the default
    caption-oriented path strips both.
    """
    from services.transcriber import transcribe

    project = await _load_project(project_id)
    cfg = _settings_for(project)
    paths = storage.paths(project_id)

    if not paths["audio"].exists():
        raise RuntimeError("No extracted audio — run ingest first.")

    await _set_status(project_id, ProjectStatus.transcribing.value)
    await queue.update_progress(job_id, 0.02, "Transcribing")

    language = cfg.get("language") or "auto"
    result = await transcribe(
        str(paths["audio"]),
        duration=float(project.duration or 0.0),
        is_cancelled=lambda: queue.is_cancelled(job_id),
        # Pass the transcriber's own message through. It says "Transcribing...
        # 1234.5s / 15826.0s (12%) [chunk 3/9]"; the constant "Transcribing"
        # this used to send threw all of it away, and on a 4-hour source that
        # leaves a 90-minute stage looking identical to a hung one for its
        # whole duration. handle_export next door already passes `m`.
        on_progress=lambda p, m: queue.update_progress(
            job_id, max(0.02, p * 0.97), m or "Transcribing"),
        language=None if language in ("auto", "", None) else language,
        keep_punctuation=True,
    )
    _guard(queue, job_id)

    if result.get("cancelled"):
        raise JobCancelledError("Transcription cancelled by user.")

    segments = result.get("segments") or []
    if not segments:
        raise RuntimeError(
            "The transcript came back empty. The source may have no speech, or the audio "
            "track may be silent."
        )

    # Chunks whisper could not finish. The transcriber has reported these since
    # `_tolerant` stopped one bad chunk losing a whole 4-hour file, and nothing
    # read them — so a transcript with holes went downstream looking complete,
    # and every clip scored against a missing stretch was scored against silence
    # that is not there. Recorded on the row and said out loud in the stage
    # message, which is the only place a person is looking at this point.
    failed_chunks = [int(c) for c in (result.get("failed_chunks") or [])]
    if failed_chunks:
        logger.warning("project %s: transcript is missing %d chunk(s): %s",
                       project_id, len(failed_chunks), failed_chunks)

    async with async_session() as session:
        existing = await session.execute(
            select(TranscriptModel).where(TranscriptModel.project_id == project_id).limit(1)
        )
        row = existing.scalar_one_or_none()
        if row:
            await session.execute(
                update(TranscriptModel)
                .where(TranscriptModel.project_id == project_id)
                .values(
                    language=result.get("language"),
                    segments=segments,
                    full_text=result.get("full_text"),
                    word_count=result.get("word_count"),
                    failed_chunks=failed_chunks or None,
                )
            )
        else:
            session.add(
                TranscriptModel(
                    project_id=project_id,
                    language=result.get("language"),
                    segments=segments,
                    full_text=result.get("full_text"),
                    word_count=result.get("word_count"),
                    failed_chunks=failed_chunks or None,
                )
            )
        await session.execute(
            update(ProjectModel)
            .where(ProjectModel.id == project_id)
            .values(status=ProjectStatus.transcribed.value)
        )
        await session.commit()

    await queue.update_progress(
        job_id, 1.0,
        f"Transcribed — {len(failed_chunks)} chunk(s) missing"
        if failed_chunks else "Transcribed")
    await queue.enqueue(project_id=project_id, job_type=JobType.clipper_analyze.value)
    logger.info(
        f"clipper transcript for {project_id}: {len(segments)} segments, "
        f"{result.get('word_count')} words, lang={result.get('language')}"
    )


# ── Stage 3: analyze ────────────────────────────────────────────────────────


async def _transcript_words(project_id: str) -> list[dict]:
    """Every word with a timing, flattened. [] when there is no transcript yet.

    Only used to answer "was anything transcribed here", so a missing transcript
    degrades the vocal-burst detector to "every loud voiced moment counts"
    rather than breaking the stage.
    """
    async with async_session() as session:
        row = (await session.execute(
            select(TranscriptModel)
            .where(TranscriptModel.project_id == project_id).limit(1)
        )).scalar_one_or_none()
    segments = (row.segments if row else None) or []
    if not isinstance(segments, list):
        return []
    return [w for s in segments if isinstance(s, dict) for w in (s.get("words") or [])]


async def handle_analyze(job_id: str, project_id: str, clip_id, metadata, queue) -> None:
    """Pass A + content-type/region detection. Reads only the proxy."""
    import asyncio

    from services.clipper import content_type as ct
    from services.clipper import ingest, signals, vocal_bursts

    project = await _load_project(project_id)
    paths = storage.paths(project_id)
    if not paths["proxy"].exists():
        raise RuntimeError("No analysis proxy — run ingest first.")

    duration = float(project.duration or 0.0)
    loop = asyncio.get_event_loop()

    await queue.update_progress(job_id, 0.05, "Detecting scenes")
    sig = await loop.run_in_executor(
        None,
        lambda: signals.build_signals(project_id, str(paths["proxy"]), str(paths["audio"]), duration),
    )
    _guard(queue, job_id)

    # Laughter and shouting, which the transcript cannot carry: Whisper does not
    # write "haha", so the word list `laughter_score` was built on read 0 on
    # every window of every source while holding 30% of `emotion` and 20% of
    # `reaction`. This needs the WORDS as well as the audio — a loud voiced
    # moment matters precisely when no word was transcribed for it — so it runs
    # here, after transcription, rather than inside build_signals.
    await queue.update_progress(job_id, 0.40, "Listening for reactions")
    words = await _transcript_words(project_id)
    sig["vocal_bursts"] = await loop.run_in_executor(
        None,
        lambda: vocal_bursts.vocal_burst_timeline(paths["audio"], words),
    )
    storage.write_artifact(project_id, "signals", sig)
    found = vocal_bursts.summarise(sig["vocal_bursts"])
    logger.info("project %s: %d vocal-burst frames of %d (%.1f%%)",
                project_id, found["burst_frames"], found["frames"],
                100.0 * found["share"])
    _guard(queue, job_id)

    # Sample frames on a stride that keeps the count bounded no matter how long
    # the source is — a 6-hour stream must not turn into 20k JPEGs.
    await queue.update_progress(job_id, 0.45, "Detecting faces and regions")
    max_frames = max(8, int(settings.clipper_max_sampled_frames))
    count = min(max_frames, max(8, int(duration // 15) or 8))
    step = duration / (count + 1) if duration > 0 else 1.0
    times = [round(step * (i + 1), 2) for i in range(count)]
    frames = await ingest.sample_frames(project_id, str(paths["proxy"]), times)
    _guard(queue, job_id)

    regions = await loop.run_in_executor(None, lambda: ct.detect_regions(frames))
    storage.write_artifact(project_id, "regions", regions)
    # ...and again per stretch. Layout is detected ONCE for a whole source, and
    # seven of eleven labelled sources change layout part-way through. Measured
    # on the 4-hour slice: the whole-file answer applies a Minecraft facecam
    # rect to the first 35 minutes, which are a full-frame camera with no game
    # in them at all; per stretch those minutes correctly report none.
    # `regions` above is still written and is still the fallback.
    from services.clipper import segment_type as seg_type_mod

    try:
        by_range = await loop.run_in_executor(
            None, lambda: ct.detect_regions_by_range(
                frames, times, seg_type_mod.clock_ranges(duration)))
    except Exception:
        logger.warning("clipper %s: per-stretch region detection failed; the "
                       "whole-file regions stand", project_id, exc_info=True)
        by_range = []
    if by_range:
        storage.write_artifact(project_id, "regions_by_segment", by_range)
    storage.write_artifact(
        project_id, "faces", {"samples": sig.get("faces") or [], "times": times}
    )

    await queue.update_progress(job_id, 0.80, "Detecting content type")
    async with async_session() as session:
        result = await session.execute(
            select(TranscriptModel).where(TranscriptModel.project_id == project_id).limit(1)
        )
        row = result.scalar_one_or_none()
    transcript_dict = {"segments": (row.segments if row else None) or []}

    detected = await loop.run_in_executor(
        None, lambda: ct.detect_content_type(frames, sig, transcript_dict)
    )
    async with async_session() as session:
        await session.execute(
            update(ProjectModel)
            .where(ProjectModel.id == project_id)
            .values(
                content_type=detected.get("content_type"),
                content_type_confidence=detected.get("confidence"),
                status=ProjectStatus.scoring.value,
            )
        )
        await session.commit()

    meta = storage.read_artifact(project_id, "meta") or {}
    meta["content_type"] = detected
    meta["frames_sampled"] = len(frames)
    storage.write_artifact(project_id, "meta", meta)

    await queue.update_progress(job_id, 1.0, "Detected content type")
    await queue.enqueue(project_id=project_id, job_type=JobType.clipper_score.value)
    logger.info(
        f"clipper analysis for {project_id}: {detected.get('content_type')} "
        f"@{detected.get('confidence'):.2f} from {len(frames)} frames"
        if detected.get("confidence") is not None
        else f"clipper analysis for {project_id}: {detected.get('content_type')}"
    )


def register_clipper_handlers(queue) -> None:
    """Wire all six clipper job types. Called from main.py's lifespan."""
    from workers.clipper_build import handle_score
    from workers.clipper_render_jobs import handle_export, handle_preview

    queue.register_handler(JobType.clipper_ingest.value, handle_ingest)
    queue.register_handler(JobType.clipper_transcribe.value, handle_transcribe)
    queue.register_handler(JobType.clipper_analyze.value, handle_analyze)
    queue.register_handler(JobType.clipper_score.value, handle_score)
    queue.register_handler(JobType.clipper_export.value, handle_export)
    queue.register_handler(JobType.clipper_preview.value, handle_preview)
