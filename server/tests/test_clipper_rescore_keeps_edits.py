"""A rescore must not throw away a clip a person has worked on.

PRP: PRPs/clipper-rescore-keeps-human-edits-2026-09-24.md. Everything goes
through the real `_write_clips` and the real `_exported_spans` (the function
clipper_build's pre-dedupe filter calls) on the throwaway test database.
"""
from __future__ import annotations

import pytest
from sqlalchemy import delete, select, update

from database import async_session
from models import ClipFeedbackModel, ClipModel, ClipStatus, ProjectModel
from services.clipper import feedback
from workers.clipper_build import _exported_spans, _write_clips

RUN = "rescorerun01"


async def _project(project_id: str) -> None:
    async with async_session() as session:
        await session.execute(delete(ClipModel).where(ClipModel.project_id == project_id))
        await session.execute(delete(ProjectModel).where(ProjectModel.id == project_id))
        session.add(ProjectModel(id=project_id, title="rescore keeps edits",
                                 source_kind="file", status="ready"))
        await session.commit()


async def _clip(project_id: str, clip_id: str, start: float = 0.0,
                status: str = ClipStatus.candidate.value, **kw) -> None:
    async with async_session() as session:
        await session.execute(delete(ClipFeedbackModel)
                              .where(ClipFeedbackModel.clip_id == clip_id))
        session.add(ClipModel(**{
            "title": "old clip", **kw,
            "id": clip_id, "project_id": project_id,
            "start_time": start, "end_time": start + 5.0, "duration": 5.0,
            "selection_run_id": "oldrun000001", "status": status}))
        await session.commit()


async def _event(clip_id: str, project_id: str | None, event_type: str,
                 origin: str | None) -> None:
    async with async_session() as session:
        if origin is None:
            # A legacy row: `feedback.record` refuses a NULL origin on purpose.
            session.add(ClipFeedbackModel(clip_id=clip_id, project_id=project_id,
                                          event_type=event_type, origin=None))
            await session.commit()
        else:
            await feedback.record(session, clip_id, project_id, event_type,
                                  origin=origin)


async def _rows(project_id: str) -> dict[str, ClipModel]:
    async with async_session() as session:
        rows = (await session.execute(
            select(ClipModel).where(ClipModel.project_id == project_id))).scalars().all()
    return {r.id: r for r in rows}


def _cand(start: float) -> dict:
    return {"start": start, "end": start + 5.0, "overall": 50.0, "headline": "fresh"}


async def _rescore(project_id: str, *cands: dict) -> None:
    await _write_clips(project_id, list(cands), list(cands), "general", RUN)


async def test_a_hand_edited_candidate_survives_whole():
    pid = "rkeditwhole"
    await _project(pid)
    layout = {"layout": "reaction", "reaction_binding": {"by": "human"}, "warnings": []}
    captions = {"preset_id": "clean", "y_pct": 61.5}
    await _clip(pid, "rkwhole00001", start=100.0, layout_plan=layout,
                caption_plan=captions, headline_text="my headline",
                title="my headline", rank_position=3, is_alternative=False,
                source_has_burned_captions=True)
    async with async_session() as session:  # a trim
        await session.execute(update(ClipModel).where(ClipModel.id == "rkwhole00001")
                              .values(start_time=101.25, end_time=104.5, duration=3.25))
        await session.commit()
    await _event("rkwhole00001", pid, "layout_changed", feedback.ORIGIN_MANUAL)

    await _rescore(pid, _cand(0.0))

    rows = await _rows(pid)
    kept = rows["rkwhole00001"]
    assert (kept.start_time, kept.end_time, kept.duration) == (101.25, 104.5, 3.25)
    assert kept.layout_plan == layout
    assert kept.caption_plan == captions
    assert kept.headline_text == "my headline" and kept.title == "my headline"
    assert kept.status == ClipStatus.candidate.value
    assert kept.selection_run_id == "oldrun000001"
    assert kept.rank_position == 3
    assert kept.source_has_burned_captions is True
    assert len(rows) == 2


@pytest.mark.parametrize("event", ["rejected", "approved"])
async def test_a_verdict_keeps_the_clip(event):
    pid = f"rkverdict{event[:3]}"
    await _project(pid)
    await _clip(pid, f"rkv{event[:3]}000001", status=event)
    await _event(f"rkv{event[:3]}000001", pid, event, feedback.ORIGIN_MANUAL)
    await _rescore(pid, _cand(50.0))
    assert f"rkv{event[:3]}000001" in await _rows(pid)


@pytest.mark.parametrize("event,origin", [
    ("generated", feedback.ORIGIN_MANUAL),
    ("previewed", feedback.ORIGIN_MANUAL),
    ("reviewed", feedback.ORIGIN_MANUAL),
    ("layout_changed", feedback.ORIGIN_AUTO),
    ("approved", feedback.ORIGIN_SYSTEM),
    ("headline_changed", feedback.ORIGIN_AUTO),
    ("previewed", None),
])
async def test_events_that_are_not_a_persons_work_do_not_keep(event, origin):
    pid = "rknotwork"
    await _project(pid)
    await _clip(pid, "rknotwork001", start=0.0)
    await _event("rknotwork001", pid, event, origin)
    await _rescore(pid, _cand(0.0))
    rows = await _rows(pid)
    assert "rknotwork001" not in rows
    assert [r.selection_run_id for r in rows.values()] == [RUN]


async def test_a_legacy_null_origin_edit_keeps_the_clip():
    pid = "rknullorig"
    await _project(pid)
    await _clip(pid, "rknullorig01")
    await _event("rknullorig01", pid, "headline_changed", None)
    await _rescore(pid, _cand(50.0))
    assert "rknullorig01" in await _rows(pid)


async def test_an_untouched_candidate_is_replaced():
    pid = "rkuntouched"
    await _project(pid)
    await _clip(pid, "rkuntouch001")
    await _rescore(pid, _cand(0.0))
    rows = await _rows(pid)
    assert list(rows) != ["rkuntouch001"] and len(rows) == 1
    assert next(iter(rows.values())).selection_run_id == RUN


async def test_an_exported_clip_is_kept_as_today():
    pid = "rkexported"
    await _project(pid)
    await _clip(pid, "rkexported01", status=ClipStatus.exported.value)
    await _rescore(pid, _cand(50.0))
    rows = await _rows(pid)
    assert rows["rkexported01"].status == ClipStatus.exported.value
    assert len(rows) == 2


async def test_a_fresh_candidate_overlapping_a_kept_clip_is_not_inserted():
    pid = "rkoverlap"
    await _project(pid)
    await _clip(pid, "rkoverlap001", start=10.0)
    await _event("rkoverlap001", pid, "caption_changed", feedback.ORIGIN_MANUAL)
    await _rescore(pid, _cand(10.0), _cand(40.0))
    rows = await _rows(pid)
    assert sorted((r.start_time, r.id == "rkoverlap001") for r in rows.values()) == [
        (10.0, True), (40.0, False)]


async def test_the_pre_dedupe_spans_use_the_same_predicate():
    pid = "rkspans"
    await _project(pid)
    await _clip(pid, "rkspanstouch", start=10.0)
    await _event("rkspanstouch", pid, "start_changed", feedback.ORIGIN_MANUAL)
    await _clip(pid, "rkspansexprt", start=30.0, status=ClipStatus.exported.value)
    await _clip(pid, "rkspansplain", start=60.0)
    await _event("rkspansplain", pid, "previewed", feedback.ORIGIN_MANUAL)
    spans = sorted((s["start"], s["end"]) for s in await _exported_spans(pid))
    assert spans == [(10.0, 15.0), (30.0, 35.0)]
    # ...and it is the set `_write_clips` keeps, row for row.
    await _rescore(pid)
    assert sorted(r.start_time for r in (await _rows(pid)).values()) == [10.0, 30.0]


async def test_a_feedback_row_without_project_id_still_counts():
    pid = "rknullproj"
    await _project(pid)
    await _clip(pid, "rknullproj01")
    await _event("rknullproj01", None, "end_changed", feedback.ORIGIN_MANUAL)
    assert len(await _exported_spans(pid)) == 1
    await _rescore(pid, _cand(50.0))
    assert "rknullproj01" in await _rows(pid)


async def test_a_touched_clip_in_another_project_keeps_nothing_here():
    a, b = "rkprojA", "rkprojB"
    await _project(a)
    await _project(b)
    await _clip(a, "rkprojAclip1")
    await _clip(b, "rkprojBclip1")
    # Filed under project A's id, but it is B's clip: only B's clip is kept.
    await _event("rkprojBclip1", a, "crop_changed", feedback.ORIGIN_MANUAL)
    assert await _exported_spans(a) == []
    await _rescore(a, _cand(50.0))
    await _rescore(b, _cand(50.0))
    assert "rkprojAclip1" not in await _rows(a)
    assert "rkprojBclip1" in await _rows(b)


async def _export_jobs(pid: str) -> list[str]:
    """Clips an export job was written for. Since R4b the job row commits with
    the claim, not through `queue.enqueue`, so a stub queue no longer sees it."""
    from models import JobModel

    async with async_session() as session:
        return list((await session.execute(
            select(JobModel.clip_id).where(JobModel.project_id == pid,
                                           JobModel.type == "clipper_export"))).scalars())


async def test_a_kept_hand_edited_clip_takes_no_unattended_render_slot():
    """Reviewer's finding on this batch: a kept clip carries its OLD run's score,
    and the unattended chain renders the pipeline's own picks only."""
    from workers.clipper_build import _auto_export

    pid = "rkautoexp"
    await _project(pid)
    await _clip(pid, "rkautoedit01", start=0.0, overall_score=99.0)
    await _event("rkautoedit01", pid, "start_changed", feedback.ORIGIN_MANUAL)
    await _rescore(pid, _cand(50.0))
    assert "rkautoedit01" in await _rows(pid)            # kept, and still the top score
    assert await _auto_export(pid, {"auto_export": 1}, None) == 1
    exported = await _export_jobs(pid)
    assert len(exported) == 1 and exported[0] != "rkautoedit01"


@pytest.mark.parametrize("origin", [feedback.ORIGIN_AUTO, feedback.ORIGIN_SYSTEM])
async def test_a_machines_event_does_not_take_the_clip_out_of_auto_export(origin):
    from workers.clipper_build import _auto_export

    pid = "rkautoorig"
    await _project(pid)
    async with async_session() as session:
        from models import JobModel
        await session.execute(delete(JobModel).where(JobModel.project_id == pid))
        await session.commit()
    await _clip(pid, "rkautoorig01", overall_score=99.0)
    await _event("rkautoorig01", pid, "layout_changed", origin)
    assert await _auto_export(pid, {"auto_export": 1}, None) == 1
    assert await _export_jobs(pid) == ["rkautoorig01"]


async def test_a_failed_insert_leaves_the_old_board_whole():
    """B1: the delete and the inserts are one transaction — a failure while
    writing the new board must not leave the old one half removed."""
    from sqlalchemy import event

    from database import engine

    pid = "rkinsfail"
    await _project(pid)
    await _clip(pid, "rkinsfail001", start=0.0)            # untouched: would go
    await _clip(pid, "rkinsfail002", start=20.0)
    await _event("rkinsfail002", pid, "headline_changed", feedback.ORIGIN_MANUAL)

    def refuse(conn, cursor, statement, *a):
        if statement.lstrip().upper().startswith("INSERT INTO CLIPS"):
            raise RuntimeError("insert refused by the test")

    event.listen(engine.sync_engine, "before_cursor_execute", refuse)
    try:
        with pytest.raises(RuntimeError, match="insert refused"):
            await _rescore(pid, _cand(50.0), _cand(80.0))
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", refuse)
    rows = await _rows(pid)
    assert sorted(rows) == ["rkinsfail001", "rkinsfail002"]
    assert {r.selection_run_id for r in rows.values()} == {"oldrun000001"}
