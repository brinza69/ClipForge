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
from typing import Any, Sequence

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


def _clip_words(clip: ClipModel) -> list[dict]:
    """The clip's word timings, on the SOURCE clock.

    `dynamic_edit` needs these and had never been given them: `_boundaries`
    places its cuts on speech pauses and `_speech_ratio` decides whether the
    streamer is talking in a shot, and both were reading an empty list on every
    export. Measured on clip 6b34b8d37259: with words the planner cuts 11 shots
    on the pauses, without them 9 on audio peaks and scene changes alone — and
    the second is exactly what shipped.

    `transcript_segments` is the obvious home for this and is NULL on every clip
    the pipeline writes, so the words come from the caption plan, which carries
    them because the word-highlight overlay needs them. That also keeps the cut
    grid and the burned captions reading the same timings.

    The caption plan's clock is clip-relative and `dynamic_edit` subtracts
    `clip_start` from every word, so the offset has to go back on here.
    """
    plan = clip.caption_plan if isinstance(clip.caption_plan, dict) else None
    if not plan:
        return []
    offset = float(clip.start_time or 0.0)
    out: list[dict] = []
    for chunk in plan.get("chunks") or []:
        for word in (chunk or {}).get("words") or []:
            try:
                start = float(word["start"]) + offset
                end = float(word.get("end", word["start"])) + offset
            except (KeyError, TypeError, ValueError):
                continue
            out.append({"word": str(word.get("word") or ""),
                        "start": start, "end": end})
    return out


def _candidate(clip: ClipModel) -> dict:
    """The shape the render/caption/layout helpers expect from a candidate."""
    return {
        "start": float(clip.start_time or 0.0),
        "end": float(clip.end_time or 0.0),
        "text": clip.transcript_text or "",
        "headline": clip.headline_text or "",
        "words": _clip_words(clip),
    }


def _write_ass(clip: ClipModel, out_dir: Path,
               drop_spans: Sequence[tuple[float, float]] | None = None) -> str | None:
    """Render the stored caption plan to an .ass file. Returns None when the
    clip has no captions, which is a legitimate state (the user can turn them
    off) — the render then simply skips the subtitles filter.

    `drop_spans` are the dead seconds the render is about to remove. The
    overlays have to move with them: libass positions against absolute times,
    so a caption left on the untrimmed clock drifts further out of sync with
    every second cut.
    """
    from services.caption_overlays import build_overlays_ass
    from services.clipper.captions import caption_plan_to_overlays

    if not clip.caption_plan:
        return None
    try:
        overlays = caption_plan_to_overlays(clip.caption_plan)
    except Exception:
        logger.warning("could not turn the caption plan into overlays", exc_info=True)
        return None
    if drop_spans:
        from services.clipper.dead_air import remap_overlays

        overlays = remap_overlays(overlays, drop_spans)
    if not overlays:
        return None

    out_dir.mkdir(parents=True, exist_ok=True)
    ass_path = out_dir / f"{clip.id}.ass"
    # The ASS canvas is the OUTPUT canvas: libass positions against PlayRes, and
    # the plan's x_pct/y_pct were resolved against 1080x1920 safe zones.
    build_overlays_ass(overlays, 1080, 1920, str(ass_path))
    return str(ass_path)


def _plan_fits(plan: Any, src_w: int, src_h: int) -> bool:
    """Whether a stored plan's crops belong to THIS source's frame.

    A plan is crops in source pixels, valid only for the dimensions it was
    measured in. Swap the source file — a re-download, a re-encode, an HD
    replacement of a proxy-resolution cut — and the old rects can still apply
    CLEANLY while meaning something else entirely: measured, a plan built for
    854x480 and used against 1920x1080 cropped the top-left corner as the
    "facecam" and a narrow strip as the "gameplay", and nothing complained
    because every rect was comfortably inside the frame.

    So the plan carries the frame it was built for and this compares that. A
    bounds check cannot do it — the wrong plan fits.
    """
    if not isinstance(plan, dict) or src_w < 2 or src_h < 2:
        return False
    plan_w, plan_h = int(plan.get("src_w") or 0), int(plan.get("src_h") or 0)
    if plan_w >= 2 and plan_h >= 2:
        return plan_w == src_w and plan_h == src_h
    # Plans written before the frame was recorded: fall back to bounds, which
    # at least catches a plan larger than the source it is being used on.
    for key in ("face_rect", "game_rect", "chat_rect"):
        rect = plan.get(key)
        if not isinstance(rect, dict):
            continue
        if (rect.get("x", 0) + rect.get("w", 0) > src_w + 2
                or rect.get("y", 0) + rect.get("h", 0) > src_h + 2):
            return False
    return True


def _regions_for(project_id: str, t: float) -> dict:
    """The on-screen layout at time `t`, falling back to the whole-file answer.

    A source whose arrangement changes has no single layout, and using the
    averaged one crops a clip against a frame it was never in.
    """
    for blob in (storage.read_artifact(project_id, "regions_by_segment") or []):
        if not isinstance(blob, dict):
            continue
        if float(blob.get("start") or 0.0) <= t <= float(blob.get("end") or 0.0):
            return blob
    return storage.read_artifact(project_id, "regions") or {}


def _layout_plan(clip: ClipModel, project: ProjectModel) -> dict:
    """Use the stored plan; fall back to a centre crop if analysis never produced
    one (e.g. an alternative the user promoted by hand)."""
    src_w = int(project.width or 1920)
    src_h = int(project.height or 1080)
    if clip.layout_plan:
        if _plan_fits(clip.layout_plan, src_w, src_h):
            return clip.layout_plan
        logger.warning(
            "clip %s has a layout plan that does not fit a %dx%d source — "
            "replanning. The source has probably been replaced since scoring.",
            clip.id, src_w, src_h)
    from services.clipper import layout as layout_mod

    regions = _regions_for(project.id, (float(clip.start_time or 0.0)
                                       + float(clip.end_time or 0.0)) / 2.0)
    faces_blob = storage.read_artifact(project.id, "faces") or {}
    cfg = project.clipper_settings or {}
    return layout_mod.plan_layout(
        _candidate(clip),
        regions,
        faces_blob.get("samples") or [],
        src_w, src_h,
        mode=cfg.get("layout_mode") or "auto",
        face_pct=float(cfg.get("face_pct") or settings.clipper_face_pct),
        include_chat=bool(cfg.get("include_chat")),
        content_type=clip.content_type or effective_content_type(project),
    )


async def _dead_spans(clip: ClipModel, project: ProjectModel
                      ) -> list[tuple[float, float]]:
    """Dead seconds to cut out of the middle of this clip (§15).

    Words come from the transcript rather than the clip row: `transcript_segments`
    is not always populated, and the word timings are what veto a "silence" that
    is really someone speaking quietly.
    """
    from sqlalchemy import select as sa_select

    from models import TranscriptModel
    from services.clipper.dead_air import dead_spans

    signals = storage.read_artifact(project.id, "signals") or {}
    if not (signals.get("silence") or []):
        return []

    async with async_session() as session:
        row = (await session.execute(
            sa_select(TranscriptModel)
            .where(TranscriptModel.project_id == project.id).limit(1)
        )).scalar_one_or_none()

    start, end = float(clip.start_time or 0.0), float(clip.end_time or 0.0)
    words: list[dict] = []
    for seg in ((row.segments if row else None) or []):
        if float(seg.get("end") or 0.0) < start or float(seg.get("start") or 0.0) > end:
            continue
        words.extend(seg.get("words") or [])
    return dead_spans(_candidate(clip), signals, words)


async def _dynamic_plan(clip: ClipModel, project: ProjectModel,
                        src_w: int, src_h: int) -> dict | None:
    """A multi-shot edit for this clip, or None when the window cannot carry one.

    Returns None rather than raising: a clip that cannot be cut dynamically is
    a clip that renders as the static split screen, which is what every export
    did before this path existed. A failure here must never lose the export.
    """
    import asyncio

    from services.clipper import dynamic_edit, dynamic_window

    paths = storage.paths(project.id)
    proxy = paths["proxy"]
    if not proxy.exists():
        logger.warning("clip %s: no analysis proxy, falling back to the static "
                       "layout — the dynamic editor measures the proxy, not the "
                       "source", clip.id)
        return None

    cand = _candidate(clip)
    duration = float(cand["end"]) - float(cand["start"])
    if duration <= 0:
        return None

    signals = storage.read_artifact(project.id, "signals") or {}
    loop = asyncio.get_event_loop()
    window = await loop.run_in_executor(
        None,
        lambda: dynamic_window.analyse_window(
            proxy, float(cand["start"]), duration, None, src_w),
    )
    plan = await loop.run_in_executor(
        None,
        lambda: dynamic_edit.plan_dynamic_edit(
            cand, signals, window["faces"],
            src_w=src_w, src_h=src_h,
            proxy_w=int(signals.get("proxy_width") or 0),
            proxy_h=int(signals.get("proxy_height") or 0),
            game_motion=window["motion"], game_focus=window["focus"],
            game_detail=window["detail"], game_ui=window["ui"],
            game_motion_hop=window["hop"]),
    )
    shots = plan.get("shots") or []
    if len(shots) < 2:
        # One shot is a static crop with extra steps, and the static path does
        # that better — it keeps the face band and the chat exclusion.
        logger.info("clip %s: the dynamic editor planned %d shot(s); using the "
                    "static layout instead", clip.id, len(shots))
        return None
    plan["src_w"], plan["src_h"] = src_w, src_h
    plan["band"] = list(window["band"])
    plan["faces_seen"] = dynamic_window.face_seen(window["faces"])
    for warning in plan.get("warnings") or []:
        logger.info("clip %s dynamic edit: %s", clip.id, warning)
    return plan


async def handle_export(job_id: str, project_id: str, clip_id, metadata, queue) -> None:
    """Full-quality 1080x1920 deliverable."""
    import asyncio

    from services.clipper.render import render_clip

    if not clip_id:
        raise RuntimeError("export job started without a clip id")

    clip, project = await _load(clip_id)
    src = _source_path(project)
    cfg = project.clipper_settings or {}
    paths = storage.paths(project_id)

    await queue.update_progress(job_id, 0.05, "Rendering export")

    # §15: seconds inside the window that carry nothing. Computed before the
    # .ass, because the captions have to be written on the trimmed clock.
    drop: list[tuple[float, float]] = []
    if bool(cfg.get("trim_silence", settings.clipper_trim_silence)):
        try:
            drop = await _dead_spans(clip, project)
        except Exception:
            logger.warning("clip %s: dead-air detection failed; rendering the "
                           "window whole", clip.id, exc_info=True)
            drop = []
    if drop:
        from services.clipper.dead_air import removed_seconds

        logger.info("clip %s: cutting %d dead span(s), %.1fs total",
                    clip.id, len(drop), removed_seconds(drop))

    ass_path = _write_ass(clip, paths["exports_dir"], drop)
    plan = _layout_plan(clip, project)

    fps = cfg.get("fps")
    fps = int(project.fps or settings.clipper_export_fps) if fps == "source" else int(
        fps or settings.clipper_export_fps
    )

    # The multi-shot path. Opt-in, and it falls back to the static layout on any
    # failure — the two renderers take the same source, the same window and the
    # same .ass, so nothing else in this handler changes.
    dyn = None
    if bool(cfg.get("dynamic_edit", settings.clipper_dynamic_edit)):
        await queue.update_progress(job_id, 0.10, "Planning the shot list")
        try:
            dyn = await _dynamic_plan(clip, project,
                                      int(project.width or 1920),
                                      int(project.height or 1080))
        except Exception:
            logger.warning("clip %s: dynamic planning failed, falling back to "
                           "the static layout", clip.id, exc_info=True)
            dyn = None

    out = storage.export_path(project_id, clip.id)
    try:
        if dyn:
            from services.clipper import dynamic_render

            await queue.update_progress(
                job_id, 0.20, f"Rendering {len(dyn['shots'])} shots")
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: dynamic_render.render_dynamic_clip(
                    src, dyn, str(out), start=float(clip.start_time or 0.0),
                    work_dir=paths["exports_dir"], ass_path=ass_path,
                    src_w=int(project.width or 1920),
                    src_h=int(project.height or 1080),
                    fps=fps, crf=int(settings.clipper_export_crf),
                    preset=settings.clipper_export_preset),
            )
        else:
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
                drop_spans=drop,
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
                # Present only when the multi-shot path rendered this file. The
                # static layout_plan above is still written either way, because
                # it is what a re-render falls back to.
                "dynamic_plan": dyn,
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
