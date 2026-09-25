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


def _place_or_refuse(clip: ClipModel) -> dict | None:
    """The reaction PUT's caption check, for an answer that turns the layer ON.

    A reaction framing with no caption slot is accepted while the layer is off,
    because nothing is placed. Turning the layer on over it used to return 200
    and then fail every preview and export, so it is refused here with the
    content height that would leave room; an accepted one stores the resolved
    position, as the reaction PUT does. Non-reaction layouts and manual
    positions come back unchanged — the resolver returns None for them.
    """
    from services.clipper.reaction_captions import (
        NoCaptionGap, caption_ready_height, resolve_reaction_caption_y)
    plan, cap = clip.layout_plan, clip.caption_plan
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
    return cap if y is None else {**cap, "y_pct": y}


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

    decision = caption_policy.decide(
        ((project.clipper_settings if project else None) or {}).get(caption_policy.SETTING),
        clip_setting=value)
    caption_plan = clip.caption_plan
    if decision["action"] == caption_policy.BURN:
        caption_plan = _place_or_refuse(clip)

    clip.source_has_burned_captions = value
    if caption_plan is not clip.caption_plan:
        clip.caption_plan = caption_plan
    invalidate_render(clip)
    await feedback_mod.add(
        session, clip.id, clip.project_id, "caption_changed",
        {"field": FIELD, "old": old, "new": value},
        origin=feedback_mod.ORIGIN_MANUAL,
    )
    await session.commit()
    return {"clip": clip_to_dict(clip, project)}


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
    new_action = caption_policy.decide(new)["action"]
    if caption_policy.decide(old)["action"] == new_action:
        return out

    clips = (await session.execute(
        select(ClipModel)
        .where(ClipModel.project_id == project.id)
        .where(ClipModel.source_has_burned_captions.is_(None))
        .order_by(ClipModel.id)
    )).scalars().all()

    busy = [c.id for c in clips if c.status == ClipStatus.exporting.value]
    if busy:
        raise _err(409, "export_in_progress",
                   "Wait for these exports to finish before changing the project's "
                   "caption source.", clip_ids=busy)

    placed, blocking = {}, []
    if new_action == caption_policy.BURN:
        for clip in clips:
            try:
                placed[clip.id] = _place_or_refuse(clip)
            except HTTPException as exc:
                blocking.append({"clip_id": clip.id, "title": clip.title,
                                 "max_content_height": exc.detail.get("max_content_height")})
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
    return out
