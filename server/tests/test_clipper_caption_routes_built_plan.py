"""RX1: turning ClipForge's captions ON over a reaction framing checks the plan the render builds.

codex-verdict-next-36 §3. A framing saved while the layer was off was never
placed. Turning the layer on — the clip's caption-source answer, SC3's layer
choice, or the project's answer — must not skip the placement because no plan is
stored (`_place_or_refuse` → `plan_to_place`). Real endpoints, the suite's
temporary DB. asyncio_mode=auto in pytest.ini.
"""
from __future__ import annotations

import pytest

from tests.test_clipper_reaction_built_plan import (  # noqa: F401 — seed/small_source are fixtures
    BAND, SQUARE, _body, _put, _snapshot, fail_read_or_build, seed, small_source)

# Where each route starts — the layer off, so the framing is saved unplaced. SC3's route burns by the
# clip's own layer choice, which it allows only over a source declared without text.
ROUTES = {
    "clip_answer": ({"source_has_burned_captions": True}, None),
    "sc3_layer": ({"source_has_burned_captions": False, "caption_layer": "suppress"}, None),
    "project_answer": ({}, {"source_has_burned_captions": True}),
}


async def _framed(client, seed, route: str, content: dict, transcript: str = "timed") -> str:
    clip_kw, project_cfg = ROUTES[route]
    cid, src = await seed(transcript, project_cfg=project_cfg, **clip_kw)
    r = await _put(client, cid, _body(src, content))
    assert r.status_code == 200 and r.json()["caption_placement"] is None, r.text
    return cid


def _turn_on(client, route: str, cid: str):
    if route == "clip_answer":
        return client.put(f"/api/clipper/clips/{cid}/caption-source",
                          json={"source_has_burned_captions": False})
    if route == "sc3_layer":
        return client.put(f"/api/clipper/clips/{cid}/source-treatment",
                          json={"source_caption_treatment": None, "caption_layer": "burn"})
    return client.patch(f"/api/clipper/projects/{cid[:-2]}/settings",
                        json={"settings": {"source_has_burned_captions": False}})


def _refusal(route: str, cid: str, detail: dict) -> dict:
    """The refused clip's entry: the project route lists every clip that blocked it."""
    if route != "project_answer":
        return detail
    assert [b["clip_id"] for b in detail["blocking_clips"]] == [cid]
    return detail["blocking_clips"][0]


@pytest.mark.parametrize("route", sorted(ROUTES))
async def test_no_caption_slot_is_refused_on_every_route_and_writes_nothing(client, seed, route):
    cid = await _framed(client, seed, route, SQUARE)
    before = await _snapshot(cid)
    r = await _turn_on(client, route, cid)
    assert r.status_code == 422, r.text               # was 200, then every export refused
    detail = r.json()["detail"]
    assert detail["error"] == "caption_placement_failed"
    assert _refusal(route, cid, detail)["max_content_height"] > 0
    assert await _snapshot(cid) == before


@pytest.mark.parametrize("route", sorted(ROUTES))
async def test_a_check_that_could_not_run_refuses_on_every_route(client, seed, monkeypatch, route):
    cid = await _framed(client, seed, route, BAND)
    fail_read_or_build(monkeypatch, "read")
    before = await _snapshot(cid)
    r = await _turn_on(client, route, cid)
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "caption_check_failed"
    assert _refusal(route, cid, detail)["reason"] == "transcript_unreadable"
    assert await _snapshot(cid) == before


@pytest.mark.parametrize("route", sorted(ROUTES))
async def test_missing_timing_turns_on_and_says_nothing_was_checked(client, seed, route):
    cid = await _framed(client, seed, route, SQUARE, transcript="untimed")
    r = await _turn_on(client, route, cid)
    assert r.status_code == 200, r.text
    if route == "project_answer":
        assert r.json()["caption_source"]["caption_placement_unverified"] == [
            {"clip_id": cid, "reason": "no_timed_words"}]
    else:
        assert r.json()["caption_placement"] == {"verified": False, "reason": "no_timed_words"}


@pytest.mark.parametrize("route", sorted(ROUTES))
async def test_room_turns_on_and_the_built_plan_is_not_stored(client, seed, route):
    cid = await _framed(client, seed, route, BAND)
    r = await _turn_on(client, route, cid)
    assert r.status_code == 200, r.text
    if route == "project_answer":
        assert r.json()["caption_source"]["affected_clip_ids"] == [cid]
        assert "caption_placement_unverified" not in r.json()["caption_source"]
    else:
        assert r.json()["caption_placement"] == {"verified": True, "reason": None}
    assert (await client.get(f"/api/clipper/clips/{cid}")).json()["caption_plan"] is None
