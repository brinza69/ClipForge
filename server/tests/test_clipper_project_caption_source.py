"""The project's caption-source answer, changed through PATCH settings.

PRP: PRPs/clipper-master-plan-2026-09-24.md §5 (B2). Until B2, PATCH settings
enqueued a rescore whenever `clipper_settings` differed at all, so declaring
that a source carries its own subtitles re-ran scoring for a switch scoring
never reads; a non-bool answer was turned into None without a word; and no
clip was invalidated, so a board full of `exported` clips kept presenting MP4s
rendered under the old answer.

Everything goes through the real endpoints on the suite's temporary DB. "Zero
writes" is checked by comparing EVERY column of every row before and after,
not the one field the endpoint was meant to write.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import event, func, inspect, select
from sqlalchemy.util import await_only

from database import async_session, engine
from models import ClipFeedbackModel, ClipModel, JobModel, ProjectModel

pytestmark = pytest.mark.asyncio

FIELD = "source_has_burned_captions"
_UNSET = object()


@pytest.fixture
def clipper_tmp(tmp_path, monkeypatch):
    from config import settings

    monkeypatch.setattr(type(settings), "clipper_dir", property(lambda _s: tmp_path))
    return tmp_path


def _caption_plan() -> dict:
    return {"preset_id": "bold_impact", "position": "bottom", "y_pct": 0.75,
            "scale": 1.0, "entry_pop": False,
            "style": {"font_size": 72, "outline_width": 5, "shadow_offset": 2.5},
            "chunks": [{"text": "YOU PLAY FORTNITE", "start": 0.0, "end": 1.0}]}


async def _setup(tmp_path, project_value=_UNSET, clips: dict | None = None,
                 analysed: bool = False) -> tuple[str, dict[str, str]]:
    """A project (optionally with the answer set) and named clips."""
    from services.clipper import storage

    src = tmp_path / "source.mp4"
    src.write_bytes(b"source guard for planning test")
    pid = "projcap-" + uuid.uuid4().hex[:8]
    cfg = None if project_value is _UNSET else {FIELD: project_value}
    ids = {}
    async with async_session() as s:
        s.add(ProjectModel(id=pid, title="project caption source", source_kind="file",
                           status="ready", video_path=str(src), width=320, height=180,
                           clipper_settings=cfg))
        for name, kw in (clips or {}).items():
            ids[name] = f"{pid}-{name}"
            s.add(ClipModel(id=ids[name], project_id=pid, title=name, start_time=1.0,
                            end_time=5.0, duration=4.0,
                            **{"status": "candidate", "caption_plan": _caption_plan(), **kw}))
        await s.commit()
    if analysed:
        storage.ensure_dirs(pid)
        storage.write_artifact(pid, "candidates", [{"start": 0.0, "end": 20.0}])
    return pid, ids


def _cols(row) -> dict:
    return {a.key: getattr(row, a.key) for a in inspect(type(row)).column_attrs}


async def _snapshot(pid: str) -> dict:
    """Every column of the project, its clips, its events and its jobs."""
    async with async_session() as s:
        project = await s.get(ProjectModel, pid)
        clips = (await s.execute(select(ClipModel).where(ClipModel.project_id == pid)
                                 .order_by(ClipModel.id))).scalars().all()
        events = await s.scalar(select(func.count()).select_from(ClipFeedbackModel)
                                .where(ClipFeedbackModel.project_id == pid))
        jobs = (await s.execute(select(JobModel.type).where(JobModel.project_id == pid))
                ).scalars().all()
        return {"project": _cols(project), "clips": {c.id: _cols(c) for c in clips},
                "events": events, "jobs": sorted(jobs)}


def _patch(client, pid: str, settings: dict | None = None, **top):
    body = dict(top)
    if settings is not None:
        body["settings"] = settings
    return client.patch(f"/api/clipper/projects/{pid}/settings", json=body)


# --- strict values ------------------------------------------------------------


@pytest.mark.parametrize("bad", [1, 0, "true", "false", "yes", "", [], {}])
async def test_a_value_that_is_not_an_answer_is_refused_and_writes_nothing(
        client, tmp_path, bad):
    pid, _ = await _setup(tmp_path, clips={"a": {}})
    before = await _snapshot(pid)
    r = await _patch(client, pid, {FIELD: bad})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error"] == "invalid_value"
    assert r.json()["detail"]["message"]
    assert await _snapshot(pid) == before


@pytest.mark.parametrize("value", [True, False, None])
async def test_each_answer_is_stored_and_rescores_nothing(client, clipper_tmp, value):
    """The no-rescore proof: cached candidates exist, so the old code queued a
    `clipper_score` job for every one of these."""
    start = None if value is not None else True
    pid, _ = await _setup(clipper_tmp, start, analysed=True)
    r = await _patch(client, pid, {FIELD: value})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rescore_job_id"] is None
    assert body["project"]["clipper_settings"][FIELD] is value
    assert body["caption_source"]["changed"] is True
    assert (await _snapshot(pid))["jobs"] == [], "no job of any kind was queued"


async def test_the_same_value_again_is_a_no_op(client, clipper_tmp):
    pid, ids = await _setup(clipper_tmp, _UNSET, analysed=True,
                            clips={"a": {"export_path": "/tmp/a.mp4", "status": "exported"}})
    first = await _patch(client, pid, {FIELD: True})
    assert first.status_code == 200, first.text
    before = await _snapshot(pid)
    again = await _patch(client, pid, {FIELD: True})
    assert again.status_code == 200, again.text
    assert again.json()["changed"] == []
    assert again.json()["rescore_job_id"] is None
    assert again.json()["caption_source"] == {
        "changed": False, "affected_clip_ids": [], "invalidated_clip_ids": [],
        "export_cleared_clip_ids": []}
    assert await _snapshot(pid) == before


async def test_a_caption_change_mixed_with_a_scoring_one_rescores_once(client, clipper_tmp):
    pid, ids = await _setup(clipper_tmp, _UNSET, analysed=True, clips={"a": {}})
    r = await _patch(client, pid, {FIELD: True, "clip_count": 7})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rescore_job_id"]
    assert body["project"]["clipper_settings"]["clip_count"] == 7
    assert body["caption_source"]["affected_clip_ids"] == [ids["a"]]
    # ONE job. The analysis here is legacy (flat files, no generation), and OW1 (next-24 Q2)
    # rebuilds a legacy analysis once before a new scoring, so that job is the analyze.
    assert (await _snapshot(pid))["jobs"] == ["clipper_analyze"]


async def test_a_scoring_change_alone_still_rescores(client, clipper_tmp):
    """The existing behaviour the classification must not lose."""
    pid, _ = await _setup(clipper_tmp, True, analysed=True)
    r = await _patch(client, pid, {"clip_count": 4})
    assert r.status_code == 200, r.text
    assert r.json()["rescore_job_id"]
    assert r.json()["caption_source"]["changed"] is False


# --- which clips move ---------------------------------------------------------


_FIVE = {
    "inh_exported": {"status": "exported", "export_path": "/tmp/x1.mp4",
                     "preview_path": "/tmp/p1.mp4"},
    "inh_candidate": {"preview_path": "/tmp/p2.mp4"},
    "inh_rejected": {"status": "rejected"},
    "own_true": {FIELD: True, "status": "exported", "export_path": "/tmp/x4.mp4"},
    "own_false": {FIELD: False, "status": "exported", "export_path": "/tmp/x5.mp4"},
}


async def test_only_inheriting_clips_whose_decision_flips_are_invalidated(client, tmp_path):
    pid, ids = await _setup(tmp_path, clips=_FIVE)
    before = await _snapshot(pid)

    r = await _patch(client, pid, {FIELD: True})
    assert r.status_code == 200, r.text
    got = r.json()["caption_source"]
    inheriting = sorted(ids[k] for k in ("inh_exported", "inh_candidate", "inh_rejected"))
    assert got["changed"] is True
    assert sorted(got["affected_clip_ids"]) == inheriting
    assert sorted(got["invalidated_clip_ids"]) == inheriting
    assert got["export_cleared_clip_ids"] == [ids["inh_exported"]]

    after = await _snapshot(pid)
    for name in ("own_true", "own_false"):
        assert after["clips"][ids[name]] == before["clips"][ids[name]], name
    exported = after["clips"][ids["inh_exported"]]
    assert exported["export_path"] is None and exported["preview_path"] is None
    assert exported["status"] == "approved"
    assert after["clips"][ids["inh_candidate"]]["preview_path"] is None
    assert after["clips"][ids["inh_rejected"]]["status"] == "rejected"
    # No person's edit of each clip: the one event is the system's note that an
    # EXISTING export was voided (C1, codex-verdict-wave1.md), on that clip only.
    async with async_session() as s:
        events = (await s.execute(
            select(ClipFeedbackModel.clip_id, ClipFeedbackModel.event_type,
                   ClipFeedbackModel.origin)
            .where(ClipFeedbackModel.project_id == pid))).all()
    assert [tuple(e) for e in events] == [(ids["inh_exported"], "export_invalidated", "system")]


async def test_an_answer_that_keeps_the_same_action_invalidates_nothing(client, tmp_path):
    """Nobody said -> "no captions in the source": both burn, so the change is
    real and no render is out of date."""
    pid, ids = await _setup(tmp_path, clips=_FIVE)
    before = await _snapshot(pid)
    r = await _patch(client, pid, {FIELD: False})
    assert r.status_code == 200, r.text
    assert r.json()["caption_source"] == {
        "changed": True, "affected_clip_ids": [], "invalidated_clip_ids": [],
        "export_cleared_clip_ids": []}
    after = await _snapshot(pid)
    assert after["clips"] == before["clips"]
    assert after["project"]["clipper_settings"][FIELD] is False


# --- refusals: whole or nothing -----------------------------------------------


async def test_an_affected_clip_being_exported_refuses_the_change(client, tmp_path):
    pid, ids = await _setup(tmp_path, clips={
        "busy": {"status": "exporting"}, "idle": {"export_path": "/tmp/i.mp4"},
        "own_busy": {FIELD: False, "status": "exporting"}})
    before = await _snapshot(pid)
    r = await _patch(client, pid, {FIELD: True})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "export_in_progress" and detail["message"]
    assert detail["clip_ids"] == [ids["busy"]], "a clip with its own answer is not affected"
    assert await _snapshot(pid) == before


async def test_an_exporting_clip_with_its_own_answer_does_not_block(client, tmp_path):
    pid, ids = await _setup(tmp_path, clips={"own_busy": {FIELD: False, "status": "exporting"}})
    r = await _patch(client, pid, {FIELD: True})
    assert r.status_code == 200, r.text
    assert r.json()["caption_source"]["affected_clip_ids"] == []


def _reaction_body(src, content_rect) -> dict:
    from services.clipper.reaction_edit import compute_source_version

    return {"content_rect": content_rect, "face_rect": {"x": 0, "y": 0, "w": 84, "h": 60},
            "source_version": compute_source_version(str(src), 320, 180),
            "source_start": 1.0, "source_end": 5.0, "src_w": 320, "src_h": 180}


# The near-square box test_clipper_reaction_caption_hint.py proves leaves no
# caption slot when the layer burns; saved here while the project suppresses.
_SQUARE = {"x": 0, "y": 0, "w": 180, "h": 176}
# A short band that does leave one.
_BAND = {"x": 0, "y": 0, "w": 320, "h": 60}


async def _suppressed_reaction_project(client, tmp_path):
    pid, ids = await _setup(tmp_path, True, clips={"square": {}, "band": {}, "plain": {}})
    src = tmp_path / "source.mp4"
    for name, rect in (("square", _SQUARE), ("band", _BAND)):
        r = await client.put(f"/api/clipper/clips/{ids[name]}/reaction-layout",
                             json=_reaction_body(src, rect))
        assert r.status_code == 200, r.text
    return pid, ids


async def test_turning_the_layer_on_over_a_framing_with_no_slot_refuses_everything(
        client, tmp_path):
    pid, ids = await _suppressed_reaction_project(client, tmp_path)
    before = await _snapshot(pid)
    r = await _patch(client, pid, {FIELD: False})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "caption_placement_failed" and detail["message"]
    assert [b["clip_id"] for b in detail["blocking_clips"]] == [ids["square"]]
    blocking = detail["blocking_clips"][0]
    assert set(blocking) == {"clip_id", "title", "max_content_height"}
    assert blocking["title"] == "square"
    assert blocking["max_content_height"] > 0, "the existing suggestion is carried through"
    assert await _snapshot(pid) == before, "not even the clips that had room moved"


async def test_turning_the_layer_on_stores_the_resolved_position(client, tmp_path):
    from routers.clipper_caption_source import _place_or_refuse

    pid, ids = await _suppressed_reaction_project(client, tmp_path)
    async with async_session() as s:
        await s.delete(await s.get(ClipModel, ids["square"]))
        await s.commit()
        band = await s.get(ClipModel, ids["band"])
        expected = _place_or_refuse(band)
    assert expected["y_pct"] != _caption_plan()["y_pct"], "the fixture must move the caption"
    events_before = (await _snapshot(pid))["events"]   # the two reaction PUTs'

    r = await _patch(client, pid, {FIELD: None})
    assert r.status_code == 200, r.text
    after = await _snapshot(pid)
    assert after["clips"][ids["band"]]["caption_plan"] == expected
    assert after["clips"][ids["plain"]]["caption_plan"] == _caption_plan()
    assert sorted(r.json()["caption_source"]["affected_clip_ids"]) == sorted(
        [ids["band"], ids["plain"]])
    assert after["events"] == events_before


# --- where the decision is shown ------------------------------------------------


_PROJECT_TRUE = {"action": "suppress", "scope": "project", "decided_by": "human",
                 "why": "a_person_declared_the_source_already_carries_captions"}
_CLIP_FALSE = {"action": "burn", "scope": "clip", "decided_by": "human",
               "why": "a_person_declared_this_clip_carries_none"}
_DEFAULT = {"action": "burn", "scope": "default", "decided_by": "default",
            "why": "nobody_has_declared_the_source"}


async def test_every_editor_response_says_what_will_happen_and_why(client, tmp_path):
    pid, ids = await _setup(tmp_path, True, clips={"inh": {}, "own": {FIELD: False}})

    board = {c["id"]: c for c in (await client.get(f"/api/clipper/projects/{pid}")).json()["clips"]}
    assert board[ids["inh"]]["effective_caption_policy"] == _PROJECT_TRUE
    assert board[ids["own"]]["effective_caption_policy"] == _CLIP_FALSE

    r = await client.put(f"/api/clipper/clips/{ids['inh']}/caption-source",
                         json={FIELD: False})
    assert r.status_code == 200, r.text
    assert r.json()["clip"]["effective_caption_policy"] == _CLIP_FALSE
    same = await client.put(f"/api/clipper/clips/{ids['own']}/caption-source",
                            json={FIELD: False})
    assert same.json()["clip"]["effective_caption_policy"] == _CLIP_FALSE, "the no-op too"

    r = await _patch(client, pid, {FIELD: None})
    assert r.status_code == 200, r.text
    patched = {c["id"]: c for c in r.json()["clips"]}
    assert patched[ids["own"]]["effective_caption_policy"] == _CLIP_FALSE
    r = await client.put(f"/api/clipper/clips/{ids['inh']}/caption-source", json={FIELD: None})
    assert r.json()["clip"]["effective_caption_policy"] == _DEFAULT


# --- the race: a claim that lands before the change takes the lock ---------------


async def test_a_claim_before_the_change_is_seen_by_it(client, tmp_path):
    """The PATCH is paused on its first write and a claim commits in that
    window. For the old code that write was the project UPDATE, after its read;
    it never looked at `exporting` at all. Now the first write is BEGIN
    IMMEDIATE, so every check after it sees the claim."""
    from test_clipper_mutation_atomicity import _claim    # the whole submit, R4b

    pid, ids = await _setup(tmp_path, clips={"a": {"export_path": "/tmp/a.mp4"}})
    window = {}

    def gate(conn, cursor, statement, *_):
        if window or statement.lstrip().upper().startswith(("SELECT", "PRAGMA")):
            return
        window["open"] = not conn.connection.dbapi_connection._connection.in_transaction
        reached.set()
        if window["open"]:
            await_only(release.wait())

    reached, release = asyncio.Event(), asyncio.Event()
    event.listen(engine.sync_engine, "before_cursor_execute", gate)
    try:
        task = asyncio.ensure_future(_patch(client, pid, {FIELD: True}))
        await asyncio.wait({task, asyncio.ensure_future(reached.wait())},
                           return_when=asyncio.FIRST_COMPLETED)
        assert window.get("open"), "the barrier must actually open a window"
        assert await _claim(ids["a"])
        release.set()
        r = await task
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", gate)

    assert r.status_code == 409, r.text
    async with async_session() as s:
        clip = await s.get(ClipModel, ids["a"])
        project = await s.get(ProjectModel, pid)
    assert clip.status == "exporting" and clip.export_path == "/tmp/a.mp4"
    assert (project.clipper_settings or {}).get(FIELD) is None
