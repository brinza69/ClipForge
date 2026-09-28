"""BURST R1–R3 (codex-verdict-next-23 §1): the caption report on the card names the file it describes,
an absent report is never "no render" or "burned nothing", and a suppressed layer is not judged by the
plan it did not use.

R1 — the preview attempt and its report: see `test_clipper_preview_attempt_r.py`.
R2 — no sidecar under an `exported` row, a `{}` state or an unknown outcome is `not_measured`; a
missing MP4 is not verified; a state that is not a dict is unreadable. Real suppress/empty stay
`not_burned`.
R3 — while the effective policy suppresses OUR layer, the plan's limits stay in `plan` but never
raise the level.
"""

from __future__ import annotations

from database import async_session
from models import ClipModel
from services.clipper import storage
from test_clipper_caption_display_api import (BURST, TAIL, WELL_TIMED, _built, _card, _render,
                                              _seed, _sidecar)

# ── R2: an absent report is not "no render", an unknown outcome is not "burned nothing" ──

async def _both(client, ident, cid):
    """The card through the project list AND through GET /clips/{id}: one answer."""
    one = await client.get(f"/api/clipper/clips/{cid}")
    assert one.status_code == 200, one.text
    view = await _card(client, ident)
    assert one.json()["caption_display"] == view
    return view


async def test_an_exported_clip_without_a_sidecar_is_not_measured(client):
    cid = await _seed("r2a", _built(WELL_TIMED))
    storage.export_path("r2a", cid).parent.mkdir(parents=True, exist_ok=True)
    storage.export_path("r2a", cid).write_bytes(b"mp4")
    view = await _both(client, "r2a", cid)
    assert (view["export"]["state"], view["export"]["why"], view["level"]) == (
        "not_measured", "sidecar_missing", "unknown")


async def test_an_exported_row_whose_mp4_is_gone_is_not_verified_from_the_json_left(client):
    plan = _built(WELL_TIMED)
    cid = await _seed("r2b", plan)
    _sidecar("r2b", cid, plan, _render(plan), mp4=False)
    view = await _both(client, "r2b", cid)
    assert (view["export"]["state"], view["export"]["why"], view["level"]) == (
        "unreadable", "mp4_missing", "unverified")


async def test_an_empty_state_and_an_unknown_outcome_are_not_measured(client):
    plan = _built(WELL_TIMED)
    for ident, state in (("r2c", {}), ("r2d", {"origin": "stored", "outcome": "mystery"})):
        cid = await _seed(ident, plan)
        _sidecar(ident, cid, plan, state)
        view = await _both(client, ident, cid)
        assert (view["export"]["state"], view["level"]) == ("not_measured", "unknown"), ident


async def test_a_state_that_is_not_a_dict_is_unreadable(client):
    plan = _built(WELL_TIMED)
    cid = await _seed("r2e", plan)
    _sidecar("r2e", cid, plan, ["not", "a", "state"])
    view = await _both(client, "r2e", cid)
    assert (view["export"]["state"], view["level"]) == ("unreadable", "unverified")


async def test_controls_a_real_suppress_and_a_real_empty_stay_not_burned(client):
    plan = _built(WELL_TIMED)
    for ident, outcome in (("r2f", "suppressed"), ("r2g", "empty")):
        cid = await _seed(ident, plan)
        _sidecar(ident, cid, plan, {"origin": "stored", "outcome": outcome, "reason": None},
                 record={"caption_filter": False})
        view = await _both(client, ident, cid)
        assert (view["export"]["state"], view["level"]) == ("not_burned", "not_burned"), ident


# ── R3: a suppressed layer is not judged by the plan it did not use ──────────

async def _suppressed_clip(ident, plan, **kw):
    cid = await _seed(ident, plan, **kw)
    async with async_session() as session:
        (await session.get(ClipModel, cid)).source_has_burned_captions = True   # our layer: off
        await session.commit()
    return cid


async def test_suppress_with_a_limited_plan_and_its_export_is_not_limited(client):
    plan = _built(TAIL)                             # settle leaves these cards no_time: limited
    cid = await _suppressed_clip("r3a", plan)
    _sidecar("r3a", cid, plan, {"origin": "stored", "outcome": "suppressed", "reason": None},
             record={"caption_filter": False})
    view = await _card(client, "r3a")
    assert view["plan"]["state"] == "limited", "the plan's facts stay for the editor"
    assert view["policy"]["action"] == "suppress"
    assert (view["level"], view["level_from"]) == ("not_burned", "export")


async def test_suppress_with_no_render_is_not_limited(client):
    cid = await _suppressed_clip("r3b", _built(TAIL), exported=False, status="approved")
    view = await _card(client, "r3b")
    assert view["plan"]["state"] == "limited"
    assert (view["level"], view["level_from"]) == ("not_rendered", None)


async def test_turning_our_layer_back_on_invalidates_and_the_plan_counts_again(client):
    plan = _built(TAIL)
    cid = await _suppressed_clip("r3c", plan)
    _sidecar("r3c", cid, plan, {"origin": "stored", "outcome": "suppressed", "reason": None},
             record={"caption_filter": False})
    r = await client.put(f"/api/clipper/clips/{cid}/caption-source",
                          json={"source_has_burned_captions": False})
    assert r.status_code == 200, r.text
    view = await _card(client, "r3c")
    assert view["policy"]["action"] == "burn"
    assert view["export"]["state"] == "stale"
    assert (view["level"], view["level_from"]) == ("limited", "plan")


async def test_burn_with_a_real_missing_card_is_limited(client):
    plan = _built(BURST)
    cid = await _seed("r3d", plan)
    _sidecar("r3d", cid, plan, _render(plan))
    assert (await _card(client, "r3d"))["level"] == "limited"

