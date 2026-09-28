"""
ClipForge Worker — AI Stream Clipper: per-clip caption-source declaration.

PUT /api/clipper/clips/{clip_id}/caption-source lets a person say, per CLIP,
whether the SOURCE already carries burned subtitles — see
services/clipper/caption_policy.py for why that is a human's call and not the
detector's. PRP: PRPs/clipper-clip-caption-source-2026-09-24.md.

`apply_project_answer` is the same decision for the PROJECT's answer, called by
PATCH /projects/{id}/settings (B2, PRPs/clipper-master-plan-2026-09-24.md §5);
it lives here so `_place_or_refuse` stays the one placement check.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from models import ClipModel, ClipStatus, ProjectModel
from services.clipper import caption_policy
from services.clipper import feedback as feedback_mod
from services.clipper.clip_mutations import lock_clip
from services.clipper.serialize import clip_to_dict, invalidate_render

router = APIRouter(prefix="/api/clipper", tags=["clipper"])

FIELD = "source_has_burned_captions"


def _err(status: int, code: str, message: str, **extra) -> HTTPException:
    return HTTPException(status, {"error": code, "message": message, **extra})


# RX1 (codex-verdict-next-36 §3.4): a build that could not READ its inputs is a failed check.
_CHECK_FAILED = ("transcript_unreadable", "build_failed", "unreadable_plan")


async def plan_to_place(clip: ClipModel, project: ProjectModel, layout: Any) -> tuple[Any, dict]:
    """`(plan, placement)` for a burned layer over `layout`, when no readable plan is stored.

    RX1 (codex-verdict-next-36 §3). The render builds such a clip's plan itself
    (`_plan_for_render`) and places THAT, so it is the one checked: checking only
    a stored plan answered 200 for framings whose every export was then refused
    (RSK, 3 of 3). It comes back for the check and is never written — it must
    not pass for a person's edit. A build that could not read its inputs is
    refused as a failed check, never read as "nothing to burn". Missing word
    timing, which the render reports as an unavailable layer, returns no plan
    and `verified: False` with the reason, so the answer can say nothing was
    checked.
    """
    from workers.clipper_captions import _plan_for_render
    seen, state = await _plan_for_render(clip, project, layout, caption_policy.BURN)
    if state["reason"] in _CHECK_FAILED:
        raise _err(422, "caption_check_failed",
                   f"ClipForge's captions for this clip could not be checked ({state['reason']}); "
                   "nothing was saved.", reason=state["reason"])
    if state["outcome"] == "unavailable":
        return None, {"verified": False, "reason": state["reason"]}
    return seen.caption_plan, {"verified": True, "reason": None}


async def _place_or_refuse(clip: ClipModel, project: ProjectModel) -> tuple[Any, dict | None]:
    """The reaction PUT's caption check, for an answer that turns the layer ON.

    A reaction framing with no caption slot is accepted while the layer is off,
    because nothing is placed. Turning the layer on over it used to return 200
    and then fail every preview and export, so it is refused here with the
    content height that would leave room; an accepted one stores the resolved
    position, as the reaction PUT does. Non-reaction layouts and manual
    positions come back unchanged — the resolver returns None for them.

    `(plan to store, placement)`. RX1: a reaction framing with no readable plan
    stored is checked with the plan its render builds (`plan_to_place`), and
    nothing is stored for it; `placement` is that check's report, None when a
    stored plan decided.
    """
    from services.clipper.reaction_captions import (
        NoCaptionGap, caption_ready_height, resolve_reaction_caption_y)
    plan, cap = clip.layout_plan, clip.caption_plan
    placement = None
    if (not isinstance(cap, dict) and isinstance(plan, dict)
            and plan.get("game_content_fit") is True):
        cap, placement = await plan_to_place(clip, project, plan)
        if cap is None:
            return clip.caption_plan, placement
    duration = float(clip.end_time or 0) - float(clip.start_time or 0)
    try:
        y = resolve_reaction_caption_y(plan, cap, clip_duration=duration)
    except NoCaptionGap as exc:
        h = caption_ready_height(plan["game_rect"], plan["face_rect"], plan["src_w"],
                                 plan["src_h"], cap, face_pct=plan["face_pct"],
                                 clip_duration=duration)
        hint = (f" At this width ({plan['game_rect']['w']} px), a content height of at "
                f"most {h} px leaves room — reframe the reaction first." if h else "")
        raise _err(422, "caption_placement_failed",
                   "This reaction framing leaves no room for ClipForge captions. "
                   + str(exc) + hint, max_content_height=h) from exc
    except ValueError as exc:
        raise _err(422, "caption_placement_failed", str(exc)) from exc
    if cap is not clip.caption_plan:        # RX1: a built plan is checked, never stored
        return clip.caption_plan, placement
    return (cap if y is None else {**cap, "y_pct": y}), placement


@router.put("/clips/{clip_id}/caption-source")
async def put_caption_source(
    clip_id: str,
    payload: dict,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Body: `{"source_has_burned_captions": true|false|null}`.

    Strict on purpose — a 1 or a "true" is never coerced, because a client
    silently accepted as a caption suppressor is the same invisible failure
    caption_policy.py exists to keep a detector from causing.
    """
    if FIELD not in payload:
        raise _err(422, "missing_field", f"{FIELD} is required.")
    value = payload[FIELD]
    if value is not None and not isinstance(value, bool):
        raise _err(422, "invalid_value", f"{FIELD} must be true, false, or null.")

    clip = await lock_clip(session, clip_id)
    if not clip:
        raise _err(404, "clip_not_found", "That clip no longer exists.")
    if clip.status == ClipStatus.exporting.value:
        raise _err(409, "export_in_progress",
                   "Wait for this export to finish before editing.")

    project = await session.get(ProjectModel, clip.project_id)
    old = clip.source_has_burned_captions
    if old == value:
        return {"clip": clip_to_dict(clip, project)}

    setting = ((project.clipper_settings if project else None) or {}).get(caption_policy.SETTING)
    layer = getattr(clip, "caption_layer", None)
    _refuse_unexecutable_burn(clip, value if isinstance(value, bool) else setting)
    before = caption_policy.decide(setting, clip_setting=old, layer=layer)
    decision = caption_policy.decide(setting, clip_setting=value, layer=layer)
    caption_plan, placement = clip.caption_plan, None
    if decision["action"] == caption_policy.BURN:
        caption_plan, placement = await _place_or_refuse(clip, project)

    clip.source_has_burned_captions = value
    if caption_plan is not clip.caption_plan:
        clip.caption_plan = caption_plan
    if decision != before:  # SC3: a layer chosen for the clip renders the same whatever the answer
        invalidate_render(clip)
    await feedback_mod.add(
        session, clip.id, clip.project_id, "caption_changed",
        {"field": FIELD, "old": old, "new": value},
        origin=feedback_mod.ORIGIN_MANUAL,
    )
    await session.commit()
    return {"clip": clip_to_dict(clip, project), "caption_placement": placement}


async def apply_project_answer(
    session: AsyncSession, project: ProjectModel, old: Any, new: bool | None
) -> dict:
    """The project's answer moving from `old` (as stored) to `new` (validated).

    Called by PATCH settings under the write lock it took before reading, and
    before it writes anything: every refusal below leaves the whole request
    unwritten. The clips it touches are the ones with NO answer of their own
    whose render decision actually flips — `decide()` on the project value
    alone is what an inheriting clip renders with, so None -> False (burn ->
    burn) is a real change that moves no clip. Clips are invalidated, not
    re-rendered; their MP4s stay on disk. No person's feedback event: a project
    change is not a person's edit of each clip, and an event would also make
    every such clip survive the next rescore as if it had been hand-edited. The
    one event is the system's `export_invalidated`, on a clip that had an export.
    """
    out = {"changed": old is not new, "affected_clip_ids": [],
           "invalidated_clip_ids": [], "export_cleared_clip_ids": []}
    inheriting = (await session.execute(
        select(ClipModel)
        .where(ClipModel.project_id == project.id)
        .where(ClipModel.source_has_burned_captions.is_(None))
        .order_by(ClipModel.id)
    )).scalars().all()
    # SC3 (codex-verdict-next-34 R4): a clip's own layer choice decides its layer, so only the clips
    # whose EFFECTIVE action flips are moved; and a chosen burn without a blur that the new answer would
    # put over source text refuses the whole change, nothing written.
    blocked = []
    for clip in inheriting:
        try:
            _refuse_unexecutable_burn(clip, new)
        except HTTPException:
            blocked.append({"clip_id": clip.id, "title": clip.title})
    if blocked:
        raise _err(422, "burn_over_untreated_source_captions",
                   f"{len(blocked)} clip(s) burn ClipForge's captions by choice without blurring the "
                   "source; this answer would put them over the source's text. Change their caption "
                   "layer first; nothing was changed.", blocking_clips=blocked)
    clips = [c for c in inheriting
             if caption_policy.decide(old, layer=getattr(c, "caption_layer", None))["action"]
             != caption_policy.decide(new, layer=getattr(c, "caption_layer", None))["action"]]
    if not clips:
        return out
    new_action = caption_policy.decide(new)["action"]

    busy = [c.id for c in clips if c.status == ClipStatus.exporting.value]
    if busy:
        raise _err(409, "export_in_progress",
                   "Wait for these exports to finish before changing the project's "
                   "caption source.", clip_ids=busy)

    placed, blocking, failed, unverified = {}, [], [], []
    if new_action == caption_policy.BURN:
        for clip in clips:  # an explicit layer never flips, so every moved clip follows the answer
            try:
                placed[clip.id], placement = await _place_or_refuse(clip, project)
            except HTTPException as exc:
                if exc.detail.get("error") == "caption_check_failed":   # RX1: not a missing slot
                    failed.append({"clip_id": clip.id, "title": clip.title,
                                   "reason": exc.detail.get("reason")})
                    continue
                blocking.append({"clip_id": clip.id, "title": clip.title,
                                 "max_content_height": exc.detail.get("max_content_height")})
                continue
            if placement and not placement["verified"]:
                unverified.append({"clip_id": clip.id, "reason": placement["reason"]})
    if failed:
        raise _err(422, "caption_check_failed",
                   f"ClipForge's captions could not be checked for {len(failed)} clip(s); "
                   "nothing was changed.", blocking_clips=failed)
    if blocking:
        raise _err(422, "caption_placement_failed",
                   f"{len(blocking)} clip(s) have a reaction framing that leaves no room "
                   "for ClipForge captions. Reframe them first, or give them their own "
                   "answer; nothing was changed.", blocking_clips=blocking)

    for clip in clips:
        out["affected_clip_ids"].append(clip.id)
        if clip.export_path:
            out["export_cleared_clip_ids"].append(clip.id)
        if clip.export_path or clip.status == ClipStatus.exported.value:
            # C1 (codex-verdict-wave1.md): `invalidate_render` moves exported ->
            # approved, and an export with no human event (auto, or legacy with
            # no feedback) was then deleted by the next rescore. A SYSTEM fact,
            # in this transaction, keeps it (`clipper_finalize._keeps`) — not a
            # person's edit and not a verdict. Only a clip that HAD an export.
            await feedback_mod.add(
                session, clip.id, clip.project_id, "export_invalidated",
                {"cause": "project_caption_source", "field": FIELD, "old": old, "new": new,
                 "previous_export_path": clip.export_path, "previous_status": clip.status},
                origin=feedback_mod.ORIGIN_SYSTEM)
        if clip.id in placed and placed[clip.id] is not clip.caption_plan:
            clip.caption_plan = placed[clip.id]
        invalidate_render(clip)
        out["invalidated_clip_ids"].append(clip.id)
    if unverified:  # RX1: saved, but no caption layer could be built to check
        out["caption_placement_unverified"] = unverified
    return out


def _refuse_unexecutable_burn(clip: ClipModel, burned: Any) -> None:
    """SC3 (codex-verdict-next-34 R4): a person's chosen burn without a blur is valid only over a source
    declared WITHOUT text. An answer that says it has text — or leaves it unknown — would make that clip
    burn over the source's own text, so it is refused until the clip's layer changes."""
    stored = getattr(clip, "source_caption_treatment", None)
    blur = isinstance(stored, dict) and stored.get("treatment") == "blur"
    if getattr(clip, "caption_layer", None) == caption_policy.BURN and not blur and burned is not False:
        raise _err(422, "burn_over_untreated_source_captions",
                   "This clip burns ClipForge's captions by choice without blurring the source; with this "
                   "answer they would go over the source's own text. Change the clip's caption layer first.")
