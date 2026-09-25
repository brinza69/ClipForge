"""Every PATCH-able field survives a rescore; no edit lands on an export (B1-r R2, R3).

Contract: codex-verdict-wave1.md, answer 3 and R2/R3. A persistent human edit
with no semantic event of its own (title, transcript text, sub-scores, warnings)
writes a non-decisive `metadata_changed` with the field in its payload, which
`_keeps()` honours and the ranker never reads as a label. A request that
changes several fields is one transaction. Approving an approved clip, or
rejecting a rejected one, is a success with no second event. Any change to an
`exporting` clip is 409 with nothing written.
"""
from __future__ import annotations

import pytest
from sqlalchemy import event

from database import async_session, engine
from models import ClipModel
from test_clipper_mutation_atomicity import _rescore, _setup, _state, api  # noqa: F401

# field -> (value, event it must leave). Every key of CLIP_PATCHABLE and
# CLIP_PATCHABLE_JSON; `test_the_inventory_is_complete` fails when one is added.
PATCHABLE = {
    "title": ("human title", "metadata_changed"),
    "transcript_text": ("human transcript", "metadata_changed"),
    "sub_scores": ({"hook": 0.9}, "metadata_changed"),
    "warnings": (["checked by hand"], "metadata_changed"),
    "headline_text": ("human headline", "headline_changed"),
    "start_time": (2.0, "start_changed"),
    "end_time": (4.5, "end_changed"),
    "caption_preset_id": ("clean_minimal", "caption_changed"),
    "caption_plan": ({"preset_id": "clean"}, "caption_changed"),
    "layout_plan": ({"layout": "fullscreen_crop"}, "layout_changed"),
}


def test_the_inventory_is_complete():
    from services.clipper.serialize import CLIP_PATCHABLE, CLIP_PATCHABLE_JSON
    assert set(PATCHABLE) == set(CLIP_PATCHABLE) | set(CLIP_PATCHABLE_JSON)


@pytest.mark.parametrize("field", list(PATCHABLE))
async def test_an_accepted_edit_of_any_field_survives_a_rescore(api, tmp_path, field):
    pid, cid, _ = await _setup(tmp_path, "patch")
    value, wanted = PATCHABLE[field]
    resp = await api.patch(f"/api/clipper/clips/{cid}", json={field: value})
    assert resp.status_code == 200, resp.text
    edited, events = await _state(cid)
    assert (wanted, "manual") in events, f"{field}: no protecting event, {events}"
    await _rescore(pid)
    row, after = await _state(cid)
    assert row is not None, f"{field}: accepted edit deleted by the rescore"
    assert after == events
    from sqlalchemy import inspect
    cols = [a.key for a in inspect(ClipModel).column_attrs]
    assert {c: getattr(row, c) for c in cols} == {c: getattr(edited, c) for c in cols}, \
        "the kept row is whole"


async def test_the_metadata_event_names_its_field(api, tmp_path):
    from services.clipper import feedback
    _pid, cid, _ = await _setup(tmp_path, "patch")
    await api.patch(f"/api/clipper/clips/{cid}", json={"title": "x", "transcript_text": "y"})
    async with async_session() as s:
        got = await feedback.events_for_clip(s, cid)
    assert sorted(e["payload"]["field"] for e in got) == ["title", "transcript_text"]
    assert {e["event_type"] for e in got} == {"metadata_changed"}


async def test_a_multi_field_edit_is_one_transaction(api, tmp_path):
    """The second event fails: neither field and neither event persists."""
    _pid, cid, _ = await _setup(tmp_path, "patch")
    seen = []

    def refuse_second(conn, cursor, statement, *a):
        if statement.lstrip().upper().startswith("INSERT INTO CLIP_FEEDBACK"):
            seen.append(statement)
            if len(seen) == 2:
                raise RuntimeError("second feedback insert refused by the test")

    event.listen(engine.sync_engine, "before_cursor_execute", refuse_second)
    try:
        resp = await api.patch(f"/api/clipper/clips/{cid}",
                               json={"title": "human title", "headline_text": "hh"})
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", refuse_second)
    assert resp.status_code >= 500
    row, events = await _state(cid)
    assert (row.title, row.headline_text, events) == ("t", None, [])


async def test_a_repeat_of_a_metadata_edit_is_a_no_op(api, tmp_path):
    _pid, cid, _ = await _setup(tmp_path, "patch")
    for _ in range(2):
        r = await api.patch(f"/api/clipper/clips/{cid}", json={"title": "same"})
        assert r.status_code == 200
    assert r.json()["changed"] == []
    assert (await _state(cid))[1] == [("metadata_changed", "manual")]


@pytest.mark.parametrize("verb,status", [("approve", "approved"), ("reject", "rejected")])
async def test_repeating_the_same_verdict_is_success_without_an_event(api, tmp_path, verb,
                                                                       status):
    _pid, cid, _ = await _setup(tmp_path, "patch")
    for _ in range(2):
        r = await api.post(f"/api/clipper/clips/{cid}/{verb}")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == status
    assert (await _state(cid))[1] == [(status, "manual")]


# ---------------------------------------------------------------------------
# R2: no field of an exporting clip moves
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field", list(PATCHABLE))
async def test_no_field_of_an_exporting_clip_is_edited(api, tmp_path, field):
    _pid, cid, _ = await _setup(tmp_path, "patch", status="exporting")
    before, _ = await _state(cid)
    resp = await api.patch(f"/api/clipper/clips/{cid}", json={field: PATCHABLE[field][0]})
    assert resp.status_code == 409, f"{field}: {resp.status_code} {resp.text}"
    assert resp.json()["detail"]["error"] == "export_in_progress"
    row, events = await _state(cid)
    assert getattr(row, field) == getattr(before, field) and events == []


async def test_a_no_op_patch_of_an_exporting_clip_is_not_refused(api, tmp_path):
    _pid, cid, _ = await _setup(tmp_path, "patch", status="exporting")
    resp = await api.patch(f"/api/clipper/clips/{cid}", json={"title": "t"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["changed"] == []
