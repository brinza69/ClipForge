"""D2r-3 (closure-4 §2): the project's error is its latest ANALYSIS attempt's, and
a clip's preview outcome sits on the clip.

- `analysis_attempt` is the latest clipper_ingest/transcribe/analyze/score job by
  `created_at`; `error` is its error when it failed; `retry_allowed` is the
  predicate `/retry` refuses by (409 `retry_not_applicable`, zero jobs, status
  unchanged).
- `last_preview` is the clip's latest `clipper_preview` job, with `discarded`
  read from the marker the handler writes — one query per board.

Nothing new is imported at module level, so the file runs against the tree
before the change and fails there for the reasons in D2r3-before.txt.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import event, func, select, update

from database import async_session, engine
from job_attempt import CLAIMED_ATTEMPT, ClaimedAttempt
from job_queue import job_queue
from models import ClipModel, JobModel, ProjectModel, TranscriptModel
from workers import clipper_render_jobs as jobs

T0 = datetime(2026, 1, 1, 12, 0, 0)     # before any job `/retry` creates for real
BOOM = "clipper_analyze: cv2 has no attribute CascadeClassifier"


async def _project(ident, status, clips=1):
    async with async_session() as session:
        session.add(ProjectModel(id=ident, title="d2r3", source_kind="file", video_path="",
                                 status=status, processing_mode="clipping",
                                 width=320, height=180, fps=24,
                                 clipper_settings={"dynamic_edit": False,
                                                   "trim_silence": False, "fps": 24}))
        for i in range(clips):
            session.add(ClipModel(id=f"{ident}c{i}", project_id=ident, title="c",
                                  start_time=0.0, end_time=3.0, duration=3.0,
                                  transcript_text="said words", status="candidate"))
        await session.commit()


async def _job(ident, jid, type_, status, minute, *, clip=None, error=None, meta=None,
               finished=None):
    """A job row created at T0+minute; `finished` sets a later `updated_at`, to
    prove the order is the attempts', not which one finished last."""
    created = T0 + timedelta(minutes=minute)
    async with async_session() as session:
        session.add(JobModel(id=jid, project_id=ident, clip_id=clip, type=type_,
                             status=status, error=error,
                             metadata_json=json.dumps(meta) if meta else None,
                             created_at=created,
                             updated_at=T0 + timedelta(minutes=finished or minute)))
        await session.commit()


async def _jobs(ident) -> int:
    async with async_session() as session:
        return await session.scalar(
            select(func.count(JobModel.id)).where(JobModel.project_id == ident))


async def _status(ident) -> str:
    async with async_session() as session:
        return (await session.get(ProjectModel, ident, populate_existing=True)).status


async def _get(client, ident) -> dict:
    r = await client.get(f"/api/clipper/projects/{ident}")
    assert r.status_code == 200, r.text
    return r.json()


async def _refused(client, ident):
    """`/retry` is refused whole: 409, no job, the project status as it was."""
    count, status = await _jobs(ident), await _status(ident)
    r = await client.post(f"/api/clipper/projects/{ident}/retry")
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["error"] == "retry_not_applicable"
    assert await _jobs(ident) == count
    assert await _status(ident) == status


# ── the project's error is its analysis's ────────────────────────────────────

@pytest.mark.parametrize("clip_job", ["preview_failed", "preview_discarded", "export_failed"])
async def test_a_failed_clip_job_on_a_ready_project_is_no_analysis_error(client, clip_job):
    ident = f"d3a{clip_job[:4]}{clip_job[-6:]}"
    await _project(ident, "ready")
    await _job(ident, f"{ident}s", "clipper_score", "done", 0)
    type_ = "clipper_export" if clip_job.startswith("export") else "clipper_preview"
    meta = {"discarded": "inputs_changed"} if clip_job == "preview_discarded" else None
    await _job(ident, f"{ident}x", type_, "failed", 5, clip=f"{ident}c0",
               error="preview x: clip changed while it rendered; its render was discarded",
               meta=meta)

    body = await _get(client, ident)
    assert body["error"] is None
    assert body["retry_allowed"] is False
    assert body["analysis_attempt"]["job_id"] == f"{ident}s"
    assert body["analysis_attempt"]["status"] == "done"
    await _refused(client, ident)
    async with async_session() as session:
        scoring = await session.scalar(select(func.count(JobModel.id)).where(
            JobModel.project_id == ident, JobModel.type == "clipper_score"))
    assert scoring == 1


async def test_failed_then_retried_then_done_never_brings_the_old_error_back(client):
    ident = "d3retryok"
    await _project(ident, "failed")
    await _job(ident, f"{ident}a", "clipper_analyze", "failed", 0, error=BOOM)
    body = await _get(client, ident)
    assert body["error"] == BOOM and body["retry_allowed"] is True

    r = await client.post(f"/api/clipper/projects/{ident}/retry")
    assert r.status_code == 200, r.text
    resumed = r.json()["job_id"]
    body = await _get(client, ident)
    assert body["analysis_attempt"]["job_id"] == resumed
    assert body["analysis_attempt"]["status"] == "queued"
    assert body["error"] is None and body["retry_allowed"] is False

    async with async_session() as session:             # the resume succeeds
        await session.execute(update(JobModel).where(JobModel.id == resumed)
                              .values(status="done"))
        await session.execute(update(ProjectModel).where(ProjectModel.id == ident)
                              .values(status="ready"))
        await session.commit()
    for _reload in range(2):
        body = await _get(client, ident)
        assert body["error"] is None
        assert body["retry_allowed"] is False
        assert body["analysis_attempt"]["status"] == "done"


async def test_failed_then_cancelled_shows_no_old_failure_and_resume_stays_explicit(client):
    ident = "d3cancel"
    await _project(ident, "cancelled")
    await _job(ident, f"{ident}a", "clipper_analyze", "failed", 0, error=BOOM)
    await _job(ident, f"{ident}r", "clipper_analyze", "cancelled", 3)
    body = await _get(client, ident)
    assert body["error"] is None
    assert body["retry_allowed"] is True
    assert body["analysis_attempt"]["status"] == "cancelled"
    assert await _status(ident) == "cancelled"        # nothing resumed on its own

    r = await client.post(f"/api/clipper/projects/{ident}/retry")
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("later", ["preview_failed", "export_running", "preview_done"])
async def test_a_real_analysis_failure_survives_a_later_clip_job(client, later):
    ident = f"d3real{later[:3]}{later[-4:]}"
    await _project(ident, "failed")
    await _job(ident, f"{ident}a", "clipper_analyze", "failed", 0, error=BOOM)
    type_, status = {"preview_failed": ("clipper_preview", "failed"),
                     "export_running": ("clipper_export", "running"),
                     "preview_done": ("clipper_preview", "done")}[later]
    await _job(ident, f"{ident}x", type_, status, 5, clip=f"{ident}c0", error="clip error")
    body = await _get(client, ident)
    assert body["error"] == BOOM
    assert body["analysis_attempt"]["job_id"] == f"{ident}a"
    assert body["retry_allowed"] is True


@pytest.mark.parametrize("case", ["ready", "running", "queued", "older_still_running"])
async def test_a_direct_retry_on_a_ready_or_active_analysis_is_refused(client, case):
    ident = f"d3ref{case[:6]}"
    if case == "ready":
        await _project(ident, "ready")
        await _job(ident, f"{ident}s", "clipper_score", "done", 0)
    elif case == "older_still_running":
        # The newest attempt failed but an OLDER pipeline job still runs.
        await _project(ident, "failed")
        await _job(ident, f"{ident}t", "clipper_transcribe", "running", 0)
        await _job(ident, f"{ident}a", "clipper_analyze", "failed", 2, error=BOOM)
    else:
        await _project(ident, "transcribing")
        await _job(ident, f"{ident}a", "clipper_analyze", "failed", 0, error=BOOM)
        await _job(ident, f"{ident}t", "clipper_transcribe", case, 2)
    body = await _get(client, ident)
    assert body["retry_allowed"] is False
    await _refused(client, ident)


# ── when the project's status and its latest attempt disagree ─────────────────

DISAGREE = {
    # project status, the pipeline jobs, expected (error, retry_allowed, attempt)
    "ready_but_latest_failed": ("ready", [("clipper_score", "failed", 0, BOOM)],
                                (BOOM, False, "failed")),
    "failed_but_latest_done": ("failed", [("clipper_score", "done", 0, None)],
                               (None, False, "done")),
    "cancelled_but_latest_done": ("cancelled", [("clipper_score", "done", 0, None)],
                                  (None, False, "done")),
    "pending_and_latest_failed": ("pending", [("clipper_ingest", "failed", 0, BOOM)],
                                  (BOOM, True, "failed")),
    "failed_with_no_pipeline_job": ("failed", [], (None, False, None)),
    # The newer attempt is the one that counts, however late the older finished.
    "older_failure_finished_last": ("ready", [("clipper_analyze", "failed", 0, BOOM, 30),
                                              ("clipper_score", "done", 5, None, 6)],
                                    (None, False, "done")),
}


@pytest.mark.parametrize("case", list(DISAGREE))
async def test_the_status_and_the_latest_attempt_disagree(client, case):
    ident = f"d3dis{case[:7]}{len(case)}"
    status, rows, (error, retry, attempt) = DISAGREE[case]
    await _project(ident, status)
    for i, (type_, st, minute, err, *finished) in enumerate(rows):
        await _job(ident, f"{ident}{i}", type_, st, minute, error=err,
                   finished=finished[0] if finished else None)
    body = await _get(client, ident)
    assert body["error"] == error
    assert body["retry_allowed"] is retry
    assert (body["analysis_attempt"] or {}).get("status") == attempt
    if not retry:
        await _refused(client, ident)


# ── last_preview on the card ─────────────────────────────────────────────────

async def test_the_latest_preview_replaces_an_earlier_failure(client):
    ident = "d3prev"
    await _project(ident, "ready", clips=3)
    c0, c1, c2 = (f"{ident}c{i}" for i in range(3))
    await _job(ident, f"{ident}f", "clipper_preview", "failed", 0, clip=c0, error="ffmpeg 1")
    await _job(ident, f"{ident}d", "clipper_preview", "done", 4, clip=c0)
    # c1: a done preview, then a newer failure — the failure is the latest.
    await _job(ident, f"{ident}g", "clipper_preview", "done", 0, clip=c1)
    await _job(ident, f"{ident}h", "clipper_preview", "failed", 3, clip=c1, error="later",
               finished=4)
    # c0's older failure finished last; it still does not win.
    await _job(ident, f"{ident}o", "clipper_preview", "failed", 1, clip=c0, error="old",
               finished=60)
    await _job(ident, f"{ident}e", "clipper_export", "failed", 9, clip=c2, error="export")

    cards = {c["id"]: c for c in (await _get(client, ident))["clips"]}
    assert cards[c0]["last_preview"]["job_id"] == f"{ident}d"
    assert cards[c0]["last_preview"]["status"] == "done"
    assert cards[c0]["last_preview"]["error"] is None
    assert cards[c0]["last_preview"]["discarded"] is None
    assert cards[c1]["last_preview"]["job_id"] == f"{ident}h"
    assert cards[c1]["last_preview"]["error"] == "later"
    assert cards[c2]["last_preview"] is None          # an export is not a preview

    r = await client.get(f"/api/clipper/clips/{c0}")
    assert r.status_code == 200, r.text
    assert r.json()["last_preview"] == cards[c0]["last_preview"]


async def test_the_board_reads_its_previews_in_one_query(client):
    """The same number of job queries for a board of 1 clip and of 6."""
    counts = []
    for n in (1, 6):
        ident = f"d3q{n}"
        await _project(ident, "ready", clips=n)
        for i in range(n):
            await _job(ident, f"{ident}p{i}", "clipper_preview", "done", i, clip=f"{ident}c{i}")
        seen = []

        def count(_conn, _cursor, statement, *_a):
            if "FROM jobs" in statement:
                seen.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", count)
        try:
            body = await _get(client, ident)
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", count)
        assert all(c["last_preview"]["status"] == "done" for c in body["clips"])
        counts.append(len(seen))
    assert counts[0] == counts[1], counts


# ── `discarded`: from the handler's marker, never from text ─────────────────

class _Queue:
    worker_id = "d2r3-worker"

    async def update_progress(self, *_):
        pass

    def is_cancelled(self, _):
        return False


async def _preview_run(monkeypatch, ident, how):
    """Run the real `handle_preview` on a job row, doing `how` mid-render, then
    fail the job through the real `fail_job` — the path a queue takes."""
    import services.clipper.render as static_render

    await _project(ident, "ready")
    clip = f"{ident}c0"
    async with async_session() as session:
        session.add(TranscriptModel(project_id=ident, language="en", full_text="x",
                                    segments=[{"start": 0.0, "end": 3.0, "text": "untimed"}]))
        await session.commit()
    await _job(ident, f"{ident}j", "clipper_preview", "running", 1, clip=clip)
    async with async_session() as session:
        # Claimed as the queue claims it — a worker AND a live lease — which a preview's
        # publication now checks (BURST R1, codex-verdict-next-24 §3).
        await session.execute(update(JobModel).where(JobModel.id == f"{ident}j")
                              .values(worker_id=_Queue.worker_id, attempt_count=1,
                                      lease_expires_at=datetime.utcnow() + timedelta(minutes=10)))
        await session.commit()
    CLAIMED_ATTEMPT.set(ClaimedAttempt(f"{ident}j", 1, _Queue.worker_id))   # and its identity (R1c)

    async def render(_src, _cand, _plan, _ass, out, **_kw):
        if how == "render_fails":
            # The words of a refusal, with no refusal behind them.
            raise RuntimeError("ffmpeg exited 1; its render was discarded")
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"P")
        async with async_session() as session:
            if how == "inputs_changed":
                await session.execute(update(ProjectModel).where(ProjectModel.id == ident)
                                      .values(width=640))
            elif how == "newer_export":
                await session.execute(update(ClipModel).where(ClipModel.id == clip).values(
                    status="exported", export_job_id="elsewhere",
                    export_path=str(Path(out).with_name("export.mp4"))))
            await session.commit()

    monkeypatch.setattr(static_render, "render_preview", render)
    monkeypatch.setattr(jobs, "_source_path", lambda _: "unused.mp4")
    monkeypatch.setattr(type(job_queue), "_cleanup_workspace",
                        lambda *_a: asyncio.sleep(0))
    with pytest.raises(RuntimeError) as raised:
        await jobs.handle_preview(f"{ident}j", ident, clip, {}, _Queue())
    await job_queue.fail_job(f"{ident}j", str(raised.value), owner_id=_Queue.worker_id)
    return clip


@pytest.mark.parametrize("how, expected", [("inputs_changed", "inputs_changed"),
                                           ("newer_export", "newer_export"),
                                           ("render_fails", None)])
async def test_discarded_names_the_refusal_and_nothing_else(monkeypatch, client, how,
                                                            expected):
    ident = f"d3disc{how[:6]}"
    clip = await _preview_run(monkeypatch, ident, how)
    r = await client.get(f"/api/clipper/clips/{clip}")
    assert r.status_code == 200, r.text
    last = r.json()["last_preview"]
    assert last["job_id"] == f"{ident}j"
    assert last["status"] == "failed"
    assert last["discarded"] == expected
    assert last["error"]
    body = await _get(client, ident)
    assert body["clips"][0]["last_preview"] == last
    # A preview on a ready project, discarded or not, is no analysis error.
    assert body["error"] is None and body["retry_allowed"] is False
    assert await _status(ident) == "ready"
