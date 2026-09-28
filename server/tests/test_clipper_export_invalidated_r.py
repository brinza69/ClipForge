"""A project caption-source change voids an export; the rescore must not then
delete the clip (B2 amendments C1 + C4).

Contract: codex-verdict-wave1.md "B2.prompt.md — amendamente obligatorii". When
the project's answer invalidates an EXISTING export, the same transaction writes
one system `export_invalidated` event (cause + the previous export), and
`_keeps()` keeps a clip carrying it: `invalidate_render` moves exported ->
approved, and an export with no human event (auto-exported, or legacy with no
feedback) was then deletable. No event for a clip without an export, for a
no-op or for a refusal. A claim and the setting are serialised in both orders.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import event, select

import test_clipper_project_caption_source as pcs
from database import async_session, engine
from models import ClipFeedbackModel, ClipModel
from test_clipper_mutation_atomicity import _rescore
from test_clipper_project_caption_source import FIELD, _patch, _snapshot, clipper_tmp  # noqa: F401

pytestmark = pytest.mark.asyncio


async def _events(cid):
    async with async_session() as s:
        return [(e, o, p) for e, o, p in (await s.execute(
            select(ClipFeedbackModel.event_type, ClipFeedbackModel.origin,
                   ClipFeedbackModel.payload)
            .where(ClipFeedbackModel.clip_id == cid)
            .order_by(ClipFeedbackModel.created_at))).all()]


async def _invalidations(pid):
    async with async_session() as s:
        return (await s.execute(select(ClipFeedbackModel.clip_id).where(
            ClipFeedbackModel.project_id == pid,
            ClipFeedbackModel.event_type == "export_invalidated"))).scalars().all()


async def _exported_project(tmp_path, prior: str, **setup):
    """One inheriting exported clip with a real MP4 and a hand-written headline
    but no human event, plus a candidate without an export."""
    mp4 = tmp_path / "exported.mp4"
    mp4.write_bytes(b"the previous render, byte for byte")
    pid, ids = await pcs._setup(tmp_path, clips={
        "exp": {"status": "exported", "export_path": str(mp4),
                "headline_text": "a person wrote this"},
        "cand": {}}, **setup)
    if prior != "none":
        async with async_session() as s:
            from services.clipper import feedback
            await feedback.add(s, ids["exp"], pid, "exported", {"path": str(mp4)},
                               origin=prior)
            await s.commit()
    return pid, ids, mp4


@pytest.mark.parametrize("prior", ["none", "auto", "system"])
async def test_an_invalidated_export_survives_the_next_rescore(client, tmp_path, prior):
    pid, ids, mp4 = await _exported_project(tmp_path, prior)
    history = await _events(ids["exp"])

    r = await _patch(client, pid, {FIELD: True})
    assert r.status_code == 200, r.text
    assert r.json()["caption_source"]["export_cleared_clip_ids"] == [ids["exp"]]
    after_patch = await _snapshot(pid)
    exp = after_patch["clips"][ids["exp"]]
    assert (exp["status"], exp["export_path"]) == ("approved", None)

    events = await _events(ids["exp"])
    assert events[:len(history)] == history
    (etype, origin, payload), = events[len(history):]
    assert (etype, origin) == ("export_invalidated", "system")
    assert payload["cause"] == "project_caption_source"
    assert payload["previous_export_path"] == str(mp4)
    assert payload["previous_status"] == "exported"
    assert (payload["old"], payload["new"]) == (None, True)
    assert await _events(ids["cand"]) == [], "no event for a clip with no export"

    await _rescore(pid)
    after = await _snapshot(pid)
    assert after["clips"].get(ids["exp"]) == exp, "same id, same row, same edits"
    assert await _events(ids["exp"]) == events, "same history"
    assert ids["cand"] not in after["clips"], "the plain candidate is still replaceable"
    assert mp4.read_bytes() == b"the previous render, byte for byte"


async def test_caption_and_scoring_in_one_request_keep_the_invalidated_export(
        client, clipper_tmp):
    pid, ids, _mp4 = await _exported_project(clipper_tmp, "auto", analysed=True)
    r = await _patch(client, pid, {FIELD: True, "clip_count": 7})
    assert r.status_code == 200, r.text
    assert r.json()["rescore_job_id"]
    assert await _invalidations(pid) == [ids["exp"]]
    await _rescore(pid)                   # what the queued clipper_score job does
    assert ids["exp"] in (await _snapshot(pid))["clips"]


# --- no-op and refusals: zero events, zero writes ------------------------------


async def test_a_no_op_writes_no_invalidation(client, tmp_path):
    pid, ids, _ = await _exported_project(tmp_path, "none")
    assert (await _patch(client, pid, {FIELD: True})).status_code == 200
    before = await _snapshot(pid)
    again = await _patch(client, pid, {FIELD: True})
    assert again.status_code == 200 and again.json()["changed"] == []
    assert again.json()["caption_source"]["changed"] is False
    assert await _snapshot(pid) == before
    assert await _invalidations(pid) == [ids["exp"]], "only the first, real change"


async def test_an_answer_that_keeps_the_action_writes_no_invalidation(client, tmp_path):
    pid, _ids, _ = await _exported_project(tmp_path, "none")
    before = await _snapshot(pid)
    r = await _patch(client, pid, {FIELD: False})               # burn -> burn
    assert r.status_code == 200
    assert (await _snapshot(pid))["clips"] == before["clips"]
    assert await _invalidations(pid) == []


async def test_a_422_value_writes_no_invalidation(client, tmp_path):
    pid, _ids, _ = await _exported_project(tmp_path, "none")
    before = await _snapshot(pid)
    assert (await _patch(client, pid, {FIELD: 1})).status_code == 422
    assert await _snapshot(pid) == before and await _invalidations(pid) == []


async def test_one_exporting_clip_among_several_blocks_them_all(client, tmp_path):
    """The exported clip comes first in id order, so an implementation that
    wrote per clip and checked per clip would have written its event already."""
    pid, ids = await pcs._setup(tmp_path, clips={
        "a_exported": {"status": "exported", "export_path": "/tmp/a.mp4"},
        "b_candidate": {},
        "c_busy": {"status": "exporting"}})
    before = await _snapshot(pid)
    r = await _patch(client, pid, {FIELD: True})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["clip_ids"] == [ids["c_busy"]]
    assert await _snapshot(pid) == before
    assert await _invalidations(pid) == []


async def test_a_placement_refusal_among_several_writes_no_invalidation(client, tmp_path):
    pid, ids = await pcs._suppressed_reaction_project(client, tmp_path)
    async with async_session() as s:
        band = await s.get(ClipModel, ids["band"])
        band.status, band.export_path = "exported", "/tmp/band.mp4"
        await s.commit()
    before = await _snapshot(pid)
    r = await _patch(client, pid, {FIELD: False})
    assert r.status_code == 422, r.text
    assert await _snapshot(pid) == before
    assert await _invalidations(pid) == []


# --- C4: claim versus the setting, the other order ---------------------------------


async def test_a_claim_after_the_setting_took_the_lock_waits_for_it(client, tmp_path):
    """The setting holds SQLite's write lock (paused on its first write after
    BEGIN IMMEDIATE); the claim, on another connection, blocks until the
    setting commits and then claims the invalidated row. Nothing is invalidated
    after the claim: the invalidation happened, whole, before it.

    The claim-first order is `test_a_claim_before_the_change_is_seen_by_it`
    (single clip) and `test_a_claim_first_blocks_every_clip` below."""
    from test_clipper_mutation_atomicity import _claim    # the whole submit, R4b

    pid, ids = await pcs._setup(tmp_path, clips={
        "a": {"status": "exported", "export_path": "/tmp/a.mp4"}, "b": {}})
    held, release = asyncio.Event(), asyncio.Event()
    state = {}

    def pause_holding_the_lock(conn, cursor, statement, *_):
        if state or not statement.lstrip().upper().startswith(("UPDATE", "INSERT")):
            return
        state["in_tx"] = conn.connection.dbapi_connection._connection.in_transaction
        held.set()
        from sqlalchemy.util import await_only
        await_only(release.wait())

    async def claim():
        return await _claim(ids["a"])

    event.listen(engine.sync_engine, "before_cursor_execute", pause_holding_the_lock)
    try:
        patch = asyncio.ensure_future(_patch(client, pid, {FIELD: True}))
        await held.wait()
        assert state["in_tx"], "the pause must be inside the setting's transaction"
        claiming = asyncio.ensure_future(claim())
        await asyncio.sleep(0.3)
        assert not claiming.done(), "the claim got through while the setting held the lock"
        release.set()
        r, claimed = await patch, await claiming
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", pause_holding_the_lock)

    assert r.status_code == 200, r.text
    assert claimed is True
    after = await _snapshot(pid)
    assert after["clips"][ids["a"]]["status"] == "exporting"
    assert after["clips"][ids["a"]]["export_path"] is None, "the claim read the committed change"
    assert await _invalidations(pid) == [ids["a"]]


async def test_a_claim_first_blocks_every_clip(client, tmp_path):
    from sqlalchemy.util import await_only

    from test_clipper_mutation_atomicity import _claim    # the whole submit, R4b

    pid, ids = await pcs._setup(tmp_path, clips={
        "a": {"status": "exported", "export_path": "/tmp/a.mp4"}, "b": {}})
    window, reached, release = {}, asyncio.Event(), asyncio.Event()

    def gate(conn, cursor, statement, *_):
        if window or statement.lstrip().upper().startswith(("SELECT", "PRAGMA")):
            return
        window["open"] = not conn.connection.dbapi_connection._connection.in_transaction
        reached.set()
        if window["open"]:
            await_only(release.wait())

    before = await _snapshot(pid)
    event.listen(engine.sync_engine, "before_cursor_execute", gate)
    try:
        task = asyncio.ensure_future(_patch(client, pid, {FIELD: True}))
        await reached.wait()
        assert window["open"]
        assert await _claim(ids["b"])
        release.set()
        r = await task
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", gate)

    assert r.status_code == 409, r.text
    after = await _snapshot(pid)
    assert after["clips"][ids["a"]] == before["clips"][ids["a"]], "the other clip rolled back"
    assert after["project"]["clipper_settings"] == before["project"]["clipper_settings"]
    assert await _invalidations(pid) == []
