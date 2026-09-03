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

import asyncio
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
from services.clipper import review_media
from services.clipper import review_lock
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
        select(ClipModel)
        .where(ClipModel.project_id.in_(body.project_ids))
        .where(ClipModel.rank_position.is_not(None)
               | ClipModel.shadow_rank.is_not(None))
    )
    clips = rows.scalars().all()
    if not clips:
        raise HTTPException(
            status_code=409,
            detail="no board to review: score a project in a shadow mode first")

    missing = sorted(set(body.project_ids) - {c.project_id for c in clips})
    if missing:
        raise HTTPException(status_code=409, detail={"projects_without_board": missing})
    # A preserved export can carry an older rank. Do not compare boards that
    # never coexisted; all-unknown historical runs stay explicitly unknown.
    for project_id in set(body.project_ids):
        group = [c for c in clips if c.project_id == project_id]
        runs = {c.selection_run_id for c in group}
        shadows = {c.shadow_run_id for c in group if c.shadow_run_id is not None}
        if (len(runs) > 1 or len(shadows) > 1
                or (None not in runs and shadows and runs != shadows)
                or any(c.shadow_rank is not None and c.selection_run_id is not None
                       and c.shadow_run_id != c.selection_run_id for c in group)):
            raise HTTPException(status_code=409, detail="board_contains_different_selection_runs")
    picked = []
    for clip in clips:
        row = {"clip_id": clip.id, "project_id": clip.project_id,
               "rank_position": clip.rank_position, "shadow_rank": clip.shadow_rank,
               "shadow_run_id": clip.shadow_run_id, "selection_run_id": clip.selection_run_id,
               "export_path": clip.export_path, "start_time": clip.start_time,
               "end_time": clip.end_time, "duration": clip.duration,
               "transcript_text": clip.transcript_text}
        try:
            row["media"] = await asyncio.to_thread(review_media.capture, row)
        except review_media.MediaChanged as exc:
            raise HTTPException(status_code=409, detail=f"review_not_ready: {exc}") from exc
        picked.append(row)
    out = review_mod.create(uuid.uuid4().hex[:16], picked, seed=body.seed)
    _save(out)
    logger.info("clipper review %s: %d items over %d projects",
                out["session_id"], len(out["order"]), len(body.project_ids))
    return {"session_id": out["session_id"],
            "rubric_version": out["rubric_version"],
            **review_mod.progress(out)}


@router.get("/{session_id}/next")
async def get_next(session_id: str) -> dict:
    state = _load(session_id)
    if not review_mod.sealed_review(state):
        raise HTTPException(status_code=409, detail="historical_review_read_only")
    handle = review_mod.next_item(state)
    if handle is None:
        if not review_mod.complete(state):
            raise HTTPException(status_code=409, detail="review_state_incomplete")
        return {"done": True, **review_mod.progress(state)}

    item = next(i for i in state["items"] if i["review_item_id"] == handle)
    await _verified_media(item)

    return {"done": False,
            "item": review_mod.public_item(state, handle, item["media"]["presentation"]),
            **review_mod.progress(state)}


@router.post("/{session_id}/answer")
async def post_answer(session_id: str, body: Answer,
                      session: AsyncSession = Depends(get_session)) -> dict:
    # Covers the awaits too: both backend processes serve this same directory.
    path = _session_path(session_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="no such review session")
    try:
        with review_lock.answer_lock(path):
            return await _store_answer(session_id, body, session)
    except review_lock.ReviewBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


async def _store_answer(session_id: str, body: Answer, session: AsyncSession) -> dict:
    state = _load(session_id)
    if not review_mod.sealed_review(state):
        raise HTTPException(status_code=409, detail="historical_review_read_only")
    payload = body.model_dump()
    handle = payload.pop("review_item_id")
    item = next((i for i in state.get("items", [])
                 if i.get("review_item_id") == handle), None)
    if item is None:
        raise HTTPException(status_code=404, detail="no such review item")
    await _verified_media(item)
    try:
        stored = review_mod.record(state, handle, payload)
    except review_mod.ReviewConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # These bytes, not whichever export the same DB row may point at later.
    media = item["media"]
    stored["presented_media"] = {
        "sha256": media["sha256"], "sidecar_sha256": media["sidecar_sha256"],
        "render_version": media["render_version"], "selection_run_id": media["selection_run_id"],
        "start": media["presentation"]["start_time"], "end": media["presentation"]["end_time"],
    }
    _save(state)

    # `reviewed`, deliberately outside `feedback._DECISIVE`. A person answering
    # an evaluation question about a clip they did not ask for has not decided
    # to publish it, and the ranker must not read it as approval — that is the
    # confusion that gave `training_rows()` 43 rows all labelled 1.0.
    #
    # `origin` stays `manual` because origin describes the ACTOR, and a human
    # answered. The CONTEXT goes in the payload; folding "why they were asked"
    # into "who answered" would rebuild the same ambiguity somewhere new.
    # The session is the source of truth. A retry after a DB write failure can
    # mirror the same saved answer without appending it twice after a lost HTTP
    # response. The per-session process lock covers this check and write.
    events = await feedback_mod.events_for_clip(session, item["clip_id"])
    matches = [e for e in events if e["event_type"] == ClipperEvent.reviewed.value
               and e["payload"].get("review_session_id") == session_id
               and e["payload"].get("review_item_id") == handle]
    if not matches:
        await feedback_mod.record(
            session, item["clip_id"], item["project_id"],
            ClipperEvent.reviewed.value,
            payload={"context": "blind_eval",
                     "schema_version": state["schema_version"],
                     "rubric_version": state.get("rubric_version"),
                     "review_session_id": session_id,
                     "review_item_id": handle,
                     **stored},
            origin=feedback_mod.ORIGIN_MANUAL)

    return {"ok": True, **review_mod.progress(state)}


@router.get("/{session_id}/item/{review_item_id}/video")
async def item_video(session_id: str, review_item_id: str):
    """The snapshotted full export, addressed by the session handle.

    Not `/clips/{clip_id}/export-file`, which is the same bytes: that URL puts
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

    # THE EXPORT, never the preview. `render_preview` caps at 12 seconds by
    # design — it is a proxy for the editor — and a review session run on it
    # asks "is this clip worth exporting" about the first twelve seconds of a
    # sixty-second clip. Measured on the first real session: 14 of 15 answers
    # said the clip ended too early, which was true of the video and false of
    # the clip, and every one of the five payoffs sat past the cut.
    #
    # So there is no fallback. A missing export is an answerable 409; a silent
    # downgrade to a truncated proxy is what invalidated a whole session.
    path = await _verified_media(item)
    # Named after the HANDLE, not the clip: a download or a saved file that
    # carries the clip id walks the leak out of the browser.
    return FileResponse(path, media_type="video/mp4",
                        filename=f"{review_item_id}.mp4")


async def _verified_media(item: dict):
    try:
        return await asyncio.to_thread(review_media.verify, item.get("media"))
    except review_media.MediaChanged as exc:
        raise HTTPException(status_code=409, detail=f"review_media_unavailable: {exc}") from exc


@router.get("/{session_id}/result")
async def get_result(session_id: str) -> dict:
    """The comparison, and the membership that was hidden until now.

    No partial tally: even without clip ids it can reveal which board selected
    the last answered item. Historical results stay readable, explicitly not
    covered by the new policy, and their sessions cannot accept more answers.
    """
    state = _load(session_id)
    try:
        items = review_mod.reveal(state)
    except review_mod.ReviewConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"session_id": session_id,
            "historical": review_mod.historical_review(state),
            "blinding_policy": state.get("blinding_policy"),
            "rubric_version": state.get("rubric_version"),
            "seed": state.get("seed"),
            **review_mod.progress(state),
            "tally": review_mod.tally(state),
            "items": items}
