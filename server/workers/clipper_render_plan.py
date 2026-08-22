"""
ClipForge — AI Stream Clipper: what a render will contain, before it runs.

Split out of `clipper_render_jobs.py` on 2026-08-18, when that file passed 750
lines against the repo's 500 limit. The seam is real rather than arbitrary:
everything here answers WHAT to render — which window, which shot list, which
layout, which caption file, which seconds to drop — and nothing here runs
ffmpeg or touches a job queue.

`_decide_render` is the entry point and the reason the split lands here. Export
and preview used to decide separately, so the editor previewed a static split
screen for a clip that shipped with nineteen cuts; they consume one answer now,
and that answer is this module.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Sequence

from config import settings
from database import async_session
from models import ClipModel, ProjectModel
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


def _caption_y(clip: ClipModel, dyn: dict | None) -> float | None:
    """Where the caption should sit given the UI panels THIS cut exposes.

    The stored `y_pct` was resolved at score time against `regions.json`, whose
    `hud` list is empty on the source where a caption demonstrably landed on the
    game UI. The panels are detected per clip and the shot list says where each
    one lands in the output frame, so both halves only exist here, at export.

    Returns None when there is nothing new to say, and the stored position
    stands.
    """
    panels = (dyn or {}).get("_panels") or []
    if not panels or not clip.caption_plan:
        return None
    if (clip.caption_plan or {}).get("y_pct_manual"):
        # Somebody moved it in the editor. Re-placing it around detected UI is
        # right when nobody has expressed a preference and wrong the moment
        # somebody has — an edit that the next export silently undoes is worse
        # than no editor at all.
        return None
    try:
        from services.clipper.captions import panels_to_keep_out, resolve_position

        keep = panels_to_keep_out(panels, (dyn or {}).get("shots") or [])
        if not keep:
            return None
        existing = ((clip.layout_plan or {}).get("safe_zones") or {}).get("keep_out") or []
        _x, y = resolve_position(
            str((clip.caption_plan or {}).get("position") or "bottom"),
            {"safe_zones": {"keep_out": list(existing) + keep}})
        return y
    except Exception:
        logger.warning("clip %s: could not re-place the caption around the UI "
                       "panels; keeping the stored position", clip.id, exc_info=True)
        return None


def _write_ass(clip: ClipModel, out_dir: Path,
               drop_spans: Sequence[tuple[float, float]] | None = None,
               y_pct: float | None = None) -> str | None:
    """Render the stored caption plan to an .ass file. Returns None when the
    clip has no captions, which is a legitimate state (the user can turn them
    off) — the render then simply skips the subtitles filter.

    `drop_spans` are the dead seconds the render is about to remove. The
    overlays have to move with them: libass positions against absolute times,
    so a caption left on the untrimmed clock drifts further out of sync with
    every second cut.

    `y_pct` overrides the stored caption height when the export found game UI
    the score-time plan could not have known about. The plan itself is left
    alone: it is a record of what was decided then, and a re-score would
    recompute it anyway.
    """
    from services.caption_overlays import build_overlays_ass
    from services.clipper.captions import caption_plan_to_overlays

    if not clip.caption_plan:
        return None
    plan = clip.caption_plan
    if y_pct is not None and abs(float(plan.get("y_pct") or 0.0) - y_pct) > 1e-4:
        logger.info("clip %s: caption moved %.3f -> %.3f to clear detected game UI",
                    clip.id, float(plan.get("y_pct") or 0.0), y_pct)
        plan = {**plan, "y_pct": y_pct}
    try:
        overlays = caption_plan_to_overlays(plan)
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

    from services.clipper import dynamic_subject

    faces_all = storage.read_artifact(project.id, "faces") or {}
    stable = dynamic_subject.stable_track(
        faces_all.get("samples") if isinstance(faces_all, dict) else faces_all)
    if stable:
        logger.info("clip %s: framing on the source's fixed subject "
                    "(spread %.1fpx vs %.1f for the next cluster)",
                    clip.id, stable["spread"], stable["runner_up"])

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
            game_motion_hop=window["hop"],
            # Computed on the WHOLE-source track, not this window's. A fixed
            # webcam overlay is only recognisable against hours of material —
            # inside one 40-second window it looks like any other cluster.
            stable_track=stable),
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
    # Handed to Pass D so it does not decode the window a second time, and
    # popped before the sidecar is written — the track is dozens of samples of
    # box coordinates and belongs in neither the plan nor the deliverable.
    plan["_review_faces"] = window["faces"]
    plan["_panels"] = window.get("panels") or []
    for warning in plan.get("warnings") or []:
        logger.info("clip %s dynamic edit: %s", clip.id, warning)
    return plan



async def _decide_render(clip, project, out_dir, *, on_stage=None) -> dict:
    """Everything that decides WHAT a render contains, for either consumer.

    One decision, two callers, and that is the entire reason this exists rather
    than being inlined twice. Until 2026-08-17 `handle_preview` took the static
    renderer unconditionally while `handle_export` could take the multi-shot
    one, so a person approved a fixed split screen and received an edit with a
    dozen cuts in it — and the preview also ignored `trim_silence`, the
    watermark, and the caption height the export would resolve. A preview that
    composes differently from the final render is worse than no preview;
    `render_preview` says so in its own docstring about the static pair, and
    the multi-shot path quietly broke the promise.

    Returns the shot plan (or None), the static layout, the dead-air spans, the
    .ass written on the trimmed clock, and the options both renderers take.
    """
    cfg = project.clipper_settings or {}

    async def stage(pct: float, message: str) -> None:
        if on_stage is not None:
            await on_stage(pct, message)

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

    plan = _layout_plan(clip, project)

    fps = cfg.get("fps")
    fps = int(project.fps or settings.clipper_export_fps) if fps == "source" else int(
        fps or settings.clipper_export_fps
    )

    # The multi-shot path. It falls back to the static layout on any failure —
    # the two renderers take the same source, the same window and the same
    # .ass, so nothing else downstream changes.
    #
    # It runs BEFORE the .ass is written, which it did not use to. The shot list
    # is what says where a detected UI panel lands in the output frame, and the
    # caption has to be placed knowing that — the whole point of detecting the
    # panels. Writing the captions first meant placing them blind.
    dyn = None
    if bool(cfg.get("dynamic_edit", settings.clipper_dynamic_edit)):
        await stage(0.10, "Planning the shot list")
        try:
            dyn = await _dynamic_plan(clip, project,
                                      int(project.width or 1920),
                                      int(project.height or 1080))
        except Exception:
            logger.warning("clip %s: dynamic planning failed, falling back to "
                           "the static layout", clip.id, exc_info=True)
            dyn = None

    # Resolved once and given to BOTH the .ass and the review. Computing it
    # twice, or letting the review read the stored plan, means Pass D judging a
    # caption position the render did not use — it would go on reporting a
    # caption it had already caused to move.
    caption_y = _caption_y(clip, dyn)
    return {
        "cfg": cfg,
        "drop": drop,
        "plan": plan,
        "dyn": dyn,
        "fps": fps,
        "caption_y": caption_y,
        "ass_path": _write_ass(clip, out_dir, drop, caption_y),
        "watermark": str(cfg.get("watermark_text") or ""),
    }
