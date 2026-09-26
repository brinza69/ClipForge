"""
ClipForge — AI Stream Clipper: ORM → JSON shaping.

The frontend contract lives in src/types/clipper.ts; this module is the other
half of it. Both clipper routers import from here so a field is never spelled
two different ways in two responses.

Kept separate from the routers because ClipModel carries ~55 legacy columns
from the superseded clip editor, and only a named subset belongs in a clipper
response. Whitelisting here (rather than dumping the row) also stops a future
column from silently leaking into the API.
"""

from __future__ import annotations

import logging
from typing import Any

from models import ClipModel, ClipStatus, ProjectModel
from services.clipper import caption_policy, edit_profiles

logger = logging.getLogger("clipforge.clipper.serialize")


def invalidate_render(clip: ClipModel) -> None:
    """Keep files/history on disk, stop presenting the old file as this edit."""
    clip.preview_path = None
    clip.export_path = None
    clip.review = None
    if clip.status == ClipStatus.exported.value:
        clip.status = ClipStatus.approved.value


# Fields a PATCH /clips/{id} is allowed to touch, mapped to a coercer. Anything
# not in here is ignored rather than 400-ing, so a newer frontend talking to an
# older backend degrades instead of breaking.
CLIP_PATCHABLE: dict[str, type] = {
    "title": str,
    "start_time": float,
    "end_time": float,
    "transcript_text": str,
    "headline_text": str,
    "caption_preset_id": str,
}

# `status` was in the list above until 2026-08-18, typed `str` and validated
# nowhere. A client could write any string into it, and — worse — could walk
# straight past the guard on the export endpoint by setting the status the
# guard wanted to see. A whitelist that includes the field the guards read is
# not a whitelist. Status moves through the named endpoints only.
#
# THE ONE PLACE THE LEGAL MOVES ARE WRITTEN DOWN. Five call sites used to
# assign `clip.status` directly, each with its own idea of what was allowed:
# approve, reject, the export endpoint, the render worker's success path and
# its failure path.
_CLIP_TRANSITIONS: dict[str, frozenset[str]] = {
    "candidate": frozenset({"approved", "rejected", "exporting"}),
    "approved": frozenset({"rejected", "candidate", "exporting"}),
    "rejected": frozenset({"approved", "candidate"}),
    # Rendering. `exported` and `failed` are what the worker writes when it is
    # done; nothing else may leave this state, which is what stops a second
    # export being started on top of a running one.
    "exporting": frozenset({"exported", "failed"}),
    # Not terminal, deliberately: re-rendering after an edit is what the clip
    # editor exists for, and a finished clip can still be rejected.
    "exported": frozenset({"exporting", "rejected", "approved", "candidate"}),
    "failed": frozenset({"exporting", "rejected", "candidate"}),
}


def can_transition(current: str | None, target: str) -> bool:
    """Whether a clip may move from `current` to `target`.

    A move to the state it is already in is allowed and is a no-op: approving
    an approved clip is a double-click, not an error.
    """
    now = str(current or "candidate")
    if now == target:
        return True
    return target in _CLIP_TRANSITIONS.get(now, frozenset())

# Same idea for the JSON-blob columns, which need no coercion.
CLIP_PATCHABLE_JSON = ("layout_plan", "caption_plan", "sub_scores", "warnings")

PROJECT_PATCHABLE_JSON = ("clipper_settings",)
PROJECT_PATCHABLE: dict[str, type] = {
    "title": str,
    "content_type_override": str,
}


def _as_bool(value: Any) -> bool:
    """SQLite gives back 0/1/None for BOOLEAN; the client wants a real bool."""
    return bool(value) if value is not None else False


def _warnings(clip: ClipModel) -> list[str]:
    """`clip.warnings` as the `string[]` the card maps over, or `[]` (D2r-2 K8).

    A legacy dict, string or list with a non-str element is answered as `[]`
    and logged; the stored value is not touched. A string would crash the card
    at `.slice().map`, and converting any of them would invent warning text.
    """
    value = clip.warnings
    if value is None:
        return []
    if isinstance(value, list) and all(isinstance(w, str) for w in value):
        return value
    logger.warning("clip %s: warnings are a %s, not a list of text; answered as []",
                   clip.id, type(value).__name__)
    return []


def clip_to_dict(clip: ClipModel, project: ProjectModel | None = None) -> dict[str, Any]:
    """One candidate, shaped exactly like `ClipperClip` in src/types/clipper.ts.

    `effective_caption_policy` needs the project's answer, so it is `None` when
    the caller did not pass the project: "not computed here", never a default
    that would read as "nobody has said".
    """
    return {
        "id": clip.id,
        "project_id": clip.project_id,
        "title": clip.title,
        "start_time": round(float(clip.start_time or 0.0), 3),
        "end_time": round(float(clip.end_time or 0.0), 3),
        "duration": round(float(clip.duration or 0.0), 3),
        "overall_score": clip.overall_score,
        "sub_scores": clip.sub_scores,
        "score_reason": clip.score_reason,
        "headline_text": clip.headline_text,
        "transcript_text": clip.transcript_text,
        "content_type": clip.content_type,
        "content_confidence": clip.content_confidence,
        "content_type_origin": clip.content_type_origin,
        # Resolved HERE, not in the browser. The frontend displays this; it does
        # not own a second copy of the type-to-profile map, which would drift
        # from `edit_profiles` the first time either changed.
        "edit_profile": edit_profiles.resolve(
            clip.content_type, clip.content_confidence, clip.content_type_origin),
        "layout_plan": clip.layout_plan,
        "caption_plan": clip.caption_plan,
        "caption_preset_id": clip.caption_preset_id,
        "warnings": _warnings(clip),
        "dedupe_group": clip.dedupe_group,
        "is_alternative": _as_bool(clip.is_alternative),
        # Tri-state: True/False are a person's answer, None is "nobody has
        # said" — `_as_bool` would turn that into False and erase the
        # difference between "declared none" and "never asked".
        "source_has_burned_captions": (
            None if clip.source_has_burned_captions is None
            else bool(clip.source_has_burned_captions)),
        # What the next render will do with our caption layer, and whose answer
        # that is (clip / project / default). Presentation only.
        "effective_caption_policy": caption_policy.effective(
            (project.clipper_settings or {}).get(caption_policy.SETTING),
            clip.source_has_burned_captions) if project is not None else None,
        "rank_position": clip.rank_position,
        "selection_run_id": clip.selection_run_id,
        # Why this clip exists — anchor, payoff, required context, archetype,
        # which edit variant, what the judge said. The UI can stay unaware of
        # it, but a bad pick has to be explainable without a debugger.
        "reasoning": clip.reasoning or None,
        # What Pass D found in the rendered cut. Present only after an export,
        # because that is when there is a cut to look at.
        "review": clip.review or None,
        "ranker_version": clip.ranker_version,
        "status": clip.status,
        "export_path": clip.export_path,
        "preview_path": clip.preview_path,
        "thumbnail_path": clip.thumbnail_path,
    }


def project_to_dict(project: ProjectModel) -> dict[str, Any]:
    """One project, shaped like `ClipperProject`. Clips/active_job are added by
    the detail endpoint — the list endpoint deliberately omits them so a page
    with 50 projects doesn't ship every candidate."""
    return {
        "id": project.id,
        "title": project.title,
        "source_url": project.source_url,
        "source_type": project.source_type,
        "source_kind": project.source_kind,
        "status": project.status,
        "channel_name": project.channel_name,
        "duration": project.duration,
        "width": project.width,
        "height": project.height,
        "fps": project.fps,
        "thumbnail_url": project.thumbnail_url,
        "content_type": project.content_type,
        "content_type_confidence": project.content_type_confidence,
        "content_type_override": project.content_type_override,
        "rights_confirmed": _as_bool(project.rights_confirmed),
        "clipper_settings": project.clipper_settings,
        "analysis_version": project.analysis_version,
        "created_at": project.created_at.isoformat() if project.created_at else None,
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
    }


def job_to_dict(job: Any) -> dict[str, Any]:
    """A JobModel row in the shape `ClipperJob` expects."""
    return {
        "id": job.id,
        "project_id": job.project_id,
        "clip_id": job.clip_id,
        "type": job.type,
        "status": job.status,
        "progress": float(job.progress or 0.0),
        "progress_message": job.progress_message or "",
        "error": job.error,
    }


def effective_content_type(project: ProjectModel) -> str:
    """The user's override always beats detection (brief §16)."""
    return project.content_type_override or project.content_type or "unknown"


def apply_patch(
    obj: Any,
    payload: dict[str, Any],
    allowed: dict[str, type],
    allowed_json: tuple[str, ...],
) -> list[str]:
    """Copy whitelisted keys from `payload` onto `obj`. Returns the field names
    that actually changed, so callers can record precise feedback events
    instead of a vague "edited"."""
    changed: list[str] = []
    for key, coerce in allowed.items():
        if key not in payload or payload[key] is None:
            continue
        try:
            value = coerce(payload[key])
        except (TypeError, ValueError):
            continue
        if getattr(obj, key, None) != value:
            setattr(obj, key, value)
            changed.append(key)
    for key in allowed_json:
        if key in payload:
            if getattr(obj, key, None) != payload[key]:
                setattr(obj, key, payload[key])
                changed.append(key)
    return changed
