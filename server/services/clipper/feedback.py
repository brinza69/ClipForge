"""
ClipForge — AI Stream Clipper: append-only feedback event store.

Every user action on a candidate (preview, approve, reject, boundary nudge,
export) appends a row to `clip_feedback`. Nothing here ever updates or deletes:
the log is the training set, and rewriting history would silently change what
the ranker learned from.

`training_rows()` folds the log back into (features, label) pairs joined to the
`feature_vector` frozen on the clip at scoring time — never features recomputed
by newer code, which would train the model on inputs the user never saw.

This module is the only place that talks to the DB in the clipper service
package; the ranker itself (ranker.py) stays pure.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import ClipFeedbackModel, ClipModel, ClipperEvent

logger = logging.getLogger("clipforge.clipper.feedback")

EVENT_TYPES: tuple[str, ...] = tuple(e.value for e in ClipperEvent)

# The only events that carry a judgement. `deleted` is deliberately NOT a label:
# users delete for housekeeping (disk, duplicates) as often as for quality, so
# treating it as a negative would poison the training set.
LABEL_EXPORTED = 1.0
LABEL_APPROVED = 0.75
LABEL_REJECTED = 0.0

_DECISIVE = ("exported", "approved", "rejected")

# Boundary edits are the cheapest proxy for "the model got the window wrong".
_BOUNDARY_EVENTS = ("start_changed", "end_changed")


# ── Writing ──────────────────────────────────────────────────────────────────

async def record(
    session: AsyncSession,
    clip_id: str,
    project_id: str | None,
    event_type: str,
    payload: dict | None = None,
) -> str:
    """Append one feedback event and return its new row id.

    Raises ValueError for an event type outside EVENT_TYPES — an unknown type
    would be invisible to every reader below and silently lost.
    """
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown clipper event type: {event_type!r}")
    if not clip_id:
        raise ValueError("clip_id is required")

    row = ClipFeedbackModel(
        clip_id=clip_id,
        project_id=project_id,
        event_type=event_type,
        payload=payload,
    )
    session.add(row)
    # Flush before commit so the Python-side uuid default is assigned while the
    # instance is guaranteed live, whatever expire_on_commit is set to.
    await session.flush()
    new_id = row.id
    await session.commit()
    logger.debug(f"clipper feedback {event_type} clip={clip_id} id={new_id}")
    return new_id


# ── Reading ──────────────────────────────────────────────────────────────────

def _event_dict(row: ClipFeedbackModel) -> dict[str, Any]:
    return {
        "id": row.id,
        "clip_id": row.clip_id,
        "project_id": row.project_id,
        "event_type": row.event_type,
        "payload": row.payload or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def events_for_clip(session: AsyncSession, clip_id: str) -> list[dict]:
    """Every event for one clip, oldest first (the order the labellers below
    assume)."""
    if not clip_id:
        return []
    result = await session.execute(
        select(ClipFeedbackModel)
        .where(ClipFeedbackModel.clip_id == clip_id)
        .order_by(ClipFeedbackModel.created_at, ClipFeedbackModel.id)
    )
    return [_event_dict(r) for r in result.scalars().all()]


# ── Labelling ────────────────────────────────────────────────────────────────

def label_for_events(events: list[str]) -> float | None:
    """Fold a clip's event-type history (oldest first) into a training label.

    None means "no decisive event" — the clip was never reviewed, and an
    unreviewed clip is not a negative example.

    An export outranks everything: nobody exports a clip they dislike, whatever
    they clicked first. Otherwise the LAST approve/reject wins, so approving and
    then changing your mind labels the clip a reject rather than the max of the
    two.
    """
    if not events:
        return None
    decisive = [e for e in events if e in _DECISIVE]
    if not decisive:
        return None
    if "exported" in decisive:
        return LABEL_EXPORTED
    return LABEL_APPROVED if decisive[-1] == "approved" else LABEL_REJECTED


def edit_distance_seconds(events: list[dict]) -> float:
    """Total seconds of boundary correction across a clip's event dicts.

    The offline eval reports the mean of this: if the ranker is picking good
    windows, users should be dragging the handles less over time. Payload shape
    is {"old": float, "new": float}; anything unparseable contributes 0.
    """
    total = 0.0
    for ev in events or []:
        if not isinstance(ev, dict) or ev.get("event_type") not in _BOUNDARY_EVENTS:
            continue
        payload = ev.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        try:
            old = float(payload.get("old"))  # type: ignore[arg-type]
            new = float(payload.get("new"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        delta = abs(new - old)
        if math.isfinite(delta):
            total += delta
    return total


# ── Training set ─────────────────────────────────────────────────────────────

async def training_rows(session: AsyncSession) -> list[dict]:
    """Labelled rows for ranker.train / ranker.evaluate.

    [{"clip_id","features","label","group"}] — `group` is the project id, so the
    ranker can score within-source ordering (comparing a clip from a podcast
    against one from a gaming stream says nothing useful).

    Rows come back in each project's heuristic rank order (rank_position, nulls
    last), which is what ranker.evaluate uses as the baseline ordering to beat.
    Clips with no decisive event or no frozen feature_vector are excluded.
    """
    fb = await session.execute(
        select(ClipFeedbackModel.clip_id, ClipFeedbackModel.event_type)
        .order_by(ClipFeedbackModel.created_at, ClipFeedbackModel.id)
    )
    history: dict[str, list[str]] = {}
    for clip_id, event_type in fb.all():
        if clip_id:
            history.setdefault(clip_id, []).append(event_type)

    labels = {cid: label_for_events(evs) for cid, evs in history.items()}
    labelled = {cid: lab for cid, lab in labels.items() if lab is not None}
    if not labelled:
        return []

    clips = await session.execute(
        select(
            ClipModel.id,
            ClipModel.project_id,
            ClipModel.feature_vector,
        )
        # Filtered by id in Python rather than a huge IN (...) list: SQLite caps
        # bound parameters, and the clips table is small enough to scan.
        .where(ClipModel.feature_vector.is_not(None))
        .order_by(
            ClipModel.project_id,
            # SQLite sorts NULLs first ascending; this pushes unranked clips to
            # the end so the incoming order really is the heuristic ranking.
            ClipModel.rank_position.is_(None),
            ClipModel.rank_position,
            ClipModel.created_at,
        )
    )

    rows: list[dict] = []
    for clip_id, project_id, features in clips.all():
        if clip_id not in labelled:
            continue
        if not isinstance(features, dict) or not features:
            continue
        rows.append(
            {
                "clip_id": clip_id,
                "features": features,
                "label": float(labelled[clip_id]),
                "group": project_id,
            }
        )
    logger.info(f"clipper training set: {len(rows)} labelled rows")
    return rows
