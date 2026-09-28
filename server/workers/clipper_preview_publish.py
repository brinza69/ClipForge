"""Where a preview's file is published, and which ATTEMPT it then belongs to.

Moved out of `clipper_render_jobs` (which re-exports these names) when BURST R1 grew it
(codex-verdict-next-23/24).

A preview is published at an IMMUTABLE per-attempt path (`attempt_path`), and the transaction selects
it: `preview_path` AND `preview_record` — the attempt's identity and its caption report — together. So:
- a failed commit leaves the previous file and its report selected; the new file is simply unselected
  (the handler deletes it), never a new file under an old report;
- nothing is renamed over a selected file, so no rollback is needed on disk;
- the attempt is `(job_id, attempt_count, worker)` as the queue CLAIMED it for this invocation (R1c): a
  job id alone is not an attempt, since a recovered job runs again under another `attempt_count`, and
  the row read later may already be a newer attempt's;
- at publication the job row must still be THIS attempt's — running, same worker and attempt, a live
  lease — or nothing is selected, and an attempt older than the selected one never replaces it.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from sqlalchemy import select, update

from database import async_session
from job_attempt import CLAIMED_ATTEMPT
from models import ClipModel, ClipStatus, JobModel, JobStatus, ProjectModel
from services.clipper.clip_mutations import begin_write
from services.clipper.project_attempts import (DISCARD_INPUTS_CHANGED, DISCARD_NEWER_EXPORT,
                                               DISCARD_NEWER_PREVIEW)
from workers.clipper_captions import _caption_inputs, _caption_warnings

# Not a discard marker on the job row: the row belongs to another attempt (or to nobody) by then.
LOST_ATTEMPT = "attempt_not_current"


def _preview_inputs(clip, project) -> tuple:
    """Everything the preview's PICTURE was decided from (D2r-2 K7).

    The caption inputs — window, transcript text, caption plan and preset, the
    burned-captions answer, the layout plan, and the project's whole
    `clipper_settings` (trim, dynamic edit, layout and caption policy, preset,
    position, watermark, layout mode, face size, chat) — plus what
    `_layout_plan`, `_dynamic_plan` and the renderer read besides: the clip's
    content type and the project's dimensions, source file and content type.
    Left out, with the reasons in D2r2-result.md: the analysis artefacts and the
    transcript row (immutable after analysis, K9), `headline_text` (carried by
    `_candidate`, drawn by nothing), `project.fps` (a preview renders at
    PREVIEW_FPS) and the content confidence/origin (shadow profile only).
    """
    return (_caption_inputs(clip, project), clip.content_type,
            project.width, project.height, project.video_path,
            project.content_type, project.content_type_override,
            # SC3: a changed source-caption treatment or layer choice is a changed picture
            getattr(clip, "source_caption_treatment", None), getattr(clip, "caption_layer", None))


def capture_attempt(job_id: str) -> dict | None:
    """`{job_id, attempt, worker}` of THIS invocation, as the queue's claim fixed it (R1c).

    Never the job row now: a takeover between the claim and that read lent an old handler the new
    attempt's identity, and it published under it (codex-verdict-next-25 §1). None when this task
    runs no claim of this job — then nothing it renders may be selected.
    """
    claimed = CLAIMED_ATTEMPT.get()
    if claimed is None or claimed.job_id != job_id:
        return None
    return {"job_id": claimed.job_id, "attempt": claimed.attempt, "worker": claimed.worker}


def attempt_path(published: Path, ident: dict) -> Path:
    """`<clip>.<job>-a<attempt>.mp4` beside the legacy `<clip>.mp4`: never a selected file's name."""
    return published.with_name(f"{published.stem}.{ident['job_id']}-a{ident['attempt']}{published.suffix}")


async def _still_this_attempt(session, ident: dict) -> bool:
    job = await session.get(JobModel, ident["job_id"], populate_existing=True)
    return (job is not None and job.status == JobStatus.running.value
            and job.worker_id == ident["worker"] and int(job.attempt_count or 0) == ident["attempt"]
            and job.lease_expires_at is not None and job.lease_expires_at > datetime.utcnow())


async def _older_than_selected(session, row: ClipModel, ident: dict) -> bool:
    """True when the selected file belongs to an attempt later than this one: its job was created
    later, or it is a later attempt of the same job (BURST R1)."""
    rec = row.preview_record if isinstance(row.preview_record, dict) else None
    if not rec or not rec.get("job_id"):
        return False
    if rec["job_id"] == ident["job_id"]:
        return int(rec.get("attempt") or 0) > int(ident["attempt"] or 0)
    theirs = await session.get(JobModel, rec["job_id"])
    mine = await session.get(JobModel, ident["job_id"])
    if theirs is None or mine is None or theirs.created_at is None or mine.created_at is None:
        return theirs is not None      # an attempt that cannot be ordered never replaces a known one
    return theirs.created_at > mine.created_at


async def _publish_preview(clip: ClipModel, seen: tuple, attempt: Path, out: Path,
                           state: dict | None, ident: dict) -> str | None:
    """Move this attempt's render to its own immutable `out` and SELECT it — only while the inputs it
    rendered from are still the row's, no export published after it loaded (D2r-2 K7), the job row is
    still this attempt's, and no later attempt's file is selected. Otherwise nothing is selected.

    Returns None when it published, else the refusal's cause. Input/export/newer-preview refusals are
    written on the job row as `metadata.discarded` (D2r-3), in the same transaction as the decision;
    `attempt_not_current` is not, since the row is no longer this attempt's.

    All of it runs under the write lock a PATCH (`_load_clip(lock=True)`) and `_publish_export` take.
    The rename goes to a NEW name, so the only thing the commit decides is which file is selected: a
    crash between the rename and the commit leaves an unselected file, never a selected one that
    changed under its row (codex-verdict-next-24 §3).
    """
    async with async_session() as session:
        await begin_write(session)
        row = await session.get(ClipModel, clip.id, populate_existing=True)
        project = await session.get(ProjectModel, clip.project_id, populate_existing=True)
        if not await _still_this_attempt(session, ident):
            await session.rollback()
            return LOST_ATTEMPT
        cause = None
        if row is None or project is None or _preview_inputs(row, project) != seen:
            cause = DISCARD_INPUTS_CHANGED
        elif row.status == ClipStatus.exported.value and (
                row.status, row.export_job_id, row.export_path) != (
                clip.status, clip.export_job_id, clip.export_path):
            cause = DISCARD_NEWER_EXPORT
        elif await _older_than_selected(session, row, ident):
            cause = DISCARD_NEWER_PREVIEW
        job = await session.get(JobModel, ident["job_id"])
        if cause is not None:
            if job is not None:
                job.metadata_json = json.dumps(
                    {**json.loads(job.metadata_json or "{}"), "discarded": cause})
            await session.commit()
            return cause
        os.replace(attempt, out)
        record = {"job_id": ident["job_id"], "attempt": ident["attempt"], "worker": ident["worker"],
                  "caption_plan_state": state}
        # The job row keeps its own copy for history; the CARD reads `preview_record` only.
        if job is not None:
            job.metadata_json = json.dumps(
                {**json.loads(job.metadata_json or "{}"), "caption_plan_state": state})
        values = {"preview_path": str(out), "preview_record": record}
        warnings = _caption_warnings(row.warnings, state, "preview")
        if warnings is not None:
            values["warnings"] = warnings
        await session.execute(
            update(ClipModel).where(ClipModel.id == clip.id).values(**values))
        await session.commit()
    return None


async def selected_path(clip_id: str) -> str | None:
    async with async_session() as session:
        return await session.scalar(select(ClipModel.preview_path).where(ClipModel.id == clip_id))
