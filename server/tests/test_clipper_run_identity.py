"""S7f: one selection identity survives through clips and render provenance."""

from __future__ import annotations

import inspect
import json

import pytest
from sqlalchemy import delete, select

from database import async_session
from models import ClipModel, ClipStatus, ProjectModel
from services.clipper import render_input
from services.clipper.serialize import clip_to_dict
from workers import clipper_build
from workers import clipper_render_jobs
from workers.clipper_finalize import _reasoning_of, _write_clips, _write_traces


async def _project(project_id: str) -> None:
    async with async_session() as session:
        await session.execute(delete(ClipModel).where(ClipModel.project_id == project_id))
        await session.execute(delete(ProjectModel).where(ProjectModel.id == project_id))
        session.add(ProjectModel(id=project_id, title="run identity",
                                 source_kind="file", status="ready"))
        await session.commit()


def _candidate(start: float, *, shadow_run_id: str | None = None) -> dict:
    cand = {
        "start": start, "end": start + 5.0, "overall": 80.0,
        "headline": "candidate", "content_type": "gaming",
    }
    if shadow_run_id is not None:
        cand["shadow_rank"] = 1
        cand["shadow_run_id"] = shadow_run_id
    return cand


async def test_every_fresh_row_carries_the_selection_run_and_shadow_agrees():
    project_id, run_id = "runidfresh", "abc123def456"
    await _project(project_id)
    winner = _candidate(0.0)
    shadow = _candidate(10.0, shadow_run_id=run_id)

    await _write_clips(project_id, [winner, shadow], [winner], "gaming", run_id)

    async with async_session() as session:
        rows = (await session.execute(
            select(ClipModel).where(ClipModel.project_id == project_id)
            .order_by(ClipModel.start_time))).scalars().all()
    assert [row.selection_run_id for row in rows] == [run_id, run_id]
    assert rows[1].shadow_run_id == run_id
    assert rows[0].reasoning["selection_run_id"] == run_id
    assert rows[1].reasoning["selection_run_id"] == run_id
    assert clip_to_dict(rows[0])["selection_run_id"] == run_id


async def test_an_export_preserves_the_run_that_created_it_across_a_rescore():
    project_id = "runidkeep"
    await _project(project_id)
    async with async_session() as session:
        session.add(ClipModel(
            id="oldexport001", project_id=project_id, title="old export",
            start_time=0.0, end_time=5.0, duration=5.0,
            selection_run_id="oldrun123456", status=ClipStatus.exported.value))
        await session.commit()

    fresh = _candidate(20.0)
    await _write_clips(project_id, [fresh], [fresh], "gaming", "newrun123456")

    async with async_session() as session:
        rows = (await session.execute(
            select(ClipModel).where(ClipModel.project_id == project_id)
            .order_by(ClipModel.start_time))).scalars().all()
    assert [(row.status, row.selection_run_id) for row in rows] == [
        (ClipStatus.exported.value, "oldrun123456"),
        (ClipStatus.candidate.value, "newrun123456"),
    ]


@pytest.mark.parametrize("bad", [None, "", " padded ", "x" * 65, 7])
async def test_a_bad_selection_identity_is_refused_before_the_board_is_replaced(bad):
    project_id = "runidbad"
    await _project(project_id)
    async with async_session() as session:
        session.add(ClipModel(id="sentinel001", project_id=project_id,
                              title="sentinel", status=ClipStatus.candidate.value))
        await session.commit()

    cand = _candidate(0.0)
    with pytest.raises(ValueError):
        await _write_clips(project_id, [cand], [cand], "gaming", bad)

    async with async_session() as session:
        ids = (await session.execute(
            select(ClipModel.id).where(ClipModel.project_id == project_id))).scalars().all()
    assert ids == ["sentinel001"]


async def test_a_shadow_rank_from_another_run_is_refused_before_any_write():
    project_id = "runidshadow"
    await _project(project_id)
    cand = _candidate(0.0, shadow_run_id="other_run")
    with pytest.raises(ValueError, match="different selection run"):
        await _write_clips(project_id, [cand], [cand], "gaming", "this_run")

    async with async_session() as session:
        count = len((await session.execute(
            select(ClipModel.id).where(ClipModel.project_id == project_id))).all())
    assert count == 0


async def test_a_shadow_rank_without_its_run_is_not_silently_attributed():
    cand = {**_candidate(0.0), "shadow_rank": 1}
    with pytest.raises(ValueError, match="must name its selection run"):
        await _write_clips("unused", [cand], [cand], "gaming", "this_run")
    assert "selection_run_id" not in cand


@pytest.mark.parametrize("run_id", ["selection123", None])
async def test_the_export_handler_writes_the_rows_identity_without_backfill(
        run_id, monkeypatch, tmp_path):
    """Execute the real handler and sidecar writer, replacing only the encode
    and render planning. No production data, media or provider is touched."""
    from services.clipper import render

    project_id = "runidexport"
    await _project(project_id)
    if run_id is not None:
        cand = _candidate(0.0)
        await _write_clips(project_id, [cand], [cand], "gaming", run_id)
    else:
        async with async_session() as session:
            session.add(ClipModel(project_id=project_id, title="predates S7f",
                                  start_time=0.0, end_time=5.0, duration=5.0))
            await session.commit()
    async with async_session() as session:
        clip = (await session.execute(
            select(ClipModel).where(ClipModel.project_id == project_id))).scalar_one()

    source = tmp_path / "source.mp4"
    source.write_bytes(b"source stub")
    output = tmp_path / f"{clip.id}.mp4"

    async def decide(*_args, **_kwargs):
        return {"cfg": {}, "drop": [], "plan": {}, "dyn": None,
                "fps": 30.0, "caption_y": None, "ass_path": None,
                "watermark": "", "edit_profile": None, "creator_view": None,
                "regime_view": None, "rhythm_view": None}

    async def encode(*_args, **_kwargs):
        output.write_bytes(b"encoded stub")
        return {"size": output.stat().st_size}

    class Queue:
        async def update_progress(self, *_args):
            pass

        def is_cancelled(self, _job_id):
            return False

    monkeypatch.setattr(clipper_render_jobs, "_decide_render", decide)
    monkeypatch.setattr(clipper_render_jobs, "_source_path", lambda _p: str(source))
    monkeypatch.setattr(clipper_render_jobs.storage, "paths",
                        lambda _p: {"exports_dir": tmp_path})
    monkeypatch.setattr(clipper_render_jobs.storage, "export_path", lambda *_a: output)
    monkeypatch.setattr(render, "render_clip", encode)

    await clipper_render_jobs.handle_export("test-export", project_id, clip.id,
                                             {}, Queue())

    body = json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))
    assert "selection_run_id" in body
    assert body["selection_run_id"] == run_id
    assert body["input_fingerprint"] == render_input.input_fingerprint(body)
    async with async_session() as session:
        saved = await session.get(ClipModel, clip.id)
        assert saved.status == ClipStatus.exported.value
        assert saved.selection_run_id == run_id


def test_the_explanation_and_sidecar_name_the_same_selection_identity():
    run_id = "abc123def456"
    assert _reasoning_of({"selection_run_id": run_id}) == {
        "selection_run_id": run_id}
    source = inspect.getsource(clipper_render_jobs.handle_export)
    assert '"selection_run_id": clip.selection_run_id' in source


def test_the_worker_hands_the_trace_identity_to_clip_persistence():
    source = inspect.getsource(clipper_build.handle_score)
    assert "_write_clips(project_id, ranked, winners, profile, trace.run_id)" in source


def test_both_selection_artefacts_and_the_clip_use_one_identity(monkeypatch):
    from services.clipper.reasoning_trace import RunTrace
    from workers import clipper_finalize

    trace = RunTrace("project", mode="story_v2")
    written = {}
    monkeypatch.setattr(
        clipper_finalize.storage, "write_artifact",
        lambda _project, name, payload: written.__setitem__(name, payload))

    _write_traces("project", trace, [], "story_v2")

    assert written["reasoning_run"]["run_id"] == trace.run_id
    assert written["selection_trace"]["run_id"] == trace.run_id
    assert _reasoning_of({"selection_run_id": trace.run_id}) == {
        "selection_run_id": trace.run_id}


def test_a_provenance_label_does_not_change_the_render_recipe_fingerprint():
    base = {"source": {"path": "source.mp4"}, "source_start": 1.0,
            "source_end": 6.0, "layout_plan": {"layout": "crop"}}
    assert render_input.input_fingerprint({**base, "selection_run_id": "run-a"}) == \
        render_input.input_fingerprint({**base, "selection_run_id": "run-b"})
    assert "selection_run_id" not in render_input.FINGERPRINT_KEYS


def test_the_schema_migration_requires_the_selection_identity_column():
    import database

    assert "selection_run_id" in database._REQUIRED_MIGRATED_COLUMNS["clips"]
