"""A changed reject reason is an edit, not a second verdict (Codex Bfix verdict, Q3).

Same state + same effective reason: 200, nothing written. Already rejected + a
different reason: one `metadata_changed` {field: reject_reason, old, new},
origin manual, in the same transaction — no second `rejected`, no label change.
Normalisation (`reject_clip`): no `reason` key leaves the reason alone; an
explicit `null` or `""` clears it. The comparison is with the CURRENT
rejection: a new approved->rejected cycle starts from its own `rejected` event.
"""
from __future__ import annotations

from sqlalchemy import event

from database import async_session, engine
from test_clipper_mutation_atomicity import _setup, api  # noqa: F401


async def _events(cid):
    from services.clipper import feedback
    async with async_session() as s:
        return [(e["event_type"], e["origin"], e["payload"])
                for e in await feedback.events_for_clip(s, cid)]


async def _reason(cid):
    from services.clipper import feedback
    async with async_session() as s:
        return await feedback.current_reject_reason(s, cid)


async def _label(cid):
    from services.clipper import feedback
    return feedback.label_for_events([(e, o) for e, o, _p in await _events(cid)])


async def _reject(api, cid, body=None):
    r = await api.post(f"/api/clipper/clips/{cid}/reject", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "rejected"
    return r


async def _rejected(api, tmp_path, reason="too slow"):
    _pid, cid, _ = await _setup(tmp_path, "patch")
    await _reject(api, cid, {"reason": reason})
    return cid


async def test_the_same_reason_again_is_a_no_op(api, tmp_path):
    cid = await _rejected(api, tmp_path)
    await _reject(api, cid, {"reason": "too slow"})
    assert await _events(cid) == [("rejected", "manual", {"reason": "too slow"})]


async def test_a_different_reason_is_one_metadata_edit(api, tmp_path):
    cid = await _rejected(api, tmp_path)
    label = await _label(cid)
    await _reject(api, cid, {"reason": "bad audio"})
    assert await _events(cid) == [
        ("rejected", "manual", {"reason": "too slow"}),
        ("metadata_changed", "manual",
         {"field": "reject_reason", "old": "too slow", "new": "bad audio"}),
    ]
    assert await _reason(cid) == "bad audio"
    assert await _label(cid) == label == 0.0, "a reason edit is not a verdict"

    await _reject(api, cid, {"reason": "bad audio"})      # the corrected one, again
    assert len(await _events(cid)) == 2


async def test_an_omitted_reason_does_not_clear_it(api, tmp_path):
    cid = await _rejected(api, tmp_path)
    await _reject(api, cid)
    await _reject(api, cid, {})
    await _reject(api, cid, {"note": "no reason key"})
    assert len(await _events(cid)) == 1
    assert await _reason(cid) == "too slow"


async def test_an_explicit_null_or_empty_reason_clears_it(api, tmp_path):
    cid = await _rejected(api, tmp_path)
    await _reject(api, cid, {"reason": None})
    assert (await _events(cid))[-1] == (
        "metadata_changed", "manual", {"field": "reject_reason", "old": "too slow", "new": None})
    assert await _reason(cid) is None
    await _reject(api, cid, {"reason": ""})             # already cleared: "" == null here
    assert len(await _events(cid)) == 2


async def test_a_first_rejection_with_an_empty_reason_stores_none(api, tmp_path):
    _pid, cid, _ = await _setup(tmp_path, "patch")
    await _reject(api, cid, {"reason": ""})
    assert await _events(cid) == [("rejected", "manual", {})]


async def test_a_failed_event_rolls_the_reason_edit_back(api, tmp_path):
    cid = await _rejected(api, tmp_path)

    def refuse(conn, cursor, statement, *a):
        if statement.lstrip().upper().startswith("INSERT INTO CLIP_FEEDBACK"):
            raise RuntimeError("feedback insert refused by the test")

    event.listen(engine.sync_engine, "before_cursor_execute", refuse)
    try:
        resp = await api.post(f"/api/clipper/clips/{cid}/reject", json={"reason": "bad audio"})
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", refuse)
    assert resp.status_code >= 500
    assert len(await _events(cid)) == 1
    assert await _reason(cid) == "too slow"


async def test_a_new_rejection_cycle_does_not_inherit_an_old_correction(api, tmp_path):
    cid = await _rejected(api, tmp_path)
    await _reject(api, cid, {"reason": "bad audio"})       # corrected in cycle 1
    assert (await api.post(f"/api/clipper/clips/{cid}/approve")).status_code == 200
    await _reject(api, cid)                                # cycle 2, no reason
    assert await _reason(cid) is None, "cycle 2 inherited cycle 1's correction"
    await _reject(api, cid, {"reason": "bad audio"})       # new for THIS rejection
    assert (await _events(cid))[-1] == (
        "metadata_changed", "manual", {"field": "reject_reason", "old": None, "new": "bad audio"})
    assert [e for e, _o, _p in await _events(cid)] == [
        "rejected", "metadata_changed", "approved", "rejected", "metadata_changed"]
    assert await _label(cid) == 0.0
