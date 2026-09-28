"""Regenerate against a newer edit, a claim and a changed input (B1-r, R1 + R2).

Contract: codex-verdict-wave1.md "B1-r" R1/R2. The headline's model call runs
UNLOCKED; what it was given is snapshotted first and compared under the lock.
Any difference in the target (`headline_text`) or an input (window, text, words,
the settings that reach the model) is 409 `clip_changed` with neither edit nor
event; a clip being exported is 409 `export_in_progress`, before the model runs
when it already was. Codex's probes (codex-wave1-probes.py) are the first three
tests, unchanged in substance.
"""
from __future__ import annotations

import asyncio

import pytest

from database import async_session
from models import ClipModel, ProjectModel, TranscriptModel
from test_clipper_mutation_atomicity import (  # noqa: F401 — fixtures
    FIRST_WRITE, Gate, _claim, _setup, _state, api, headline_barrier, interleave,
)


def _regen(api, cid):
    return api.post(f"/api/clipper/clips/{cid}/regenerate", json={"what": "headline"})


async def _during_the_model(api, cid, barrier, meanwhile):
    task = asyncio.ensure_future(_regen(api, cid))
    await barrier["reached"].wait()
    await meanwhile()
    barrier["release"].set()
    return await task


async def test_a_claim_during_the_model_call_refuses_the_headline(api, tmp_path, headline_barrier):
    _, cid, _ = await _setup(tmp_path, "patch")

    async def claim():
        assert await _claim(cid)

    resp = await _during_the_model(api, cid, headline_barrier, claim)
    row, events = await _state(cid)
    assert resp.status_code == 409, f"{resp.status_code} status={row.status} {row.headline_text}"
    assert resp.json()["detail"]["error"] == "export_in_progress"
    assert row.status == "exporting" and row.headline_text is None and events == []


@pytest.mark.parametrize("field", ["headline_text", "transcript_text"])
async def test_a_newer_edit_or_input_refuses_the_old_headline(api, tmp_path, headline_barrier,
                                                               field):
    _, cid, _ = await _setup(tmp_path, "patch")

    async def edit():
        r = await api.patch(f"/api/clipper/clips/{cid}", json={field: "new human words"})
        assert r.status_code == 200, r.text

    resp = await _during_the_model(api, cid, headline_barrier, edit)
    row, events = await _state(cid)
    assert resp.status_code == 409, f"{resp.status_code} headline={row.headline_text}"
    assert resp.json()["detail"]["error"] == "clip_changed"
    assert getattr(row, field) == "new human words"
    if field == "transcript_text":
        assert row.headline_text is None, "a headline for the old text was written"
    assert len(events) == 1, f"only the human edit's event: {events}"


async def test_already_exporting_is_refused_before_the_model_is_called(
        api, tmp_path, headline_barrier):
    _, cid, _ = await _setup(tmp_path, "patch")
    assert await _claim(cid)
    headline_barrier["release"].set()
    resp = await _regen(api, cid)
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == "export_in_progress"
    assert not headline_barrier["reached"].is_set(), "the model was called for nothing"
    assert (await _state(cid))[1] == []


async def test_a_language_change_during_the_model_call_refuses_it(api, tmp_path, headline_barrier):
    pid, cid, _ = await _setup(tmp_path, "patch")

    async def relanguage():
        async with async_session() as s:
            project = await s.get(ProjectModel, pid)
            project.clipper_settings = {**(project.clipper_settings or {}), "language": "ro"}
            await s.commit()

    resp = await _during_the_model(api, cid, headline_barrier, relanguage)
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == "clip_changed"
    assert (await _state(cid))[0].headline_text is None


async def test_new_words_in_the_transcript_refuse_the_headline(api, tmp_path, headline_barrier):
    """Same window, same `transcript_text`: only the project's word timings moved
    (a re-transcribe). The start/end comparison alone saved it anyway."""
    pid, cid, _ = await _setup(tmp_path, "patch")

    async def retranscribe():
        async with async_session() as s:
            s.add(TranscriptModel(project_id=pid, language="en", full_text="hello there",
                                  segments=[{"start": 1.5, "end": 3.0, "text": "hello there",
                                             "words": [{"word": "hello", "start": 1.5,
                                                        "end": 2.0},
                                                       {"word": "there", "start": 2.1,
                                                        "end": 3.0}]}]))
            await s.commit()

    resp = await _during_the_model(api, cid, headline_barrier, retranscribe)
    assert resp.status_code == 409, resp.text
    assert (await _state(cid))[0].headline_text is None


async def test_an_unchanged_snapshot_still_saves(api, tmp_path, headline_barrier):
    """The refusal is about change, not about the call being slow."""
    _, cid, _ = await _setup(tmp_path, "patch")

    async def nothing():
        return None

    resp = await _during_the_model(api, cid, headline_barrier, nothing)
    assert resp.status_code == 200, resp.text
    row, events = await _state(cid)
    assert row.headline_text == "new words" and events == [("headline_changed", "manual")]


# ---------------------------------------------------------------------------
# captions: built under the lock, from the settings read under the lock
# ---------------------------------------------------------------------------

async def test_regenerated_captions_use_the_settings_committed_before_the_lock(api, tmp_path):
    """The project (and so `caption_position`) used to be read BEFORE the lock
    and the plan built from that copy: a settings change committed in between
    was invisible to a plan saved after it."""
    pid, cid, _ = await _setup(tmp_path, "regen_captions")

    async def move_captions_to_top():
        async with async_session() as s:
            project = await s.get(ProjectModel, pid)
            project.clipper_settings = {**(project.clipper_settings or {}),
                                        "caption_position": "top"}
            await s.commit()

    gate = Gate(FIRST_WRITE)
    resp, _ = await interleave(
        api.post(f"/api/clipper/clips/{cid}/regenerate", json={"what": "captions"}),
        gate, move_captions_to_top)
    assert gate.window, "the regenerate wrote before the barrier: the race was not forced"
    assert resp.status_code == 200, resp.text
    from services.clipper.captions import build_caption_plan
    at = {p: build_caption_plan({"start": 1.0, "end": 5.0, "text": ""}, {"segments": []},
                                preset_id="bold_impact", max_words=3, position=p,
                                layout={})["y_pct"] for p in ("top", "bottom")}
    assert at["top"] != at["bottom"], "the fixture must tell the two positions apart"
    async with async_session() as s:
        clip = await s.get(ClipModel, cid)
    assert clip.caption_plan["y_pct"] == at["top"], clip.caption_plan["y_pct"]
