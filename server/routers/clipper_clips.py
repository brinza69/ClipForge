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

from config import settings
from database import get_session
from job_queue import job_queue
from models import ClipModel, ClipStatus, JobType, ProjectModel
from services.clipper.serialize import (
    CLIP_PATCHABLE,
    CLIP_PATCHABLE_JSON,
    apply_patch,
    clip_to_dict,
)

logger = logging.getLogger("clipforge.clipper.clips")

router = APIRouter(prefix="/api/clipper", tags=["clipper"])

# Which patched field maps to which feedback event. Anything not listed still
# saves, it just isn't a training signal.
_FIELD_EVENTS = {
    "start_time": "start_changed",
    "end_time": "end_changed",
    "layout_plan": "layout_changed",
    "caption_plan": "caption_changed",
    "caption_preset_id": "caption_changed",
    "headline_text": "headline_changed",
}


def _err(status: int, code: str, message: str, details: str = "") -> HTTPException:
    return HTTPException(status, {"error": code, "message": message, "details": details})


async def _load_clip(session: AsyncSession, clip_id: str) -> ClipModel:
    clip = await session.get(ClipModel, clip_id)
    if not clip:
        raise _err(404, "clip_not_found", "That clip no longer exists.")
    return clip


@router.get("/clips/{clip_id}")
async def get_clip(clip_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    return clip_to_dict(await _load_clip(session, clip_id))


@router.patch("/clips/{clip_id}")
async def patch_clip(
    clip_id: str, payload: dict, session: AsyncSession = Depends(get_session)
) -> dict:
    """Apply an edit. Only whitelisted fields move; the changed set drives both
    the derived duration and the feedback events."""
    from services.clipper import feedback

    clip = await _load_clip(session, clip_id)
    before = {"start_time": clip.start_time, "end_time": clip.end_time}

    changed = apply_patch(clip, payload or {}, CLIP_PATCHABLE, CLIP_PATCHABLE_JSON)

    if "start_time" in changed or "end_time" in changed:
        start = max(0.0, float(clip.start_time or 0.0))
        end = float(clip.end_time or 0.0)
        if end <= start:
            raise _err(400, "invalid_range", "The clip's end must come after its start.")
        project = await session.get(ProjectModel, clip.project_id)
        if project and project.duration and end > float(project.duration):
            raise _err(
                400,
                "range_past_source",
                "That end time is past the end of the source video.",
            )
        clip.start_time, clip.end_time = start, end
        clip.duration = round(end - start, 3)
        # A hand-edited clip's stale render is worse than none: drop the paths
        # so the UI shows "needs re-render" instead of the old file.
        clip.preview_path = None

    if changed:
        await session.commit()
        for field in changed:
            event = _FIELD_EVENTS.get(field)
            if not event:
                continue
            payload_out: dict[str, Any] = {"field": field}
            if field in before:
                payload_out |= {"old": before[field], "new": getattr(clip, field)}
            await feedback.record(session, clip.id, clip.project_id, event, payload_out)

    return {"clip": clip_to_dict(clip), "changed": changed}


async def _set_status(
    session: AsyncSession, clip_id: str, status: str, event: str, payload: dict | None = None
) -> dict:
    from services.clipper import feedback

    clip = await _load_clip(session, clip_id)
    clip.status = status
    await session.commit()
    await feedback.record(session, clip.id, clip.project_id, event, payload)
    return clip_to_dict(clip)


@router.post("/clips/{clip_id}/approve")
async def approve_clip(clip_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    return await _set_status(session, clip_id, ClipStatus.approved.value, "approved")


@router.post("/clips/{clip_id}/reject")
async def reject_clip(
    clip_id: str, payload: dict | None = None, session: AsyncSession = Depends(get_session)
) -> dict:
    """A reject reason, when given, is the highest-signal feedback we get —
    it is stored verbatim on the event."""
    reason = (payload or {}).get("reason")
    return await _set_status(
        session,
        clip_id,
        ClipStatus.rejected.value,
        "rejected",
        {"reason": reason} if reason else None,
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
        )
        return {"job_id": job_id, "what": what}

    if what == "headline":
        from services.clipper.headline import generate_headline

        cand = {
            "start": clip.start_time,
            "end": clip.end_time,
            "text": clip.transcript_text or "",
            "words": (clip.transcript_segments or {}) if isinstance(clip.transcript_segments, dict) else {},
        }
        result = await generate_headline(
            cand,
            engine=settings.clipper_llm_engine or None,
            language=cfg.get("language") or "auto",
        )
        clip.headline_text = result.get("text") or clip.headline_text
        await session.commit()
        return {"clip": clip_to_dict(clip), "source": result.get("source")}

    if what == "captions":
        from services.clipper.captions import build_caption_plan

        transcript = {"segments": clip.transcript_segments or []}
        try:
            clip.caption_plan = build_caption_plan(
                {"start": clip.start_time, "end": clip.end_time, "text": clip.transcript_text or ""},
                transcript,
                preset_id=clip.caption_preset_id or cfg.get("caption_preset_id") or "bold_impact",
                max_words=3,
                position=cfg.get("caption_position") or "bottom",
                layout=clip.layout_plan or {},
            )
            await session.commit()
        except Exception as exc:
            logger.exception("caption regeneration failed")
            raise _err(500, "caption_failed", "Could not rebuild the captions.", str(exc)[:200])
        return {"clip": clip_to_dict(clip)}

    raise _err(400, "unknown_regenerate_target", f"Cannot regenerate '{what}'.")


@router.get("/clips/{clip_id}/preview-frame")
async def preview_frame(
    clip_id: str, t: float | None = None, session: AsyncSession = Depends(get_session)
) -> Response:
    """A single still with the captions burned in, for the editor.

    Reuses services/caption_overlays.render_preview_frame — the same code path
    the Caption Studio already uses, so what the user sees here matches what
    the export will produce.
    """
    from services.caption_overlays import render_preview_frame
    from services.clipper.captions import caption_plan_to_overlays

    clip = await _load_clip(session, clip_id)
    project = await session.get(ProjectModel, clip.project_id)
    source = (project.video_path if project else None) or ""
    if not source:
        raise _err(409, "source_missing", "The source video has not been downloaded yet.")

    # `t` is relative to the clip; the frame lives at clip.start + t in the source.
    offset = float(t if t is not None else 0.5)
    absolute = max(0.0, float(clip.start_time or 0.0) + max(0.0, offset))

    overlays: list[dict] = []
    if clip.caption_plan:
        try:
            overlays = caption_plan_to_overlays(clip.caption_plan)
        except Exception:
            logger.exception("could not build overlays for the preview frame")

    try:
        png = await _in_thread(lambda: render_preview_frame(source, overlays, absolute))
    except Exception as exc:
        logger.exception("preview frame render failed")
        raise _err(500, "preview_failed", "Could not render that frame.", str(exc)[:200])

    # Short cache: the editor re-requests on every scrub, but an edit must show
    # up immediately.
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "max-age=5"})


async def _in_thread(fn):
    import asyncio

    return await asyncio.get_event_loop().run_in_executor(None, fn)


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
    if not path or not Path(path).exists():
        raise _err(404, "no_preview", "This clip has no rendered preview yet.")
    return FileResponse(path, media_type="video/mp4", filename=f"{clip.id}.mp4")


@router.get("/clips/{clip_id}/export-file")
async def export_file(clip_id: str, session: AsyncSession = Depends(get_session)):
    """Download the final render."""
    from fastapi.responses import FileResponse

    clip = await _load_clip(session, clip_id)
    if not clip.export_path or not Path(clip.export_path).exists():
        raise _err(404, "no_export", "This clip has not been exported yet.")
    safe = "".join(c for c in (clip.title or clip.id) if c.isalnum() or c in " -_")[:60].strip()
    return FileResponse(
        clip.export_path, media_type="video/mp4", filename=f"{safe or clip.id}.mp4"
    )


@router.post("/clips/{clip_id}/export")
async def export_clip(clip_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    clip = await _load_clip(session, clip_id)
    clip.status = ClipStatus.exporting.value
    await session.commit()
    job_id = await job_queue.enqueue(
        project_id=clip.project_id,
        job_type=JobType.clipper_export.value,
        clip_id=clip.id,
    )
    return {"job_id": job_id, "clip_id": clip.id}


@router.post("/clips/{clip_id}/feedback")
async def record_feedback(
    clip_id: str, payload: dict, session: AsyncSession = Depends(get_session)
) -> dict:
    from services.clipper import feedback

    clip = await _load_clip(session, clip_id)
    event_type = (payload or {}).get("event_type") or ""
    try:
        event_id = await feedback.record(
            session, clip.id, clip.project_id, event_type, (payload or {}).get("payload")
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
    result = await session.execute(query.limit(500))
    return [clip_to_dict(c) for c in result.scalars().all()]
