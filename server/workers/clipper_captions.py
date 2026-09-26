"""What the burned captions say, where they sit, and on which clock.

Split out of `clipper_render_plan.py` when Batch R3a took it past the repo's
500-line limit. The seam is the one the file already had: everything here is
about the caption track — the words on the clip's clock, the height the export
chooses when it finds game UI the score-time plan could not have known about,
and the .ass the renderer burns. Nothing here decides what is on screen.

Re-exported from `clipper_render_plan`, so every existing import still works.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence

from database import async_session
from models import ClipModel
from services.clipper import caption_policy
# The canonical transcript read — the one the editor's "regenerate captions"
# uses — so a plan built at render time sees the words a rebuilt one would.
from services.clipper.clip_mutations import _project_transcript

logger = logging.getLogger("clipforge.clipper.render")

#: Every `clip.warnings` entry this module writes starts with this, so the next
#: render can take back its own report without touching the layout's warnings.
CAPTION_WARNING = "Captions not burned"
_NOT_BURNED_WHY = {
    "no_transcript": "this project has no transcript",
    "no_timed_words": "no timed words in this clip's window",
    "incomplete_timing": "a word in this clip's window has no complete start and end",
    "empty_plan": "the words in this window produced no caption text",
    "build_failed": "the caption plan could not be built",
    "transcript_unreadable": "the transcript could not be read",
    "unreadable_plan": "the stored caption plan is not a plan",
    "overlays_failed": "the caption plan could not be turned into caption events",
    "all_captions_in_removed_time": "every caption fell in the dead air this render cut",
}


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


def _caption_faces(clip, project, dyn, caption_y):
    """Refine the render-time position using local faces; keep manual edits."""
    from services.clipper.caption_faces import place
    from services.clipper.captions import _norm_rect, panels_to_keep_out

    if not clip.caption_plan:
        return caption_y, None
    try:
        # Static split-screen head rectangles do not describe a dynamic crop.
        # Retain other reserved areas and the existing panel avoidance policy.
        keep = [r for r in (((clip.layout_plan or {}).get("safe_zones") or {})
                            .get("keep_out") or []) if r.get("kind") != "face"]
        keep += panels_to_keep_out((dyn or {}).get("_panels") or [],
                                  (dyn or {}).get("shots") or [])
        normalized = [_norm_rect(r, 1080, 1920) for r in keep]
        keep = [{"y": r[1] * 1920, "h": (r[3] - r[1]) * 1920}
                for r in normalized if r is not None]
        current = (caption_y if caption_y is not None
                   else float(clip.caption_plan.get("y_pct", .75)))
        report = place(clip.caption_plan, dyn, start=float(clip.start_time),
                       src_w=project.width, src_h=project.height,
                       current_y=current, keep_out=keep)
        return (report["y_pct"] if report["applied"] else caption_y), report
    except (TypeError, ValueError, KeyError):
        logger.warning("clip %s: unreadable caption/face observations", clip.id,
                       exc_info=True)
        return caption_y, {"applied": False, "coverage_complete": False,
                           "reason": "unreadable_observations"}


def _write_ass(clip: ClipModel, out_dir: Path,
               drop_spans: Sequence[tuple[float, float]] | None = None,
               y_pct: float | None = None, state: dict | None = None) -> str | None:
    """Render the stored caption plan to an .ass file. Returns None when the
    clip has no captions, which is a legitimate state (the user can turn them
    off) — the render then simply skips the subtitles filter.

    `state` is the render's `caption_plan_state`. A `burn` that writes nothing
    is corrected IN PLACE to what happened (D2r K2), so the plan stays the
    intended one and the state says the effective result: `empty_after_remap`
    when the dead-air remap removed every event (some removed is still `burn`),
    `empty` when the chunks carry no text, `unavailable` when they could not be
    turned into events.

    `drop_spans` are the dead seconds the render is about to remove. The
    overlays have to move with them: libass positions against absolute times,
    so a caption left on the untrimmed clock drifts further out of sync with
    every second cut.

    `y_pct` overrides the stored caption height when the export found UI or
    local faces the score-time plan could not have known about. The plan is left
    alone: it is a record of what was decided then, and a re-score would
    recompute it anyway.
    """
    from services.caption_overlays import build_overlays_ass
    from services.clipper.captions import caption_plan_to_overlays

    if not clip.caption_plan:
        return None
    plan = clip.caption_plan
    if y_pct is not None and abs(float(plan.get("y_pct") or 0.0) - y_pct) > 1e-4:
        logger.info("clip %s: caption moved %.3f -> %.3f around detected content",
                    clip.id, float(plan.get("y_pct") or 0.0), y_pct)
        plan = {**plan, "y_pct": y_pct}
    def settled(outcome: str, reason: str) -> None:
        if state is not None and state.get("outcome") == "burn":
            state.update(outcome=outcome, reason=reason)
            logger.warning("clip %s: burn requested, nothing burned — %s", clip.id, reason)

    try:
        overlays = caption_plan_to_overlays(plan)
    except Exception:
        logger.warning("could not turn the caption plan into overlays", exc_info=True)
        settled("unavailable", "overlays_failed")
        return None
    if not overlays:
        settled("empty", "no_caption_text")
        return None
    if drop_spans:
        from services.clipper.dead_air import remap_overlays

        overlays = remap_overlays(overlays, drop_spans)
    if not overlays:
        settled("empty_after_remap", "all_captions_in_removed_time")
        return None

    out_dir.mkdir(parents=True, exist_ok=True)
    ass_path = out_dir / f"{clip.id}.ass"
    # The ASS canvas is the OUTPUT canvas: libass positions against PlayRes, and
    # the plan's x_pct/y_pct were resolved against 1080x1920 safe zones.
    build_overlays_ass(overlays, 1080, 1920, str(ass_path))
    return str(ass_path)


def _place_burned(clip, project, plan, dyn, drop, caption_y, _reaction_fit):
    """The height a BURNED layer sits at: reaction fit first, then local faces.

    Moved out of `_decide_render` unchanged (D2, at that file's 500 lines); it
    runs only inside the burn decision, so a suppressed layer never triggers
    the placement helpers.
    """
    # Reaction fit: resolve inside the burn decision so a suppressed layer
    # never triggers the placement helper. Pass drop spans so all-removed
    # overlays return None rather than a stale position.
    if _reaction_fit and caption_y is None:
        _cp = clip.caption_plan if isinstance(clip.caption_plan, dict) else None
        if _cp and not _cp.get("y_pct_manual"):
            from services.clipper.reaction_captions import resolve_reaction_caption_y
            _clip_dur = (float(clip.end_time or 0.)
                         - float(clip.start_time or 0.))
            try:
                _resolved = resolve_reaction_caption_y(
                    plan, _cp, drop_spans=drop, clip_duration=_clip_dur)
            except ValueError as exc:
                raise RuntimeError(
                    f"clip {clip.id}: reaction caption placement failed — {exc}"
                ) from exc
            if _resolved is not None:
                caption_y = _resolved
    caption_y, caption_face_placement = _caption_faces(clip, project, dyn, caption_y)
    return caption_y, caption_face_placement


class _WithPlan:
    """The clip as ONE render sees it: the row, plus a plan built for it.

    Never the row itself. The preview-frame route hands `_decide_render` a clip
    attached to its request session, and setting `caption_plan` there is one
    autoflush away from storing a machine plan as if a person had saved it.
    """

    def __init__(self, clip, caption_plan: dict):
        self._clip, self.caption_plan = clip, caption_plan

    def __getattr__(self, name):
        return getattr(self._clip, name)


def _window_timing(transcript: dict, start: float, end: float) -> str | None:
    """None when every word the builder would read here has a real clock, else
    why not: `no_timed_words` (none at all) or `incomplete_timing` (some).

    Checked here because the builder never says "none": a segment with no word
    timings gets its text spread evenly over its span, and with none in the
    window at all the clip's own text is spread over the whole clip — caption
    tracks nobody said on that clock. ONE timed word was not enough (D2r K1,
    Codex's counterexample): `real [0.1,0.5]` beside an untimed `invented clock`
    segment [1,3] shipped `invented [1,2]`, `clock [2,3]`. So every word of
    every segment the builder reads (its own overlap test, `_clip_words`) needs
    a finite start and end with end >= start; a missing end is incomplete, not
    "zero length". `end == start` is admitted (D2r-2, closure-3 §1 A): it is a
    point timestamp the ASR gave, not a demonstrated duration and not a gap —
    refusing it refused 3,417 of 6,813 real alternatives while the winners burn
    the same words. A word is COUNTED only by the builder's own word test
    (`we > c_start and ws < c_end`), so a point exactly at the clip's start or
    end, which the builder drops, never admits a window on its own. How a burst
    of points DISPLAYS is the builder's separate lot. The builder itself is
    unchanged: finalize and "regenerate captions" keep its fallback, and a
    stored plan is never checked here.
    """
    import math

    from services.clipper.captions import _f

    timed, incomplete = 0, False
    for seg in transcript.get("segments") or []:
        seg = seg or {}
        if _f(seg.get("end")) <= start or _f(seg.get("start")) >= end:
            continue                       # the builder does not read it either
        words = seg.get("words") or []
        if not words and (seg.get("text") or "").split():
            incomplete = True              # the builder would spread this text
        for word in words:
            if not isinstance(word, dict):
                incomplete = True
                continue
            if not str(word.get("word") or word.get("text") or "").strip():
                continue                   # no token: nothing burned for it
            try:
                w0, w1 = float(word["start"]), float(word["end"])
            except (KeyError, TypeError, ValueError):
                incomplete = True
                continue
            if not (math.isfinite(w0) and math.isfinite(w1) and w1 >= w0):
                incomplete = True
            elif w1 > start and w0 < end:
                timed += 1
    if not timed:
        return "no_timed_words"
    return "incomplete_timing" if incomplete else None


async def _plan_for_render(clip, project, layout: dict, action: str):
    """`(clip, state)` — the caption plan this render uses, and where it came from.

    D2. `_write_clips` stores a plan only for planned clips, so an alternative
    reaches export with `caption_plan: None`, and under `burn` it shipped with
    no captions and nothing said so. Missing is built here, with the builder
    finalize and "regenerate captions" use, for THIS render only: nothing is
    written to the row, so it cannot pass for a person's edit, a rescore sees
    the row it always saw, and preview and export — which both call
    `_decide_render` — build the same plan from the same inputs.

    `state` = `{"origin", "outcome", "reason"}`:
      origin  "stored" | "built" | None (no plan used)
      outcome "burn" | "empty" (a stored plan with no chunks — an answer, not
              a gap) | "suppressed" (policy; nothing built) | "unavailable"
              (burn asked for, no plan could be had — `reason` says why)
              | "empty_after_remap", set later by `_write_ass` (D2r K2)
    A stored plan of any shape is never replaced, whoever wrote it.
    """
    stored = clip.caption_plan
    if action != caption_policy.BURN:
        return clip, {"origin": "stored" if isinstance(stored, dict) else None,
                      "outcome": "suppressed", "reason": None}
    if isinstance(stored, dict):
        return clip, {"origin": "stored",
                      "outcome": "burn" if stored.get("chunks") else "empty",
                      "reason": None}

    def unavailable(reason: str):
        logger.warning("clip %s: burn requested, no caption plan — %s", clip.id, reason)
        return clip, {"origin": None, "outcome": "unavailable", "reason": reason}

    if stored is not None:
        return unavailable("unreadable_plan")
    try:
        async with async_session() as session:
            transcript = await _project_transcript(session, project.id)
    except Exception:
        logger.warning("clip %s: transcript read failed", clip.id, exc_info=True)
        return unavailable("transcript_unreadable")
    if not transcript.get("segments"):
        return unavailable("no_transcript")
    start, end = float(clip.start_time or 0.0), float(clip.end_time or 0.0)
    gap = _window_timing(transcript, start, end)
    if gap:
        return unavailable(gap)

    from services.clipper.captions import build_caption_plan

    cfg = project.clipper_settings or {}
    try:
        # The arguments "regenerate captions" passes (routers/clipper_clips.py),
        # which are finalize's plus the clip's own preset choice.
        built = build_caption_plan(
            {"start": start, "end": end, "text": clip.transcript_text or ""},
            transcript,
            preset_id=clip.caption_preset_id or cfg.get("caption_preset_id") or "bold_impact",
            max_words=3,
            position=cfg.get("caption_position") or "bottom",
            layout=layout or {},
        )
    except Exception:
        logger.warning("clip %s: caption planning failed", clip.id, exc_info=True)
        return unavailable("build_failed")
    if not built.get("chunks"):
        return unavailable("empty_plan")
    return _WithPlan(clip, built), {"origin": "built", "outcome": "burn", "reason": None}


def _caption_warnings(existing, state: dict | None, render: str = "render") -> list | None:
    """`existing` (the CURRENT `clip.warnings`) with this render's caption report
    in place of the last one; None = leave the value alone.

    None for a legacy value that is not a list of str (D2r K4; D2r-2 K8 adds the
    list with a non-str element): iterating a dict gives its keys, a string its
    characters, and `str()` of a mixed list's elements turns a number into
    "text" — none of it a warning anybody wrote. It is left as it is and
    logged, never rewritten. `null` is no warnings.

    The report is labelled as `render`'s result (D2r K3). It is not taken back
    when a person edits the clip — nothing but a render can say whether the
    edit burns — so it says which render it describes, and the next render
    replaces it.
    """
    if existing is not None and not (isinstance(existing, list)
                                     and all(isinstance(w, str) for w in existing)):
        logger.warning("clip warnings are a %s, not a list of text; left as they are",
                       type(existing).__name__)
        return None
    kept = [w for w in (existing or []) if not w.startswith(CAPTION_WARNING)]
    outcome = (state or {}).get("outcome")
    if outcome in ("unavailable", "empty_after_remap"):
        reason = state.get("reason")
        why = _NOT_BURNED_WHY.get(reason, str(reason))
        kept.append(f"{CAPTION_WARNING} ({reason if outcome == 'unavailable' else outcome}): "
                    f"{why}, although the caption policy says burn. Result of the last "
                    f"{render}; an edit since then is checked by the next render.")
    return kept


def _caption_inputs(clip, project) -> tuple:
    """What a render's caption report was decided from (D2r K3): the clip's
    caption fields and window, and the project's settings (policy, preset,
    position, dead-air trim). The preview's full input list extends this one
    (`clipper_render_jobs._preview_inputs`, D2r-2 K7).

    The transcript ROW is not in it, by assumption (D2r-2 K9, closure-3 §4): the
    transcript is taken as immutable after analysis. No clipper path that
    rewrites it was found; none was proved absent, and no mechanism is added.
    A path that does rewrite it has to invalidate renders itself."""
    return (clip.caption_plan, clip.caption_preset_id, clip.start_time, clip.end_time,
            clip.transcript_text, clip.source_has_burned_captions, clip.layout_plan,
            (project.clipper_settings if project is not None else None))
