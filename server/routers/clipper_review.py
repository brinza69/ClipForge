"""
ClipForge Worker — AI Stream Clipper: the blind review session.

Mounted under the same /api/clipper prefix as the other clipper routers. The
rubric, the shuffle and the membership all live in `services/clipper/blind_review.py`;
this file is the HTTP edge and the disk, and it exists to keep one rule:
**membership never crosses the wire while the review is running.**

The session is a JSON file rather than a table. It is the disk-only pattern
CLAUDE.md rule 4 describes for a feature project: nothing else joins against it,
it is written once and appended to, and a table would mean a migration for a
shape that is still being learned.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_session
from models import ClipModel, ClipperEvent
from services.clipper import feedback as feedback_mod
from services.clipper import blind_review as review_mod
from services.clipper import storage

logger = logging.getLogger("clipforge.clipper.review")

router = APIRouter(prefix="/api/clipper/review", tags=["clipper"])


def _root():
    path = settings.clipper_dir / "_review"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _session_path(session_id: str):
    # The id is generated here and never taken from a client, but this is the
    # one place a path is built from a string, and `safe_join` exists because
    # that is exactly where traversal gets in.
    if not session_id.isalnum() or len(session_id) > 40:
        raise HTTPException(status_code=400, detail="bad session id")
    return _root() / f"{session_id}.json"


def _load(session_id: str) -> dict:
    path = _session_path(session_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="no such review session")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        raise HTTPException(status_code=500,
                            detail=f"unreadable session: {exc}") from exc


def _save(session: dict) -> None:
    storage.atomic_write_json(_session_path(session["session_id"]), session)


class NewSession(BaseModel):
    project_ids: list[str]
    seed: int | None = None


class Answer(BaseModel):
    review_item_id: str
    worth_exporting: str
    self_contained: str
    hook: str
    start_boundary: str
    end_boundary: str
    technical_problem: str
    reject_reasons: list[str] = []
    note: str = ""
    watch_fraction: float | None = None


@router.get("/rubric")
async def get_rubric() -> dict:
    """The questions, so the page renders what the service will accept.

    One definition, not two. A page with its own copy of the options drifts from
    the validator, and the drift shows up as a reviewer's answer being refused
    after they have already watched the clip.
    """
    return {
        "rubric_version": review_mod.RUBRIC_VERSION,
        "questions": [dict(q, options=list(q["options"]))
                      for q in review_mod.QUESTIONS],
        "reject_reasons": list(review_mod.REJECT_REASONS),
    }


@router.post("")
async def start_session(body: NewSession,
                        session: AsyncSession = Depends(get_session)) -> dict:
    if not body.project_ids:
        raise HTTPException(status_code=400, detail="pick at least one project")

    rows = await session.execute(
        select(ClipModel.id, ClipModel.project_id, ClipModel.rank_position,
               ClipModel.shadow_rank, ClipModel.shadow_run_id)
        .where(ClipModel.project_id.in_(body.project_ids))
        .where(ClipModel.rank_position.is_not(None)
               | ClipModel.shadow_rank.is_not(None))
    )
    picked = [{"clip_id": r[0], "project_id": r[1], "rank_position": r[2],
               "shadow_rank": r[3], "shadow_run_id": r[4]}
              for r in rows.all()]
    if not picked:
        raise HTTPException(
            status_code=409,
            detail="no board to review: score a project in a shadow mode first")

    out = review_mod.create(uuid.uuid4().hex[:16], picked, seed=body.seed)
    _save(out)
    logger.info("clipper review %s: %d items over %d projects",
                out["session_id"], len(out["order"]), len(body.project_ids))
    return {"session_id": out["session_id"],
            "rubric_version": out["rubric_version"],
            **review_mod.progress(out)}


@router.get("/{session_id}/next")
async def get_next(session_id: str,
                   session: AsyncSession = Depends(get_session)) -> dict:
    state = _load(session_id)
    handle = review_mod.next_item(state)
    if handle is None:
        return {"done": True, **review_mod.progress(state)}

    item = next(i for i in state["items"] if i["review_item_id"] == handle)
    clip = await session.get(ClipModel, item["clip_id"])
    if clip is None:
        raise HTTPException(status_code=410,
                            detail="the clip this item points at is gone")

    return {"done": False,
            "item": review_mod.public_item(state, handle, {
                "start_time": clip.start_time, "end_time": clip.end_time,
                "duration": clip.duration, "preview_path": clip.preview_path,
                "transcript_text": clip.transcript_text}),
            **review_mod.progress(state)}


@router.post("/{session_id}/answer")
async def post_answer(session_id: str, body: Answer,
                      session: AsyncSession = Depends(get_session)) -> dict:
    state = _load(session_id)
    payload = body.model_dump()
    handle = payload.pop("review_item_id")
    try:
        stored = review_mod.record(state, handle, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _save(state)

    item = next(i for i in state["items"] if i["review_item_id"] == handle)
    # `reviewed`, deliberately outside `feedback._DECISIVE`. A person answering
    # an evaluation question about a clip they did not ask for has not decided
    # to publish it, and the ranker must not read it as approval — that is the
    # confusion that gave `training_rows()` 43 rows all labelled 1.0.
    #
    # `origin` stays `manual` because origin describes the ACTOR, and a human
    # answered. The CONTEXT goes in the payload; folding "why they were asked"
    # into "who answered" would rebuild the same ambiguity somewhere new.
    await feedback_mod.record(
        session, item["clip_id"], item["project_id"],
        ClipperEvent.reviewed.value,
        payload={"context": "blind_eval",
                 "schema_version": review_mod.SCHEMA_VERSION,
                 "rubric_version": state.get("rubric_version"),
                 "review_session_id": session_id,
                 "review_item_id": handle,
                 **stored},
        origin=feedback_mod.ORIGIN_MANUAL)
    await session.commit()

    return {"ok": True, **review_mod.progress(state)}


@router.get("/{session_id}/item/{review_item_id}/video")
async def item_video(session_id: str, review_item_id: str,
                     session: AsyncSession = Depends(get_session)):
    """The clip's preview, addressed by the session handle.

    Not `/clips/{clip_id}/preview-file`, which is the same bytes: that URL puts
    the clip id in the page's DOM and in the browser's network log, and the clip
    id is what the reviewer's own board is addressed by. One glance at the board
    for that id tells them whether the clip is ranked, which is the whole blind
    for the price of opening devtools.
    """
    from fastapi.responses import FileResponse

    state = _load(session_id)
    item = next((i for i in state.get("items") or ()
                 if i.get("review_item_id") == review_item_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="no such review item")

    clip = await session.get(ClipModel, item["clip_id"])
    path = (clip.preview_path or clip.export_path) if clip else None
    if not path or not storage.is_usable_output(path):
        raise HTTPException(status_code=404,
                            detail="this clip has no rendered preview yet")
    # Named after the HANDLE, not the clip: a download or a saved file that
    # carries the clip id walks the leak out of the browser.
    return FileResponse(path, media_type="video/mp4",
                        filename=f"{review_item_id}.mp4")


@router.get("/{session_id}/result")
async def get_result(session_id: str) -> dict:
    """The comparison, and the membership that was hidden until now.

    Available whenever it is asked for, including mid-session. Withholding it
    would only push the reviewer to reconstruct it from the board, and a partial
    tally is honestly labelled by `reviewed` counts.
    """
    state = _load(session_id)
    return {"session_id": session_id,
            "rubric_version": state.get("rubric_version"),
            "seed": state.get("seed"),
            **review_mod.progress(state),
            "tally": review_mod.tally(state),
            "items": review_mod.reveal(state)}
