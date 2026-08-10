"""
ClipForge — AI Stream Clipper: export and preview render jobs.

Both handlers do the same three things — rebuild the caption ASS from the
stored plan, ask the layout engine for a filtergraph, run ONE ffmpeg encode —
and differ only in resolution, quality and whether the result is a deliverable.

Why rebuild the ASS at render time instead of storing it: the user can edit the
transcript, the preset, the position or the crop between analysis and export,
and a stale .ass on disk would silently ship the pre-edit captions.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import update

from config import settings
from database import async_session
from models import ClipModel, ClipStatus, ProjectModel
from services.clipper import storage
from services.clipper.serialize import effective_content_type

logger = logging.getLogger("clipforge.clipper.render")


async def _load(clip_id: str) -> tuple[ClipModel, ProjectModel]:
    async with async_session() as session:
        clip = await session.get(ClipModel, clip_id)
        project = await session.get(ProjectModel, clip.project_id) if clip else None
    if not clip:
        raise RuntimeError(f"clip {clip_id} disappeared before its render started")
    if not project:
        raise RuntimeError(f"project for clip {clip_id} no longer exists")
    return clip, project


def _source_path(project: ProjectModel) -> str:
    src = project.video_path or ""
    if not src or not Path(src).exists():
        raise RuntimeError(
            "The source video is no longer on disk. Re-run the analysis to fetch it again."
        )
    return src


def _candidate(clip: ClipModel) -> dict:
    """The shape the render/caption/layout helpers expect from a candidate."""
    return {
        "start": float(clip.start_time or 0.0),
        "end": float(clip.end_time or 0.0),
        "text": clip.transcript_text or "",
        "headline": clip.headline_text or "",
    }


def _write_ass(clip: ClipModel, out_dir: Path) -> str | None:
    """Render the stored caption plan to an .ass file. Returns None when the
    clip has no captions, which is a legitimate state (the user can turn them
    off) — the render then simply skips the subtitles filter."""
    from services.caption_overlays import build_overlays_ass
    from services.clipper.captions import caption_plan_to_overlays

    if not clip.caption_plan:
        return None
    try:
        overlays = caption_plan_to_overlays(clip.caption_plan)
    except Exception:
        logger.warning("could not turn the caption plan into overlays", exc_info=True)
        return None
    if not overlays:
        return None

    out_dir.mkdir(parents=True, exist_ok=True)
    ass_path = out_dir / f"{clip.id}.ass"
    # The ASS canvas is the OUTPUT canvas: libass positions against PlayRes, and
    # the plan's x_pct/y_pct were resolved against 1080x1920 safe zones.
    build_overlays_ass(overlays, 1080, 1920, str(ass_path))
    return str(ass_path)


def _layout_plan(clip: ClipModel, project: ProjectModel) -> dict:
    """Use the stored plan; fall back to a centre crop if analysis never produced
    one (e.g. an alternative the user promoted by hand)."""
    if clip.layout_plan:
        return clip.layout_plan
    from services.clipper import layout as layout_mod

    regions = storage.read_artifact(project.id, "regions") or {}
    faces_blob = storage.read_artifact(project.id, "faces") or {}
    cfg = project.clipper_settings or {}
    return layout_mod.plan_layout(
        _candidate(clip),
        regions,
        faces_blob.get("samples") or [],
        int(project.width or 1920),
        int(project.height or 1080),
        mode=cfg.get("layout_mode") or "auto",
        face_pct=float(cfg.get("face_pct") or settings.clipper_face_pct),
        include_chat=bool(cfg.get("include_chat")),
        content_type=clip.content_type or effective_content_type(project),
    )


async def handle_export(job_id: str, project_id: str, clip_id, metadata, queue) -> None:
    """Full-quality 1080x1920 deliverable."""
    from services.clipper.render import render_clip

    if not clip_id:
        raise RuntimeError("export job started without a clip id")

    clip, project = await _load(clip_id)
    src = _source_path(project)
    cfg = project.clipper_settings or {}
    paths = storage.paths(project_id)

    await queue.update_progress(job_id, 0.05, "Rendering export")
    ass_path = _write_ass(clip, paths["exports_dir"])
    plan = _layout_plan(clip, project)

    fps = cfg.get("fps")
    fps = int(project.fps or settings.clipper_export_fps) if fps == "source" else int(
        fps or settings.clipper_export_fps
    )

    out = storage.export_path(project_id, clip.id)
    try:
        result = await render_clip(
            src,
            _candidate(clip),
            plan,
            ass_path,
            str(out),
            fps=fps,
            crf=int(settings.clipper_export_crf),
            preset=settings.clipper_export_preset,
            watermark=str(cfg.get("watermark_text") or ""),
            on_progress=lambda p, m: queue.update_progress(job_id, 0.05 + 0.9 * p, m),
            is_cancelled=lambda: queue.is_cancelled(job_id),
        )
    except Exception:
        async with async_session() as session:
            await session.execute(
                update(ClipModel).where(ClipModel.id == clip.id).values(
                    status=ClipStatus.failed.value
                )
            )
            await session.commit()
        raise

    # A sidecar with everything needed to reproduce this file — source
    # timestamps, scores, the layout and caption plans, and the model versions
    # that produced them (brief §25).
    sidecar = out.with_suffix(".json")
    sidecar.write_text(
        __import__("json").dumps(
            {
                "clip_id": clip.id,
                "project_id": project_id,
                "source": {"path": src, "url": project.source_url},
                "source_start": clip.start_time,
                "source_end": clip.end_time,
                "duration": clip.duration,
                "title": clip.title,
                "headline": clip.headline_text,
                "transcript": clip.transcript_text,
                "overall_score": clip.overall_score,
                "sub_scores": clip.sub_scores,
                "score_reason": clip.score_reason,
                "layout_plan": plan,
                "caption_plan": clip.caption_plan,
                "content_type": clip.content_type,
                "analysis_version": project.analysis_version,
                "ranker_version": clip.ranker_version,
                "render": {"fps": fps, "crf": settings.clipper_export_crf,
                           "preset": settings.clipper_export_preset},
            },
            indent=2,
            ensure_ascii=False,
            default=str,
        ),
        encoding="utf-8",
    )

    async with async_session() as session:
        await session.execute(
            update(ClipModel)
            .where(ClipModel.id == clip.id)
            .values(status=ClipStatus.exported.value, export_path=str(out))
        )
        await session.commit()

    from services.clipper import feedback

    async with async_session() as session:
        await feedback.record(
            session, clip.id, project_id, "exported",
            {"path": str(out), "size": result.get("size")},
        )

    await queue.update_progress(job_id, 1.0, "Completed")
    logger.info(f"clipper export {clip.id}: {result.get('size', 0) // 1024} KB → {out.name}")


async def handle_preview(job_id: str, project_id: str, clip_id, metadata, queue) -> None:
    """Fast, low-resolution proxy render for the editor.

    Deliberately never marks the clip exported and never writes a sidecar — a
    preview is scratch, and treating it as a deliverable would pollute both the
    export list and the feedback labels.
    """
    from services.clipper.render import render_preview

    if not clip_id:
        raise RuntimeError("preview job started without a clip id")

    clip, project = await _load(clip_id)
    src = _source_path(project)
    paths = storage.paths(project_id)

    await queue.update_progress(job_id, 0.10, "Generating previews")
    ass_path = _write_ass(clip, paths["previews_dir"])
    plan = _layout_plan(clip, project)
    out = storage.preview_path(project_id, clip.id)

    await render_preview(src, _candidate(clip), plan, ass_path, str(out))

    async with async_session() as session:
        await session.execute(
            update(ClipModel).where(ClipModel.id == clip.id).values(preview_path=str(out))
        )
        await session.commit()

    from services.clipper import feedback

    async with async_session() as session:
        await feedback.record(session, clip.id, project_id, "previewed", None)

    await queue.update_progress(job_id, 1.0, "Preview ready")
