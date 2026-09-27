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

from config import settings
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


# ── caption display: the UI warning's facts (B/BURST-UI-contract.md) ─────────

# The outcomes that SAY no caption was burned (`clipper_captions._plan_for_render` / `_write_ass`).
# Anything else without `burn` - a missing, `{}` or unknown outcome - is not that answer (BURST R2).
_NOT_BURNED = frozenset({"suppressed", "empty", "unavailable", "empty_after_remap"})


def _render_facts(state: Any, drop_spans: Any = None, record: Any = None) -> dict[str, Any]:
    """One render's `caption_plan_state` as the warning reads it. No state, `display` absent or an
    outcome nobody wrote = `not_measured` (never clean, never "burned nothing on purpose"); a state
    that is not a dict = `unreadable`; `{}` as `display` = measured with nothing to report.
    `record` is the export's `render_record`; a preview has none, and is not asked for one."""
    blank = {"outcome": None, "reason": None, "missing": None, "facts": None}
    if state is None:
        return {**blank, "state": "not_measured"}
    if not isinstance(state, dict):
        return {**blank, "state": "unreadable"}
    outcome, reason = state.get("outcome"), state.get("reason")
    out = {"outcome": outcome, "reason": reason, "missing": None, "facts": None}
    if outcome != "burn" and reason != "no_card_representable":
        return {**out, "state": "not_burned" if outcome in _NOT_BURNED else "not_measured"}
    if "display" not in state:
        return {**out, "state": "not_measured"}
    report = state["display"]
    if not isinstance(report, dict):
        return {**out, "state": "unreadable"}
    # Burn evidence is asked of an export that drew something: with no card representable no .ass
    # was written, and its encode rightly carried no subtitles filter.
    burned = (record is None or reason == "no_card_representable"
              or (isinstance(record, dict) and record.get("caption_filter") is True))
    if not report:
        return {**out, "state": "verified" if burned else "unverified", "missing": "none"}
    from services.clipper.dead_air import remap_time

    spans = drop_spans if isinstance(drop_spans, list) else None
    try:
        unshown = []
        for c in report.get("unshown_cards") or []:
            plan_clock = c.get("why") == "no_time"
            unshown.append({**c, "clock": "plan" if plan_clock else "export",
                            "export_at": ((remap_time(float(c["start"]), spans) if spans is not None
                                           else None) if plan_clock else c.get("start"))})
        short = report.get("short_intervals")
        facts = {"short_count": report.get("short_cards"),
                 "short_cards": None if short is None else [{**c, "clock": "export"} for c in short],
                 "unshown_cards": unshown, "ass_agrees": report.get("ass_agrees"),
                 "overlapping_pairs": report.get("overlapping_pairs"),
                 "empty_events": report.get("empty_events"),
                 "plan_limits": report.get("plan_limits"),
                 "plan_settled": report.get("plan_settled")}
    except (AttributeError, KeyError, TypeError, ValueError):
        return {**out, "state": "unreadable"}
    agrees = report.get("ass_agrees") is True and burned
    return {**out, "state": "limited" if agrees else "unverified", "facts": facts,
            "missing": ("all" if reason == "no_card_representable" else "some" if unshown else "none")}


def _stale(block: dict[str, Any], why: str) -> dict[str, Any]:
    return {**block, "state": "stale", "found_state": block["state"], "current": False,
            "stale_reason": why}


def _export_view(clip: ClipModel) -> dict[str, Any]:
    """The export whose sidecar sits at `export_path` (or, after an edit cleared it, the clip's
    export slot). CURRENT only while the clip is `exported` — the R4c rule `/export-file` serves
    by — and the plan it burned is still the clip's plan."""
    import json
    from pathlib import Path

    from services.clipper import storage

    mp4 = Path(clip.export_path) if clip.export_path else storage.export_path(
        clip.project_id, clip.id)
    side_path = mp4.with_suffix(".json")
    exported = clip.status == ClipStatus.exported.value and bool(clip.export_path)
    blank = {"outcome": None, "reason": None, "missing": None, "facts": None,
             "identity": {"attempt_job_id": clip.export_job_id}, "current": True,
             "stale_reason": None}
    # The DB says a render exists: its missing file or report is never "no render" (BURST R2).
    if exported and not mp4.is_file():
        return {**blank, "state": "unreadable", "why": "mp4_missing"}
    if not side_path.is_file():
        return ({**blank, "state": "not_measured", "why": "sidecar_missing"} if exported
                else {"state": "none", "current": False})
    try:
        side = json.loads(side_path.read_text(encoding="utf-8"))
        if not isinstance(side, dict) or side.get("clip_id") != clip.id:
            raise ValueError("not this clip's sidecar")
    except (OSError, ValueError):
        block = {"state": "unreadable", "outcome": None, "reason": None, "missing": None,
                 "facts": None, "identity": None}
        return (block | {"current": True, "stale_reason": None}
                if clip.status == ClipStatus.exported.value and clip.export_path
                else _stale(block, "clip_not_exported"))
    record = side.get("render_record") if isinstance(side.get("render_record"), dict) else {}
    state = side.get("caption_plan_state")
    block = _render_facts(state, side.get("drop_spans"), record)
    block["identity"] = {"attempt_job_id": None, "ass_sha256": record.get("ass_sha256"),
                         "caption_filter": record.get("caption_filter")}
    if clip.status != ClipStatus.exported.value or not clip.export_path:
        return _stale(block, "clip_not_exported")
    origin = state.get("origin") if isinstance(state, dict) else None
    if ((origin == "stored" and side.get("caption_plan") != clip.caption_plan)
            or (origin == "built" and clip.caption_plan is not None)):
        return _stale(block, "plan_changed")
    block["identity"]["attempt_job_id"] = clip.export_job_id
    return {**block, "current": True, "stale_reason": None}


def _preview_view(clip: ClipModel) -> dict[str, Any]:
    """The preview at `preview_path` — current while it is set, since an edit clears it — read from
    `clip.preview_record`: the attempt whose file is selected and ITS report, written with
    `preview_path` in one transaction (BURST R1, codex-verdict-next-23/24), never a job looked up
    beside it. No record (a legacy preview) or one without a report: `not_measured`, known to exist,
    not known to be clean."""
    rec = clip.preview_record if isinstance(clip.preview_record, dict) else None
    published = rec is not None and "caption_plan_state" in rec
    block = _render_facts(rec.get("caption_plan_state") if published else None)
    block["identity"] = ({"job_id": rec.get("job_id"), "attempt": rec.get("attempt")} if published
                         else {"job_id": None})
    if not clip.preview_path:
        return _stale(block, "preview_cleared") if published else {"state": "none", "current": False}
    if not published:
        return {**block, "state": "not_measured", "current": True, "stale_reason": None}
    return {**block, "current": True, "stale_reason": None}


def caption_display_view(clip: ClipModel, project: ProjectModel | None = None) -> dict[str, Any]:
    """`caption_display` on a card: the CURRENT PLAN's facts, the export's and the SELECTED preview
    attempt's (`clip.preview_record`), and `level` — the one answer the warning shows (contract §5):
    "unverified" > "limited" > "unknown" > "verified" > "not_burned" > "not_rendered".

    While OUR caption layer is suppressed by the effective policy (the render's own
    `caption_policy.effective`: clip, project or default), the plan's limits stay in `plan` for
    the editor but never raise the level: the layer is off on purpose (BURST R3). That says nothing
    about how legible the SOURCE's own captions are."""
    from services.clipper import caption_display
    from services.clipper.captions import MIN_CHUNK_S

    plan = caption_display.plan_facts(clip.caption_plan, MIN_CHUNK_S)
    policy = (caption_policy.effective((project.clipper_settings or {}).get(caption_policy.SETTING),
                                       clip.source_has_burned_captions)
              if project is not None else None)
    plan_counts = not (isinstance(policy, dict) and policy.get("action") == "suppress")
    export, preview = _export_view(clip), _preview_view(clip)
    # A block that is not current reads "stale" or "none", which no level below matches.
    live = [("export", export), ("preview", preview)]
    level, source = "not_rendered", None
    for want in ("unverified", "unreadable", "limited", "not_measured", "verified", "not_burned"):
        hit = next((n for n, b in live if b["state"] == want), None)
        if hit is None and want == "limited" and plan["state"] == "limited" and plan_counts:
            hit = "plan"
        if hit is not None:
            level = {"unreadable": "unverified", "not_measured": "unknown"}.get(want, want)
            source = hit
            break
    return {"schema": "caption_display_v1", "min_chunk_s": MIN_CHUNK_S, "level": level,
            "level_from": source, "policy": policy, "plan": plan, "export": export,
            "preview": preview}


def effective_max_clip_s(project: ProjectModel | None) -> float:
    """The maximum clip length the server applies to this project (O4): its own
    `max_clip_s` when that is a positive number (a bool is not one), else the config
    default. `clip_mutations.range_refusal` refuses by it and `project_to_dict` shows
    it, so the editor never applies a limit of its own (codex-verdict-next-15 R1)."""
    cfg = (project.clipper_settings if project else None) or {}
    v = cfg.get("max_clip_s")
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not v > 0:
        return float(settings.clipper_max_clip_s)
    return float(v)


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
        "max_clip_s_effective": effective_max_clip_s(project),
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
