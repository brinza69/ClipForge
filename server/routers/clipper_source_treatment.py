"""
ClipForge Worker — AI Stream Clipper: a clip's source-caption treatment (SC3, codex-verdict-next-33 §3).

GET /api/clipper/clips/{clip_id}/source-treatment — what is stored, what the next render does with it, and
whether blur is available for this clip (and why not).

PUT /api/clipper/clips/{clip_id}/source-treatment — replaces BOTH fields, atomically:

    {"source_caption_treatment": null | {"treatment": "none"} | {"treatment": "blur", "mask_sha256": "<sha>"},
     "caption_layer": null | "burn" | "suppress"}

Per clip only in this version: no project inheritance, no temporal overrides. Only none and blur are
offered; erase is refused. Types are strict and unknown fields are refused; the client names a mask by its
hash and nothing else — the mask must already be in the project's store, imported offline. `decided_by` is
written by the server. `source_has_burned_captions` stays the truth about the source and is never touched
here: a burn over source text that stays needs an executable blur.

A change invalidates the clip's export and preview in the same transaction (`invalidate_render`, and the
preview's publish check reads both fields). An export in progress is 409 with nothing written. The same
configuration sent again is a no-op: no event, no invalidation.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from models import ClipModel, ClipStatus, ProjectModel
from services.clipper import caption_policy
from services.clipper import feedback as feedback_mod
from services.clipper import source_treatment_store as store
from services.clipper.clip_mutations import lock_clip
from services.clipper.serialize import clip_to_dict, invalidate_render
from services.clipper.source_treatment import BLUR, HUMAN, NONE, SourceTreatmentRefused

router = APIRouter(prefix="/api/clipper", tags=["clipper"])

FIELDS = ("source_caption_treatment", "caption_layer")
LAYERS = (caption_policy.BURN, caption_policy.SUPPRESS)


def _err(status: int, code: str, message: str, **extra) -> HTTPException:
    return HTTPException(status, {"error": code, "message": message, **extra})


def _requested(payload: Any) -> tuple[dict | None, str | None]:
    """The stored form of a request, or 422. Strict: a 1, a "true" or an extra key is never coerced."""
    if not isinstance(payload, dict) or set(payload) != set(FIELDS):
        raise _err(422, "invalid_body", f"The body must carry exactly {list(FIELDS)}.")
    treatment, layer = payload["source_caption_treatment"], payload["caption_layer"]
    if layer is not None and not (isinstance(layer, str) and layer in LAYERS):
        raise _err(422, "invalid_value", 'caption_layer must be null, "burn" or "suppress".')
    if treatment is None:
        return None, layer
    if not isinstance(treatment, dict) or not isinstance(treatment.get("treatment"), str):
        raise _err(422, "invalid_value", "source_caption_treatment must be null or an object with a treatment.")
    if treatment["treatment"] == NONE and set(treatment) == {"treatment"}:
        return {"treatment": NONE, "decided_by": HUMAN}, layer
    if (treatment["treatment"] == BLUR and set(treatment) == {"treatment", "mask_sha256"}
            and isinstance(treatment["mask_sha256"], str)):
        return {"treatment": BLUR, "decided_by": HUMAN, "mask_sha256": treatment["mask_sha256"]}, layer
    if treatment["treatment"] not in (NONE, BLUR):
        raise _err(422, "treatment_not_offered",
                   f"{treatment['treatment']!r} is not offered: choose none or blur.")
    raise _err(422, "invalid_value", "none takes no other field; blur takes exactly mask_sha256.")


def _src(project: ProjectModel | None) -> str | None:
    path = project.video_path if project is not None else None
    return path if path and Path(path).exists() else None


def _policy(clip: ClipModel, project: ProjectModel | None, layer: Any) -> dict:
    return caption_policy.decide(((project.clipper_settings if project else None) or {}).get(
        caption_policy.SETTING), clip_setting=clip.source_has_burned_captions, layer=layer)


def _view(clip: ClipModel, project: ProjectModel | None) -> dict:
    """Stored choice, what the next render does, and availability — all read from the server's state."""
    src = _src(project)
    policy = _policy(clip, project, clip.caption_layer)
    effective: dict = {"layer": policy["action"], "layer_decided_by": policy["decided_by"], "refused": None}
    try:
        keys = store.for_render(clip, project, policy, src or "")
        effective["treatment"] = (keys.get("source_treatment") or {}).get("treatment", NONE)
    except SourceTreatmentRefused as r:
        effective.update(treatment=None, refused={"reason": r.reason, "detail": r.detail})
    return {"requested": {"source_caption_treatment": clip.source_caption_treatment,
                          "caption_layer": clip.caption_layer},
            "effective": effective, "availability": store.availability(clip, project, src)}


@router.get("/clips/{clip_id}/source-treatment")
async def get_source_treatment(clip_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    clip = await session.get(ClipModel, clip_id)
    if not clip:
        raise _err(404, "clip_not_found", "That clip no longer exists.")
    project = await session.get(ProjectModel, clip.project_id)
    return {"clip_id": clip.id, **_view(clip, project)}


@router.put("/clips/{clip_id}/source-treatment")
async def put_source_treatment(clip_id: str, payload: dict,
                               session: AsyncSession = Depends(get_session)) -> dict:
    treatment, layer = _requested(payload)
    clip = await lock_clip(session, clip_id)
    if not clip:
        raise _err(404, "clip_not_found", "That clip no longer exists.")
    if clip.status == ClipStatus.exporting.value:
        raise _err(409, "export_in_progress", "Wait for this export to finish before editing.")
    project = await session.get(ProjectModel, clip.project_id)
    old = {"source_caption_treatment": clip.source_caption_treatment, "caption_layer": clip.caption_layer}
    new = {"source_caption_treatment": treatment, "caption_layer": layer}
    if old == new:
        return {"clip": clip_to_dict(clip, project), "changed": False, **_view(clip, project)}

    # Everything the next render would refuse is refused HERE, before anything is written.
    blur = treatment is not None and treatment["treatment"] == BLUR
    if blur:
        src = _src(project)
        if src is None:
            raise _err(422, "blur_unavailable", "The source video is not on disk.", reason="mask_source_mismatch")
        try:
            store.validate(clip, project, src, treatment["mask_sha256"])
        except SourceTreatmentRefused as r:
            raise _err(422, "blur_unavailable", r.detail or r.reason, reason=r.reason) from None
    if layer == caption_policy.BURN and not blur and store._burned(clip, project) is not False:
        raise _err(422, "burn_over_untreated_source_captions",
                   "Burning ClipForge's captions over the source's own needs the source's text blurred first.")
    caption_plan = clip.caption_plan
    if _policy(clip, project, layer)["action"] == caption_policy.BURN:
        from routers.clipper_caption_source import _place_or_refuse
        caption_plan = _place_or_refuse(clip)

    clip.source_caption_treatment = treatment
    clip.caption_layer = layer
    if caption_plan is not clip.caption_plan:
        clip.caption_plan = caption_plan
    invalidate_render(clip)
    await feedback_mod.add(session, clip.id, clip.project_id, "caption_changed",
                           {"field": "source_treatment", "old": old, "new": new},
                           origin=feedback_mod.ORIGIN_MANUAL)
    await session.commit()
    return {"clip": clip_to_dict(clip, project), "changed": True, **_view(clip, project)}
