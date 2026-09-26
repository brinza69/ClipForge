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
from services.clipper import (caption_policy, dynamic_rhythm, edit_profiles,
                              layout_policy,
                              storage)
# THE canonical numeric guard, not a local copy: it rejects the infinities too,
# and a second implementation is how R2's validation hole reopened.
from services.clipper.candidate_terms import _num

# Split out at the 500-line limit and re-exported: both render handlers and the
# tests that pin today's fixes reach for these through this module.
from workers.clipper_captions import (  # noqa: F401
    _caption_faces,
    _caption_y,
    _clip_words,
    _place_burned,
    _plan_for_render,
    _write_ass,
)
# The three proposals every render records and none of it applies. Split out in
# R4, when this file passed 500 lines for the second time — and it is the part
# that has grown with every batch since R2.
from workers import clipper_shadow_views
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


def _valid_pixel_dim(value: Any) -> bool:
    """True for a non-bool int >= 2; bools/floats/strings/NaN/inf rejected without int() coercion."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 2


def _plan_fits(plan: Any, src_w: int, src_h: int) -> bool:
    """Whether a stored plan's crops belong to THIS source's frame.

    A plan is crops in source pixels, valid only for the dimensions it was
    measured in. Swap the source file — a re-download, a re-encode, an HD
    replacement of a proxy-resolution cut — and the old rects can still apply
    CLEANLY while meaning something else entirely: measured, a plan built for
    854x480 and used against 1920x1080 cropped the top-left corner as the
    "facecam" and a narrow strip as the "gameplay", and nothing complained
    because every rect was comfortably inside the frame — a 02dea6f0a9e9-shaped
    plan reads as fitting 1920x1080 by bounds alone. Only recorded identity
    tells them apart, so a plan with no recorded dimensions is never reusable:
    there is nothing to compare, and bounds already proved insufficient.
    """
    if not isinstance(plan, dict):
        return False
    if not _valid_pixel_dim(src_w) or not _valid_pixel_dim(src_h):
        return False
    plan_w, plan_h = plan.get("src_w"), plan.get("src_h")
    if not _valid_pixel_dim(plan_w) or not _valid_pixel_dim(plan_h):
        return False
    return plan_w == src_w and plan_h == src_h


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
        # An explicit reaction layout has its own validation path. A bound plan
        # that fails (stale source, expanded window, wrong dims) raises rather
        # than falling back — a silent crop substitution is worse than an error.
        if clip.layout_plan.get("game_content_fit") is True:
            from services.clipper.reaction_edit import validate_binding
            validate_binding(
                clip.layout_plan,
                _source_path(project),
                src_w, src_h,
                float(clip.start_time or 0.0),
                float(clip.end_time or 0.0),
            )
            return clip.layout_plan
        if _plan_fits(clip.layout_plan, src_w, src_h):
            return clip.layout_plan
        logger.warning(
            "clip %s has a layout plan with missing, invalid, or mismatched "
            "recorded dimensions for a %dx%d source (plan src_w=%r src_h=%r) — "
            "replanning.",
            clip.id, src_w, src_h,
            clip.layout_plan.get("src_w"), clip.layout_plan.get("src_h"))
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
                        src_w: int, src_h: int,
                        no_second_camera: bool = False) -> dict | None:
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
            stable_track=stable,
            # DECLARED, not detected. `camera_rects` builds the second camera
            # from geometry alone, so on a source with nothing but the speaker
            # in it that rectangle is the wall behind him — and none of the
            # three `alive` guards can tell a street from a stream.
            no_second_camera=no_second_camera),
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
    plan["_face_space"] = {"width": window.get("proxy_width"),
                           "height": window.get("proxy_height"),
                           "clock": "source_requested",
                           "decoded_space": window.get("face_decoded_space")}
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
    # R4's two inputs that exist nowhere else. The boundary list is computed on
    # the plan's OWN merged style, so the weights are the ones the delivered
    # planner walked — a second style here would compare the proposal against a
    # cadence nobody rendered.
    #
    # `scenes` stays None when the signals artefact never carried the key. A
    # source nobody scanned and a source that was scanned and cut nowhere are
    # different facts, and reading the first as the second is how "no scene
    # cuts" becomes evidence. Working data, popped with the rest.
    audio_block = signals.get("audio") if isinstance(signals.get("audio"), dict) else {}
    scenes = signals.get("scenes")
    rel = [round(_num(t) - float(cand["start"]), 3) for t in (scenes or [])]
    plan["_rhythm"] = {
        "boundaries": dynamic_rhythm.cut_boundaries(
            cand.get("words") or [], audio_block.get("peaks") or [], scenes or [],
            float(cand["start"]), duration, plan.get("style") or {}),
        "scenes": (None if scenes is None
                   else [t for t in rel if 0.0 < t < duration]),
        "beats_known": audio_block.get("peaks") is not None,
    }
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
    # WHAT THE SOURCE ACTUALLY HAS IN IT, declared rather than detected. The
    # second camera is built from geometry alone — "everything to the right of
    # the facecam" — so on a single-camera source it frames the wall. Measured
    # on the delivered windows: 377 of 2,016 across the corpus exclude the
    # clip's own subject, 372 of them a `game` camera, and `30d7c6d4eae5`
    # re-plans to 6.8 s of 18.1 s of that while the speaker is talking. The
    # discriminators that looked obvious are thresholds chosen on four sources
    # with the answer visible, and one of them is backwards — so this is a
    # person's or an agent's answer, the same shape `caption_policy` uses.
    layout_decision = layout_policy.decide(
        cfg.get(layout_policy.SETTING), by=cfg.get("layout_decided_by"))

    # An explicit per-clip reaction layout overrides dynamic planning, even when
    # the project has dynamic_edit=True. The user's crop selection IS the edit.
    _reaction_fit = isinstance(plan, dict) and plan.get("game_content_fit") is True
    dyn = None
    if not _reaction_fit and bool(cfg.get("dynamic_edit", settings.clipper_dynamic_edit)):
        await stage(0.10, "Planning the shot list")
        try:
            dyn = await _dynamic_plan(
                clip, project,
                int(project.width or 1920),
                int(project.height or 1080),
                no_second_camera=(layout_decision["regions"]
                                  == layout_policy.NO_SECOND_CAMERA))
        except Exception:
            logger.warning("clip %s: dynamic planning failed, falling back to "
                           "the static layout", clip.id, exc_info=True)
            dyn = None

    # The detector's verdict is NOT consulted here, and that is the point: a
    # person's answer — the clip's, else the project's — alone suppresses the
    # layer. `source_captions` is `calibrated: false` on four sources, and an
    # uncalibrated detector removing somebody's captions fails invisibly — a
    # clip ships with no text at all and nothing reports it. When it is
    # calibrated, this call gains its second argument and nothing else moves.
    caption_policy_decision = caption_policy.decide(
        cfg.get(caption_policy.SETTING),
        clip_setting=getattr(clip, "source_has_burned_captions", None))
    # D2: an alternative has no stored plan; under burn one is built for this
    # render (never stored), and a burn that cannot be built is reported. Built
    # AFTER `_dynamic_plan`: an alternative's cut grid does not get these words as
    # timing hints, as a winner's does. Not editorially equivalent (U2, open).
    clip, caption_plan_state = await _plan_for_render(
        clip, project, plan, caption_policy_decision["action"])

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

    # The three proposals every render now records and none of it applies.
    # Lifted out when this file passed 500 lines: they are computed from the
    # finished plan and written beside it, which is a different question from
    # what to render and the only part that grows with each batch.
    views = clipper_shadow_views.shadow_views(
        clip, dyn, mode=edit_mode, profile=profile["profile"])

    caption_face_placement = None
    if caption_policy_decision["action"] == caption_policy.BURN:
        caption_y, caption_face_placement = _place_burned(
            clip, project, plan, dyn, drop, caption_y, _reaction_fit)

    return {
        "cfg": cfg,
        "drop": drop,
        "creator_view": views["creator_view"],
        "regime_view": views["regime_view"],
        "rhythm_view": views["rhythm_view"],
        "edit_profile": {**profile, "mode": edit_mode,
                         "applied": edit_profiles.delivers_profile(edit_mode)},
        "plan": plan,
        "dyn": dyn,
        "fps": fps,
        "caption_y": caption_y,
        "caption_face_placement": caption_face_placement,
        # WHETHER TO BURN A LAYER AT ALL, decided before it is written. A
        # project whose source already carries burned subtitles gets none from
        # us — that is the defect 37 of 101 stored clips are rejected for and a
        # human confirmed on 4 of 4 watched. The switch is a person's; the
        # detector's verdict rides along and applies to nothing.
        "caption_policy": caption_policy_decision,
        # The plan the .ass was written from (stored or built for this render)
        # and where it came from (`_plan_for_render`); `_write_ass` below sets
        # the effective outcome in this same dict when it burns nothing.
        "caption_plan": clip.caption_plan,
        "caption_plan_state": caption_plan_state,
        # WHAT THE SOURCE HAS IN IT, on the sidecar so a later reader can tell a
        # clip planned with one camera from one planned with two — the shot
        # list alone cannot, because a plan that never chose the second camera
        # and a source that never had one look identical.
        "layout_policy": layout_decision,
        "ass_path": (_write_ass(clip, out_dir, drop, caption_y, caption_plan_state)
                     if caption_policy_decision["action"] == caption_policy.BURN
                     else None),
        "watermark": str(cfg.get("watermark_text") or ""),
    }
