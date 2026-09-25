"""
ClipForge — AI Stream Clipper: the two ways a clip row is taken for writing.

`claim_for_export` moved here verbatim from routers/clipper_clips.py, which was
at 499 lines when B1 (PRPs/clipper-master-plan-2026-09-24.md §4) had to add to
it. `lock_clip` is B1's; `begin_write` is B1-r's
(data/claude-master-20260924/codex-verdict-wave1.md, R4). The export submit —
`submit_export`, `reconcile_export`, `export_attempt`, `release_export_claim`
and the endpoint's answer — is R4b's (codex-verdict-wave2.md). At the end,
`_project_transcript` and `_headline_inputs`: the snapshot a slow edit (the
headline's model call) is compared against once it holds the lock (R1).

Both are SQLite, not Python: start_all.ps1 runs a second backend against the
same clipforge.db, so a mutex in this process would guard nothing.
"""

from __future__ import annotations

import logging
import re

from fastapi import HTTPException
from sqlalchemy import select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import async_session
from job_rows import add_job
from models import (ClipModel, ClipStatus, JobModel, JobStatus, JobType, ProjectModel,
                    TranscriptModel, _uuid)

logger = logging.getLogger("clipforge.clipper.mutations")

# SQLite primary result codes; an extended code carries one in its low byte.
_SQLITE_BUSY, _SQLITE_LOCKED = 5, 6


def _is_busy(exc: OperationalError) -> bool:
    """BUSY or LOCKED — another writer holds the lock — and nothing else.

    The code decides when the driver gives one (sqlite3 sets `sqlite_errorcode`
    on errors it raises); the message only when it does not. A "no such table"
    or a disk error reported as "try again" would hide a real fault.
    """
    code = getattr(exc.orig, "sqlite_errorcode", None)
    if code is not None:
        return code & 0xFF in (_SQLITE_BUSY, _SQLITE_LOCKED)
    msg = str(exc.orig).lower()
    return "database is locked" in msg or "database table is locked" in msg


async def begin_write(session: AsyncSession) -> None:
    """`BEGIN IMMEDIATE`, with a lock held elsewhere past busy_timeout (30 s)
    answered as 503 `database_busy` after a rollback, not a raw 500.

    The one place a clip edit or PATCH settings takes the lock (B1-r R4a), so
    B2's project path cannot grow a second variant of it. 503 rather than 409:
    nothing about the clip conflicts with the request; the same request succeeds
    once the other writer commits, which Retry-After says.
    """
    try:
        await session.execute(text("BEGIN IMMEDIATE"))
    except OperationalError as exc:
        if not _is_busy(exc):
            raise
        await session.rollback()
        raise HTTPException(503, {
            "error": "database_busy",
            "message": "Another save is still writing. Nothing was changed; try again.",
        }, headers={"Retry-After": "1"}) from exc


async def lock_clip(session: AsyncSession, clip_id: str) -> ClipModel | None:
    """Take SQLite's write lock, THEN read the clip — None if it is gone.

    An edit used to read the row, decide, and write it later: a claim or a
    rescore that committed in between was invisible to it, so a stale write
    could take a clip out of `exporting`, or land on a row a rescore had
    already deleted (a raw 500 from the flush). With the lock taken first no
    other writer, in either backend, can commit until this session commits or
    rolls back, so every check made after this call is a check on the row the
    write will land on. A refusal raised afterwards closes the session, which
    rolls the transaction back.

    Must be the session's first write: SQLite refuses a BEGIN inside an open
    transaction. Hold it only for the check and the write — a slow step (a
    model call) runs before this, and its snapshot is compared afterwards.
    """
    await begin_write(session)
    return await session.get(ClipModel, clip_id, populate_existing=True)


# States an export may be STARTED from. Deliberately not the strict machine an
# audit would draw, and the difference is the product rather than laziness:
#
#   `candidate` stays legal because the documented flow is review the board,
#   then Export — the runbook says so and `auto_export` enqueues candidates
#   directly, bypassing this endpoint entirely.
#
#   `exported` and `failed` stay legal because re-rendering after an edit is
#   what the clip editor is FOR. Requiring a separate re-render endpoint would
#   make the ordinary case the awkward one.
#
# What is refused is the pair that can only be a mistake: a clip already
# rendering (two jobs writing one file, progress oscillating between them) and
# one that was rejected on purpose.
_EXPORTABLE_FROM = (
    ClipStatus.candidate.value,
    ClipStatus.approved.value,
    ClipStatus.exported.value,
    ClipStatus.failed.value,
)


def _live_export_job(clip_id: str):
    """The clip's queued or running export job, as a SELECT of its id."""
    return (select(JobModel.id)
            .where(JobModel.clip_id == clip_id,
                   JobModel.type == JobType.clipper_export.value,
                   JobModel.status.in_((JobStatus.queued.value, JobStatus.running.value))))


async def claim_for_export(session: AsyncSession, clip_id: str, job_id: str, *also) -> bool:
    """Take a clip for the export attempt `job_id`. True only for the caller
    that actually got it. Does NOT commit: `submit_export` commits it together
    with the job row (R4b).

    THE CONDITIONAL UPDATE IS THE LOCK, and it is a named function so it can be
    tested the way the job queue's claim is — through the endpoint, eight
    gathered requests do not interleave enough to catch anything, and a test
    that cannot fail against the old read-then-write code proves nothing.

    `exporting` is deliberately absent from the source states: a second export
    on a running one is two jobs writing one file, with progress oscillating
    between two writers. So is any clip with a LIVE export job, whatever its
    status: `handle_export` commits `exported` before its job is terminal, so
    the status alone let a second render start in that window (Codex, wave 2).
    `also` narrows it further (auto-export: untouched candidates only).
    """
    result = await session.execute(
        update(ClipModel)
        .where(ClipModel.id == clip_id,
               ClipModel.status.in_(_EXPORTABLE_FROM),
               ~_live_export_job(clip_id).exists(),
               *also)
        .values(status=ClipStatus.exporting.value, export_job_id=job_id)
    )
    return result.rowcount == 1


# WITHDRAWN (Codex Bfix verdict), and still not to be brought back: taking an
# `exporting` claim with no job again once it was 120 s old, and giving a
# failed enqueue's clip back the status read BEFORE its claim. Age is not an
# attempt's identity (a SUSPENDED request resumed and made a second job,
# codex-bfix-probes.py probe 1), and the give-back overwrote an edit accepted
# in between (probe 2). The claim, `export_job_id` and the job row are now one
# commit: a failure before it leaves nothing to give back, and orphans from
# before it are released by a person (scripts/release_stuck_exports.py).


async def submit_export(session: AsyncSession, clip_id: str, *, job_id: str, origin: str,
                        also=()) -> dict:
    """ONE transaction: the claim, `export_job_id` and the job row, for manual
    and auto-export alike (R4b). -> {"outcome", "job_status"?, "clip_status"?}.

    Under the write lock, before anything is written: an `attempt_id` that
    already names a job is that job (a retry after a lost response gets it back
    with its REAL status), or a conflict when it names another clip's or another
    kind of job. A refusal is decided under the same lock and rolled back.
    """
    await begin_write(session)
    job = await session.get(JobModel, job_id, populate_existing=True)
    if job is not None:
        mine, status = (job.clip_id == clip_id
                        and job.type == JobType.clipper_export.value), job.status
        await session.rollback()
        return ({"outcome": "existing", "job_status": status} if mine
                else {"outcome": "attempt_id_conflict"})
    if not await claim_for_export(session, clip_id, job_id, *also):
        clip = await session.get(ClipModel, clip_id, populate_existing=True)
        status = clip.status if clip else None
        live = await session.scalar(_live_export_job(clip_id).limit(1))
        await session.rollback()
        if clip is None:
            return {"outcome": "clip_not_found"}
        busy = status == ClipStatus.exporting.value or live is not None
        return {"outcome": "already_exporting" if busy else "not_exportable",
                "clip_status": status}
    project_id = await session.scalar(select(ClipModel.project_id)
                                      .where(ClipModel.id == clip_id))
    await add_job(session, project_id=project_id, job_type=JobType.clipper_export.value,
                  clip_id=clip_id, metadata={"origin": origin}, job_id=job_id)
    await session.commit()
    return {"outcome": "queued", "job_status": JobStatus.queued.value}


async def reconcile_export(clip_id: str, job_id: str) -> dict:
    """What a submit that RAISED actually did, read by its job id.

    An exception at the commit does not prove a rollback, so the answer is read
    under the write lock: a commit still in flight on another connection holds
    that lock, and whatever is read after taking it is final. Job present ->
    its real status; absent -> the transaction did not commit, and the claim was
    in it. Raises when it cannot read — the caller's "unknown", never "absent".
    """
    async with async_session() as session:
        await begin_write(session)
        job = await session.get(JobModel, job_id)
        found = (job.clip_id, job.type, job.status) if job is not None else None
        await session.rollback()
    if found is None:
        return {"outcome": "not_queued"}
    if found[:2] != (clip_id, JobType.clipper_export.value):
        return {"outcome": "attempt_id_conflict"}
    return {"outcome": "existing", "job_status": found[2]}


async def export_attempt(clip_id: str, *, job_id: str, origin: str, also=()) -> dict:
    """`submit_export` in its own session, reconciled by `job_id` when it
    raised. A busy lock at its BEGIN (503 `database_busy`, nothing written)
    propagates; a failure of the reconciliation itself is outcome `unknown`."""
    try:
        async with async_session() as session:
            return await submit_export(session, clip_id, job_id=job_id, origin=origin,
                                       also=also)
    except HTTPException:
        raise
    except Exception:
        logger.exception("export %s of clip %s raised; reconciling", job_id, clip_id)
    try:
        return await reconcile_export(clip_id, job_id)
    except Exception:
        logger.exception("export %s of clip %s: outcome unknown", job_id, clip_id)
        return {"outcome": "unknown"}


async def release_export_claim(session: AsyncSession, job: JobModel) -> int:
    """Free the clip of an export job that ended without publishing — in the
    caller's transaction, after the caller WON the job's own transition.

    Only a `clipper_export`, only the clip's current attempt (`export_job_id`),
    and only out of `exporting`: a preview, a superseded attempt, or a clip the
    attempt already published (`exported` is committed before the job ends)
    is left as it is (Codex, wave 2 (a)). -> rowcount.
    """
    if job.type != JobType.clipper_export.value or not job.clip_id:
        return 0
    result = await session.execute(
        update(ClipModel)
        .where(ClipModel.id == job.clip_id, ClipModel.export_job_id == job.id,
               ClipModel.status == ClipStatus.exporting.value)
        .values(status=ClipStatus.failed.value))
    return result.rowcount


_ATTEMPT_ID = re.compile(r"[0-9a-f]{12}")


def attempt_job_id(payload: dict | None) -> str:
    """The export's job id: the client's optional `attempt_id` (12 lowercase
    hex, the shape of every job id), or a new one. Same id on a retry after a
    lost response; a new one for a new re-export intent."""
    attempt = (payload or {}).get("attempt_id")
    if attempt is None:
        return _uuid()
    if not isinstance(attempt, str) or not _ATTEMPT_ID.fullmatch(attempt):
        raise HTTPException(400, {"error": "invalid_attempt_id", "details": "",
                                  "message": "attempt_id must be 12 lowercase hex characters."})
    return attempt


_REFUSALS = {
    "clip_not_found": (404, "That clip no longer exists.", ""),
    "already_exporting": (409, "That clip is already rendering.",
                          "Wait for the running export to finish, or cancel its job."),
    "not_exportable": (409, "A {clip_status} clip cannot be exported.",
                       "Approve it first if you want it rendered."),
    "attempt_id_conflict": (409, "That attempt id belongs to another job.",
                            "Send a new attempt_id for a new export."),
    "not_queued": (503, "The render was not queued, and the clip is unchanged.", "Try again."),
    "unknown": (503, "Could not confirm whether the render was queued.",
                "Retry with the same attempt_id to find out."),
}


def export_response(clip_id: str, job_id: str, got: dict) -> dict:
    """The endpoint's answer for `export_attempt`'s outcome; refusals raise."""
    if got["outcome"] in ("queued", "existing"):
        return {"job_id": job_id, "clip_id": clip_id, "job_status": got["job_status"]}
    status, message, details = _REFUSALS[got["outcome"]]
    code = {"not_queued": "export_not_queued",
            "unknown": "export_outcome_unknown"}.get(got["outcome"], got["outcome"])
    raise HTTPException(status, {"error": code, "message": message.format(**got),
                                 "details": details},
                        headers={"Retry-After": "1"} if status == 503 else None)


# ── What a slow edit is compared against ─────────────────────────────────────
# Moved verbatim from routers/clipper_clips.py for its 500-line limit (B1-r);
# the router re-imports both under the same names.


async def _project_transcript(session: AsyncSession, project_id: str) -> dict:
    """The project's transcript, in the shape the scorer passed at build time.

    THE CANONICAL SOURCE FOR A CLIP'S WORDS, and until 2026-08-17 the editor
    used a different one: both regeneration paths read
    `clip.transcript_segments`, a column that exists on the model and that
    NOTHING in the clipper has ever written. So rebuilding captions produced an
    empty plan and rebuilding a headline gave the model no words to work from —
    silently, because an empty transcript is a legitimate state for a clip with
    no speech.

    Returns the WHOLE transcript rather than a slice: `build_caption_plan` and
    `_clip_words` both take the candidate window and cut it themselves, and
    handing them a pre-cut one would be a second place for the window
    arithmetic to disagree.
    """
    row = (await session.execute(
        select(TranscriptModel)
        .where(TranscriptModel.project_id == project_id)
        .limit(1)
        # Re-read under the lock, the headline compares it with its snapshot.
        .execution_options(populate_existing=True)
    )).scalar_one_or_none()
    if not row or not row.segments:
        return {"segments": []}
    return {"language": row.language, "segments": row.segments,
            "full_text": row.full_text}


async def _headline_inputs(session: AsyncSession, clip: ClipModel,
                           project: ProjectModel) -> dict:
    """Everything a regenerated headline depends on, taken before the model call
    and again under the lock: equal, or the result is for a clip that is gone.
    Start/end alone let a headline for the old `transcript_text` through."""
    from services.clipper.captions import _clip_words

    transcript = await _project_transcript(session, clip.project_id)
    return {
        "target": clip.headline_text,
        "cand": {
            "start": clip.start_time,
            "end": clip.end_time,
            "text": clip.transcript_text or "",
            # The same slicer `build_caption_plan` uses, so a regenerated
            # headline sees exactly the words a regenerated caption would.
            # `generate_headline` wants a LIST of word dicts; this used to hand
            # it `clip.transcript_segments`, a column nothing writes, guarded by
            # an isinstance check that made the empty case look deliberate.
            "words": _clip_words({"start": clip.start_time, "end": clip.end_time},
                                 transcript),
        },
        "engine": settings.clipper_llm_engine or None,
        "language": (project.clipper_settings or {}).get("language") or "auto",
    }
