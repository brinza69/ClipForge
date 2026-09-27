"""
ClipForge Worker — AI Stream Clipper: clip-level API.

Split from routers/clipper.py purely to respect the 500-line file limit; both
mount under the same /api/clipper prefix.

Every user action that expresses an opinion about a candidate — approve,
reject, a boundary nudge, a crop change, an export — is also written to the
feedback log. That log is the only training signal the ranker ever gets, so
recording it here (rather than asking the frontend to remember) is what makes
the learning loop real rather than decorative.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from job_queue import job_queue
from models import ClipModel, ClipStatus, JobType, ProjectModel
from services.clipper import feedback as feedback_mod
from services.clipper import storage
from services.clipper.clip_mutations import (
    _headline_inputs, _project_transcript, attempt_job_id, export_attempt, export_response,
    lock_clip, range_refusal)
from services.clipper.serialize import (
    can_transition,
    CLIP_PATCHABLE,
    CLIP_PATCHABLE_JSON,
    apply_patch,
    clip_to_dict,
    invalidate_render as _invalidate_render,
)

logger = logging.getLogger("clipforge.clipper.clips")

router = APIRouter(prefix="/api/clipper", tags=["clipper"])

# Which patched field maps to which feedback event. Every PATCH-able field has
# one: the event is what keeps the edit through a rescore. `metadata_changed` is
# for a person's edit with no semantic event of its own — not a training signal
# (B1-r R3; test_clipper_metadata_r.py fails when a PATCH-able field is added).
_FIELD_EVENTS = {
    "start_time": "start_changed",
    "end_time": "end_changed",
    "layout_plan": "layout_changed",
    "caption_plan": "caption_changed",
    "caption_preset_id": "caption_changed",
    "headline_text": "headline_changed",
    "title": "metadata_changed",
    "transcript_text": "metadata_changed",
    "sub_scores": "metadata_changed",
    "warnings": "metadata_changed",
}


def _err(status: int, code: str, message: str, details: str = "") -> HTTPException:
    return HTTPException(status, {"error": code, "message": message, "details": details})


# `_project_transcript` and `_headline_inputs` (the snapshot a regenerated
# headline is compared against under the lock) live in
# services/clipper/clip_mutations.py, moved for the 500-line limit; imported
# above under the same names.


async def _load_clip(session: AsyncSession, clip_id: str, *, lock: bool = False) -> ClipModel:
    """`lock=True` for an edit: the row as it is under the write lock (`lock_clip`)."""
    clip = await (lock_clip(session, clip_id) if lock else session.get(ClipModel, clip_id))
    if not clip:
        raise _err(404, "clip_not_found", "That clip no longer exists.")
    return clip


async def _project_of(session: AsyncSession, clip: ClipModel) -> ProjectModel | None:
    """The clip's project as it is now (under the lock, when one is held): what
    `effective_caption_policy` is computed from in every clip dict returned here
    (C3), and the settings a regeneration reads."""
    return await session.get(ProjectModel, clip.project_id, populate_existing=True)


@router.get("/clips/{clip_id}")
async def get_clip(clip_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    from services.clipper.project_attempts import clip_cards  # + last_preview (D2r-3)
    clip = await _load_clip(session, clip_id)
    return (await clip_cards(session, [clip], await _project_of(session, clip)))[0]


@router.patch("/clips/{clip_id}")
async def patch_clip(
    clip_id: str, payload: dict, session: AsyncSession = Depends(get_session)
) -> dict:
    """Apply an edit. Only whitelisted fields move; the changed set drives both
    the derived duration and the feedback events."""
    from services.clipper import feedback

    clip = await _load_clip(session, clip_id, lock=True)
    before = {"start_time": clip.start_time, "end_time": clip.end_time}

    # The ASS reads the plan's style, not the separate preset preference used
    # by rebuild. Saving the selector used to change only that preference.
    payload = dict(payload or {})
    # A list of lines, or null; a dict/string would be read as keys/characters (D2r K4).
    warn = payload.get("warnings")
    if warn is not None and not (isinstance(warn, list) and all(isinstance(x, str) for x in warn)):
        raise _err(400, "invalid_warnings", "Warnings must be a list of text lines.")
    if payload.get("caption_preset_id"):
        from services.captioner_presets import DEFAULT_PRESETS

        preset = payload["caption_preset_id"]
        if not isinstance(preset, str) or preset not in DEFAULT_PRESETS:
            raise _err(400, "unknown_caption_preset", "Choose an available caption style.")
        caption_plan = payload.get("caption_plan", clip.caption_plan)
        if isinstance(caption_plan, dict):
            payload["caption_plan"] = {**caption_plan, "preset_id": preset,
                                       "style": dict(DEFAULT_PRESETS[preset])}
    changed = apply_patch(clip, payload or {}, CLIP_PATCHABLE, CLIP_PATCHABLE_JSON)
    project = await _project_of(session, clip)

    # Every field, not only the render's: the export and its sidecar read the
    # row as it was claimed, and no edit lands on a clip being rendered (R2).
    if changed and clip.status == ClipStatus.exporting.value:
        raise _err(409, "export_in_progress", "Wait for this export to finish before editing.")

    if "start_time" in changed or "end_time" in changed:
        start = max(0.0, float(clip.start_time or 0.0))
        end = float(clip.end_time or 0.0)
        refusal = range_refusal(project, (before["start_time"], before["end_time"]), start, end)
        if refusal:
            raise _err(400, *refusal)
        clip.start_time, clip.end_time = start, end
        clip.duration = round(end - start, 3)

    if set(changed) & {"start_time", "end_time", "caption_plan", "caption_preset_id", "layout_plan"}:
        _invalidate_render(clip)

    if changed:
        # One commit for the edit and its events: a rescore reads the events to
        # decide what to keep, so an edit committed alone was deletable.
        for field in changed:
            event = _FIELD_EVENTS.get(field)
            if not event:
                continue
            payload_out: dict[str, Any] = {"field": field}
            if field in before:
                payload_out |= {"old": before[field], "new": getattr(clip, field)}
            await feedback.add(session, clip.id, clip.project_id, event, payload_out,
                               origin=feedback.ORIGIN_MANUAL)
        await session.commit()

    return {"clip": clip_to_dict(clip, project), "changed": changed}


_OMITTED = object()     # a reject request that did not mention a reason


async def _set_status(
    session: AsyncSession, clip_id: str, status: str, event: str, payload: dict | None = None,
    *, reason: Any = _OMITTED,
) -> dict:
    from services.clipper import feedback

    clip = await _load_clip(session, clip_id, lock=True)
    if not can_transition(clip.status, status):
        raise _err(409, "illegal_transition",
                   f"A {clip.status} clip cannot become {status}.",
                   "Wait for the render to finish first."
                   if clip.status == ClipStatus.exporting.value else "")
    # Approving an approved clip is a double-click: success, and no second
    # verdict in the log the ranker trains on (R3).
    if clip.status != status:
        clip.status = status
        await feedback.add(session, clip.id, clip.project_id, event, payload,
                           origin=feedback.ORIGIN_MANUAL)
        await session.commit()
    elif reason is not _OMITTED:
        # Rejecting a rejected clip with a different reason is an edit of the
        # reason, not a second verdict (Codex Bfix Q3): no `rejected`, no label
        # change, compared with the CURRENT rejection's effective reason.
        old = await feedback.current_reject_reason(session, clip.id)
        if reason != old:
            await feedback.add(session, clip.id, clip.project_id, "metadata_changed",
                               {"field": "reject_reason", "old": old, "new": reason},
                               origin=feedback.ORIGIN_MANUAL)
            await session.commit()
    return clip_to_dict(clip, await _project_of(session, clip))


@router.post("/clips/{clip_id}/approve")
async def approve_clip(clip_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    return await _set_status(session, clip_id, ClipStatus.approved.value, "approved")


@router.post("/clips/{clip_id}/reject")
async def reject_clip(
    clip_id: str, payload: dict | None = None, session: AsyncSession = Depends(get_session)
) -> dict:
    """A reject reason, when given, is the highest-signal feedback we get —
    it is stored verbatim on the event.

    Normalisation: no `reason` key leaves the reason as it is; `null` or `""`
    means "no reason" — on a clip already rejected, that clears it."""
    body = payload or {}
    reason = (body["reason"] or None) if "reason" in body else _OMITTED
    return await _set_status(
        session,
        clip_id,
        ClipStatus.rejected.value,
        "rejected",
        {"reason": reason} if reason not in (_OMITTED, None) else None,
        reason=reason,
    )


@router.post("/clips/{clip_id}/regenerate")
async def regenerate(
    clip_id: str, payload: dict | None = None, session: AsyncSession = Depends(get_session)
) -> dict:
    """Re-derive one part of a clip: "headline", "captions", "layout" or
    "preview". Only `preview` needs a job; the rest are fast enough inline."""
    clip = await _load_clip(session, clip_id)
    project = await session.get(ProjectModel, clip.project_id)
    if not project:
        raise _err(404, "project_not_found", "The parent project no longer exists.")

    what = ((payload or {}).get("what") or "preview").lower()
    cfg = project.clipper_settings or {}

    if what == "preview":
        job_id = await job_queue.enqueue(
            project_id=clip.project_id,
            job_type=JobType.clipper_preview.value,
            clip_id=clip.id,
            # Stamped even though a person clearly asked: an UNSTAMPED job is
            # deliberately not read as a verdict, so every human-initiated
            # render has to say so.
            metadata={"origin": feedback_mod.ORIGIN_MANUAL},
        )
        return {"job_id": job_id, "what": what}

    if what == "headline":
        from services.clipper.headline import generate_headline

        if clip.status == ClipStatus.exporting.value:      # no model call for nothing
            raise _err(409, "export_in_progress", "Wait for this export to finish before editing.")
        seen = await _headline_inputs(session, clip, project)
        result = await generate_headline(seen["cand"], engine=seen["engine"],
                                         language=seen["language"])
        # The model call ran unlocked. Its words are for the snapshot it was
        # given: the target it replaces, the window, the text, the words and the
        # settings that reached the model. Any of them moved -> refuse (R1).
        clip = await _load_clip(session, clip_id, lock=True)
        project = await _project_of(session, clip)
        if clip.status == ClipStatus.exporting.value:
            raise _err(409, "export_in_progress", "Wait for this export to finish before editing.")
        if project is None or await _headline_inputs(session, clip, project) != seen:
            raise _err(409, "clip_changed", "The clip changed while its headline was written.",
                       "Regenerate the headline again.")
        new_text = result.get("text") or clip.headline_text
        if new_text != clip.headline_text:
            clip.headline_text = new_text
            await feedback_mod.add(session, clip.id, clip.project_id, "headline_changed",
                                   {"field": "headline_text", "regenerated": True},
                                   origin=feedback_mod.ORIGIN_MANUAL)
            await session.commit()
        return {"clip": clip_to_dict(clip, project), "source": result.get("source")}

    if what == "captions":
        from services.clipper.captions import build_caption_plan

        clip = await _load_clip(session, clip_id, lock=True)
        if clip.status == ClipStatus.exporting.value:
            raise _err(409, "export_in_progress", "Wait for this export to finish before editing.")
        # The settings as they are under the lock, not as read before it.
        project = await _project_of(session, clip)
        cfg = (project.clipper_settings if project else None) or {}
        transcript = await _project_transcript(session, clip.project_id)
        try:
            plan = build_caption_plan(
                {"start": clip.start_time, "end": clip.end_time, "text": clip.transcript_text or ""},
                transcript,
                preset_id=clip.caption_preset_id or cfg.get("caption_preset_id") or "bold_impact",
                max_words=3,
                position=cfg.get("caption_position") or "bottom",
                layout=clip.layout_plan or {},
            )
            if plan != clip.caption_plan:        # an identical rebuild is not an edit
                clip.caption_plan = plan
                _invalidate_render(clip)
                await feedback_mod.add(session, clip.id, clip.project_id, "caption_changed",
                                       {"field": "caption_plan", "regenerated": True},
                                       origin=feedback_mod.ORIGIN_MANUAL)
                await session.commit()
        except Exception as exc:
            logger.exception("caption regeneration failed")
            raise _err(500, "caption_failed", "Could not rebuild the captions.", str(exc)[:200])
        return {"clip": clip_to_dict(clip, project)}

    raise _err(400, "unknown_regenerate_target", f"Cannot regenerate '{what}'.")


@router.get("/clips/{clip_id}/preview-frame")
async def preview_frame(
    clip_id: str, t: float | None = None, session: AsyncSession = Depends(get_session)
) -> Response:
    """A still of the saved export recipe; `t` is on the delivered clock."""
    import math
    from workers.clipper_preview_frame import render_frame

    clip = await _load_clip(session, clip_id)
    project = await session.get(ProjectModel, clip.project_id)
    source = (project.video_path if project else None) or ""
    if not source:
        raise _err(409, "source_missing", "The source video has not been downloaded yet.")

    offset = float(t if t is not None else 0.5)
    if not math.isfinite(offset) or offset < 0:
        raise _err(400, "invalid_frame_time", "Choose a non-negative frame time.")

    try:
        result = await render_frame(clip, project, offset)
    except Exception as exc:
        logger.exception("preview frame render failed")
        raise _err(500, "preview_failed", "Could not render that frame.", str(exc)[:200])

    return Response(content=result["png"], media_type="image/png", headers={
        "Cache-Control": "no-store", "X-Clip-Time": str(result["at"]),
        "X-Clip-Duration": str(result["duration"]),
        "X-Caption-Action": result["caption_action"],
    })


@router.get("/clips/{clip_id}/preview-file")
async def preview_file(clip_id: str, session: AsyncSession = Depends(get_session)):
    """Stream a rendered preview (or the export, if that is all there is).

    Served through the router rather than a StaticFiles mount because clipper
    artifacts live under data/clipper/{project}/ — mounting that would expose
    the whole analysis tree, including transcripts.
    """
    from fastapi.responses import FileResponse

    clip = await _load_clip(session, clip_id)
    path = clip.preview_path or clip.export_path
    if not path or not storage.is_usable_output(path):
        raise _err(404, "no_preview", "This clip has no rendered preview yet.")
    return FileResponse(path, media_type="video/mp4", filename=f"{clip.id}.mp4")


@router.get("/clips/{clip_id}/export-file")
async def export_file(clip_id: str, session: AsyncSession = Depends(get_session)):
    """Download the final render — only while the clip is `exported`.

    `export_path` survives a re-export, a failure and recovery, and the files at
    it can be a half-published set (`_publish_export`'s docstring). Only the
    status says all three were published and committed together (R4c).
    """
    from fastapi.responses import FileResponse

    clip = await _load_clip(session, clip_id)
    if not clip.export_path:
        raise _err(404, "no_export", "This clip has not been exported yet.")
    if clip.status != ClipStatus.exported.value:
        raise _err(409, "export_not_current",
                   f"This clip is {clip.status}; its last export is not the current one.")
    if not storage.is_usable_output(clip.export_path):
        raise _err(404, "no_export", "This clip has not been exported yet.")
    safe = "".join(c for c in (clip.title or clip.id) if c.isalnum() or c in " -_")[:60].strip()
    return FileResponse(
        clip.export_path, media_type="video/mp4", filename=f"{safe or clip.id}.mp4"
    )


# `_EXPORTABLE_FROM` and `claim_for_export` (the conditional UPDATE that is the
# export lock) live in services/clipper/clip_mutations.py, moved for the
# 500-line limit, with the submit that commits the claim with its job (R4b).


@router.post("/clips/{clip_id}/export")
async def export_clip(clip_id: str, payload: dict | None = None) -> dict:
    """Claim the clip and queue one render: the claim, `export_job_id` and the
    job row are ONE commit (R4b, `clip_mutations.submit_export`).

    THE CLAIM IS THE UPDATE. Reading the status and then writing it is two
    statements, so two requests can both read `candidate` before either commits
    and both enqueue a job — two renders writing one file, with progress
    oscillating between them. This is the same fix the job queue got on
    2026-08-17 and it was left undone here on the same day, which is how a
    lesson gets applied in one place and not the other.

    Optional `{"attempt_id": "<12 hex>"}` IS the job id: a retry after a lost
    response sends the same one and gets that job back with its real status.
    """
    job_id = attempt_job_id(payload)
    got = await export_attempt(clip_id, job_id=job_id, origin=feedback_mod.ORIGIN_MANUAL)
    return export_response(clip_id, job_id, got)


@router.post("/clips/{clip_id}/feedback")
async def record_feedback(
    clip_id: str, payload: dict, session: AsyncSession = Depends(get_session)
) -> dict:
    from services.clipper import feedback

    clip = await _load_clip(session, clip_id)
    event_type = (payload or {}).get("event_type") or ""
    try:
        event_id = await feedback.record(
            session, clip.id, clip.project_id, event_type,
            (payload or {}).get("payload"),
            # The endpoint is the UI's, so this is manual BY CONTRACT: the
            # client is not allowed to name an origin. Letting it would let an
            # automation file its own exports as human verdicts.
            origin=feedback.ORIGIN_MANUAL,
        )
    except ValueError as exc:
        raise _err(400, "unknown_event", str(exc)) from exc
    return {"event_id": event_id}


@router.post("/clips/{clip_id}/performance")
async def record_performance(
    clip_id: str, payload: dict, session: AsyncSession = Depends(get_session)
) -> dict:
    """Attach post-publication metrics.

    Entered by the user or imported from an official API — ClipForge does not
    scrape platforms (brief §30). Stored as a feedback event so it feeds the
    ranker alongside the approve/reject signal.
    """
    from services.clipper import feedback

    clip = await _load_clip(session, clip_id)
    body = dict(payload or {})
    if not body.get("platform") or not body.get("post_url"):
        raise _err(400, "missing_platform", "Both a platform and the post URL are required.")

    numeric = {
        key: float(body[key])
        for key in (
            "views", "likes", "comments", "shares", "saves",
            "avg_watch_time_s", "completion_rate", "followers_gained",
        )
        if body.get(key) is not None
    }
    event_id = await feedback.record(
        session,
        clip.id,
        clip.project_id,
        "performance_recorded",
        {
            "platform": str(body["platform"])[:40],
            "post_url": str(body["post_url"])[:500],
            "published_at": body.get("published_at"),
            **numeric,
        },
        origin=feedback.ORIGIN_MANUAL,
    )
    return {"event_id": event_id}


@router.get("/clips/{clip_id}/events")
async def clip_events(clip_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    from services.clipper import feedback

    await _load_clip(session, clip_id)
    return {"events": await feedback.events_for_clip(session, clip_id)}


@router.get("/clips")
async def list_clips(
    project_id: str | None = None,
    status: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Flat clip list. The detail endpoint already nests clips under a project;
    this exists for cross-project views (e.g. everything still unreviewed)."""
    query = select(ClipModel).order_by(ClipModel.rank_position, ClipModel.start_time)
    if project_id:
        query = query.where(ClipModel.project_id == project_id)
    if status:
        wanted = [s.strip() for s in status.split(",") if s.strip()]
        if wanted:
            query = query.where(ClipModel.status.in_(wanted))
    clips = (await session.execute(query.limit(500))).scalars().all()
    projects = {p.id: p for p in (await session.execute(select(ProjectModel).where(
        ProjectModel.id.in_({c.project_id for c in clips})))).scalars()}
    return [clip_to_dict(c, projects.get(c.project_id)) for c in clips]
