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

from typing import Any

from models import ClipModel, ProjectModel

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
    "status": str,
}

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


def clip_to_dict(clip: ClipModel) -> dict[str, Any]:
    """One candidate, shaped exactly like `ClipperClip` in src/types/clipper.ts."""
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
        "layout_plan": clip.layout_plan,
        "caption_plan": clip.caption_plan,
        "warnings": clip.warnings or [],
        "dedupe_group": clip.dedupe_group,
        "is_alternative": _as_bool(clip.is_alternative),
        "rank_position": clip.rank_position,
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
