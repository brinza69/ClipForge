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
from services.clipper import dynamic_regimes, dynamic_subject, edit_profiles, storage
# THE canonical numeric guard, not a local copy: it rejects the infinities too,
# and a second implementation is how R2's validation hole reopened.
from services.clipper.candidate_terms import _num

# Split out at the 500-line limit and re-exported: both render handlers and the
# tests that pin today's fixes reach for these through this module.
from workers.clipper_captions import (  # noqa: F401
    _caption_y,
    _clip_words,
    _write_ass,
)
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
        "words": _clip_words(clip),
    }


def _speech_share(words, clip_start: float, t0: float, t1: float) -> float:
    """How much of one interval a word covers, on the CLIP's clock.

    A share rather than a flag, so `speech_ratio_on` means something. The
    boolean it replaced made the threshold decorative: any overlap at all read
    as speech, and the setting was read, persisted and passed without ever
    changing an answer.
    """
    span = max(1e-6, t1 - t0)
    # The UNION of the overlaps, not their sum. Two words that overlap each
    # other would otherwise count the same instant twice — clamped to 1.0, so
    # the error hides, and the 58 pilot clips happen to have no overlapping
    # words at all. A measurement that is only right because the data is tidy is
    # not a measurement.
    spans: list[tuple[float, float]] = []
    for word in words or []:
        ws = _num(word.get("start")) - clip_start
        we = _num(word.get("end"), ws) - clip_start
        lo, hi = max(ws, t0), min(we, t1)
        if hi > lo:
            spans.append((lo, hi))
    covered = 0.0
    end = t0
    for lo, hi in sorted(spans):
        lo = max(lo, end)
        if hi > lo:
            covered += hi - lo
            end = hi
    return min(1.0, covered / span)


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
    # What the PLANNER decided, not what survived the merge. A clip whose only
    # fault was an invisible cut — two `fit` shots delivering one image — comes
    # back from `merge_equivalent_shots` as a single shot, and counting that
    # would drop it onto the static path: different crop, different captions,
    # different renderer version, for a change that was supposed to remove one
    # redundant command and nothing else.
    planned = int(plan.get("shot_count_before_merge") or len(shots))
    if planned < 2:
        # One shot is a static crop with extra steps, and the static path does
        # that better — it keeps the face band and the chat exclusion.
        logger.info("clip %s: the dynamic editor planned %d shot(s); using the "
                    "static layout instead", clip.id, planned)
        return None
    plan["src_w"], plan["src_h"] = src_w, src_h
    plan["band"] = list(window["band"])
    plan["faces_seen"] = dynamic_window.face_seen(window["faces"])
    # Handed to Pass D so it does not decode the window a second time, and
    # popped before the sidecar is written — the track is dozens of samples of
    # box coordinates and belongs in neither the plan nor the deliverable.
    plan["_review_faces"] = window["faces"]
    # The anchor the planner framed on, handed over rather than recomputed:
    # `_decide_render` would otherwise read `faces.json` a second time and could
    # disagree with the plan it is describing. Popped before the sidecar with
    # the rest of the working data.
    plan["_stable_track"] = stable
    # The motion series, at the face track's own rate. R3b normalises it the way
    # the planner does and decides the regime per sample; without it the regime
    # could only be weighted across a shot, which is the majority vote the plan
    # forbids. Working data, popped with the rest.
    plan["_motion"] = list(window.get("motion") or [])
    # The REAL step the motion series is on, which on a 10 FPS proxy is 0.2s
    # against the 0.25s that was asked for. Recording the wrong one made every
    # value describe a different moment from the one it classified.
    plan["_motion_hop"] = float(window.get("motion_hop") or window.get("hop") or 0.25)
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
    # Resolved on every render, applied on none of them yet. In
    # `content_aware_shadow` this is the whole of R2: the profile becomes
    # observable beside every export while the delivered plan stays exactly what
    # it was, so the two can be compared without anyone's clip changing.
    #
    # Deliberately NOT part of the render fingerprint. The fingerprint covers
    # what changes the picture, and in shadow this changes nothing; adding it
    # now would invalidate every stamped export for a field with no effect. The
    # batch that makes the profile apply is the one that adds it.
    # `available_mode`, not `resolve_mode`: config.py and a hand-edited settings
    # row are two more doors into this setting, and the router's refusal only
    # guards the third. A rig set to `content_aware` used to produce
    # `applied: true` on every render while no new grammar was connected.
    edit_mode = edit_profiles.available_mode(cfg, default=settings.clipper_edit_mode)
    profile = edit_profiles.resolve(
        clip.content_type, clip.content_confidence, clip.content_type_origin)

    # R3a, recorded and not applied: what each shot's composition would be if
    # only the CREATOR counted as a subject. The delivered plan is untouched —
    # `legacy_dynamic` stays frozen and the shadow export must stay byte-for-byte
    # what it was, or R2's contract is broken.
    creator_view = regime_view = None
    if dyn:
        faces = dyn.get("_review_faces") or []
        stable = dyn.get("_stable_track")
        creator_view = dynamic_subject.proposed_compositions(
            dyn.get("shots") or [], faces, stable)
        # R3b, recorded beside it: what each stretch IS, and how many of those
        # boundaries the viewer would actually see. Same hop as the proposal
        # above, taken from it rather than recomputed, so the two describe the
        # same timeline.
        hop = float(creator_view["sample_hop_s"])
        # Speech as the SHARE of each interval covered by a word, so the
        # threshold the style already names actually applies. A boolean per
        # sample ignored `speech_ratio_on` entirely, which meant the setting was
        # read, stored, passed and never used.
        words = _clip_words(clip)
        start = float(clip.start_time or 0.0)
        duration = float(clip.duration or 0.0)
        limit = dynamic_regimes.interval_count(duration, hop)
        # The last interval ends at the CLIP, not one hop past it: a word
        # covering 4.0-4.1s of a 4.1s clip covers all of the final interval, and
        # measuring it against 4.0-4.25 reported 40%.
        speech = [_speech_share(words, start, i * hop, min((i + 1) * hop, duration))
                  for i in range(limit)] if words else None
        # RESAMPLED FIRST. The motion series is taken in whole frames, so on a
        # 10 FPS proxy a 0.25s request lands on a 0.2s grid; normalising it and
        # indexing by the face clock read every value from the wrong moment.
        motion_hop = float(dyn.get("_motion_hop") or hop)
        binned = dynamic_regimes.resample(dyn.get("_motion") or [],
                                          from_hop=motion_hop, to_hop=hop,
                                          intervals=limit)
        scaled, had_spread = dynamic_regimes.normalise_series(
            [v for v in binned if v is not None])
        # Put the gaps back where they were: `normalise_series` only sees the
        # measured bins, and an unmeasured one must stay unmeasured.
        measured = iter(scaled)
        motion = [next(measured) if v is not None else None for v in binned]
        # Two independent axes. Coverage is how much of the timeline the series
        # reaches; variability is whether it has any spread to scale against. A
        # single status could not say "complete but flat", and that is a real
        # state — a static screen measured end to end.
        measured_bins = [v for v in motion if v is not None]
        coverage = (dynamic_regimes.COVERAGE_UNAVAILABLE if not measured_bins
                    else dynamic_regimes.COVERAGE_PARTIAL
                    if len(measured_bins) < len(motion)
                    else dynamic_regimes.COVERAGE_COMPLETE)
        variability = (dynamic_regimes.VARIABILITY_UNAVAILABLE if not measured_bins
                       else dynamic_regimes.VARIABILITY_VARIABLE if had_spread
                       else dynamic_regimes.VARIABILITY_FLAT)
        regime_view = dynamic_regimes.regime_view(
            creator=dynamic_subject.creator_presence(faces, stable, hop=hop),
            others=dynamic_subject.off_anchor_presence(faces, stable, hop=hop),
            action=motion or None, speech=speech, hop=hop, duration=duration,
            style=dyn.get("style"), shots=dyn.get("shots") or [],
            coverage=coverage, variability=variability,
            # NO WORDS IS NOT SILENCE. `_clip_words` returns [] when the clip has
            # no caption plan, and reading that as "nobody spoke" turned real
            # speech into `visual_evidence`.
            speech_known=bool(words),
            motion_hop=motion_hop or None)

    return {
        "cfg": cfg,
        "drop": drop,
        "creator_view": creator_view,
        "regime_view": regime_view,
        "edit_profile": {**profile, "mode": edit_mode,
                         "applied": edit_profiles.delivers_profile(edit_mode)},
        "plan": plan,
        "dyn": dyn,
        "fps": fps,
        "caption_y": caption_y,
        "ass_path": _write_ass(clip, out_dir, drop, caption_y),
        "watermark": str(cfg.get("watermark_text") or ""),
    }
