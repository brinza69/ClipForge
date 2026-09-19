"""Independent counterexamples: decide from the layer actually being rendered."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def bound_no_gap(tmp_path):
    from services.clipper.reaction_edit import BINDING_SCHEMA, compute_source_version
    from services.clipper.reaction_layout import plan_reaction_layout
    # Planning only: source guard stats bytes; no media decoder runs in these cases.
    src = tmp_path / "source.mp4"
    src.write_bytes(b"source guard for planning test")
    plan = plan_reaction_layout({"x": 0, "y": 0, "w": 100, "h": 180},
                                {"x": 0, "y": 0, "w": 84, "h": 60}, 320, 180)
    plan["reaction_binding"] = {
        "schema": BINDING_SCHEMA, "source_version": compute_source_version(str(src), 320, 180),
        "source_start": 0., "source_end": 4., "src_w": 320, "src_h": 180, "by": "human"}
    return plan, str(src)


def _objects(plan, src, cp, **settings):
    clip = SimpleNamespace(id="gap-regression", project_id="probe", start_time=0.,
        end_time=4., duration=4., layout_plan=plan, caption_plan=cp,
        transcript_text="", headline_text="", content_type=None, content_confidence=None,
        content_type_origin=None, status="candidate", overall_score=None, sub_scores=None,
        score_reason=None, selection_run_id=None, ranker_version=None)
    project = SimpleNamespace(id="probe", width=320, height=180, video_path=src, fps=24.,
        clipper_settings={"trim_silence": False, "dynamic_edit": False, **settings},
        source_url=None, content_type=None, content_confidence=None,
        content_type_override=None, analysis_version=None)
    return clip, project


async def test_suppressed_layer_with_no_gap_does_not_refuse_export(bound_no_gap, tmp_path):
    from workers.clipper_render_plan import _decide_render
    plan, src = bound_no_gap
    cp = {"preset_id": "viral_gradient", "entry_pop": True,
          "chunks": [{"text": "long text is irrelevant to a suppressed layer", "start": 0., "end": 2.}]}
    clip, project = _objects(plan, src, cp, source_has_burned_captions=True)
    decision = await _decide_render(clip, project, tmp_path)
    assert decision["caption_policy"]["action"] == "suppress"
    assert decision["ass_path"] is None
    assert decision["caption_y"] is None


async def test_fully_removed_layer_with_no_gap_does_not_refuse_export(bound_no_gap, tmp_path):
    from workers import clipper_render_plan as crp
    plan, src = bound_no_gap
    cp = {"entry_pop": True, "chunks": [{"text": "THIS IS REMOVED", "start": 0., "end": 1.}]}
    clip, project = _objects(plan, src, cp, trim_silence=True)
    with patch.object(crp, "_dead_spans", new_callable=AsyncMock, return_value=[(0., 1.)]):
        decision = await crp._decide_render(clip, project, tmp_path)
    assert decision["ass_path"] is None
    assert decision["caption_y"] is None


async def test_events_outside_clip_do_not_refuse_export(bound_no_gap, tmp_path):
    from workers.clipper_render_plan import _decide_render
    plan, src = bound_no_gap
    cp = {"entry_pop": True, "chunks": [{"text": "AFTER THE CLIP", "start": 5., "end": 6.}]}
    clip, project = _objects(plan, src, cp)
    decision = await _decide_render(clip, project, tmp_path)
    # The existing ASS writer may retain an out-of-window event; it draws no
    # pixels within this four-second export, so placement must not veto it.
    assert decision["caption_y"] is None


async def test_trim_does_not_move_outside_events_into_visibility(bound_no_gap, tmp_path):
    from workers import clipper_render_plan as crp
    plan, src = bound_no_gap
    cp = {"entry_pop": True, "chunks": [{"text": "AFTER THE CLIP", "start": 5., "end": 6.}]}
    clip, project = _objects(plan, src, cp, trim_silence=True)
    # Source clip is 4s, delivered clip 1s. Event remaps to 2s, still outside.
    with patch.object(crp, "_dead_spans", new_callable=AsyncMock, return_value=[(0., 3.)]):
        decision = await crp._decide_render(clip, project, tmp_path)
    assert decision["caption_y"] is None


def _roomy_plan():
    from services.clipper.reaction_layout import plan_reaction_layout
    return plan_reaction_layout({"x": 348, "y": 128, "w": 1314, "h": 798},
                                {"x": 8, "y": 476, "w": 326, "h": 232}, 1920, 1080)


def test_named_preset_is_checked_not_only_inline_overrides():
    from services.clipper.reaction_captions import resolve_reaction_caption_y
    # viral_gradient resolves to 76px with no inline style at all.
    cp = {"preset_id": "viral_gradient", "chunks": [{"text": "HELLO", "start": 0., "end": 1.}]}
    with pytest.raises(ValueError, match="manual"):
        resolve_reaction_caption_y(_roomy_plan(), cp)


def test_only_surviving_text_is_checked_after_remap():
    from services.clipper.reaction_captions import resolve_reaction_caption_y
    cp = {"chunks": [{"text": "REMOVED " * 10, "start": 0., "end": 1.},
                     {"text": "KEPT", "start": 1., "end": 2.}]}
    y = resolve_reaction_caption_y(_roomy_plan(), cp, drop_spans=[(0., 1.)])
    assert .5 < y < .55


@pytest.mark.parametrize("style", [{"shadow_offset": -80}, {"font_size": float("nan")},
                                    {"outline_width": float("inf")}])
def test_extent_guard_refuses_unbounded_or_negative_shadow(style):
    from services.clipper.reaction_captions import resolve_reaction_caption_y
    cp = {"style": style, "chunks": [{"text": "HELLO", "start": 0., "end": 1.}]}
    with pytest.raises(ValueError, match="manual"):
        resolve_reaction_caption_y(_roomy_plan(), cp)


@pytest.mark.parametrize("text", [r"A\NB\NC", r"{\fscy500}HELLO"])
def test_ass_control_text_cannot_bypass_extent_guard(text):
    from services.clipper.reaction_captions import resolve_reaction_caption_y
    # The live writer preserves raw ASS control sequences in these strings.
    cp = {"chunks": [{"text": text, "start": 0., "end": 1.}]}
    with pytest.raises(ValueError, match="manual"):
        resolve_reaction_caption_y(_roomy_plan(), cp)


@pytest.mark.parametrize("outside_clip", [False, True])
async def test_api_saved_position_matches_ordinary_worker(client, tmp_path, outside_clip):
    import uuid
    from database import async_session
    from models import ClipModel, ProjectModel
    from services.clipper.reaction_edit import compute_source_version
    from workers.clipper_render_plan import _decide_render

    src = tmp_path / "planning-source.mp4"
    src.write_bytes(b"source version only; no decoding in this test")
    pid, cid = uuid.uuid4().hex, uuid.uuid4().hex
    cp = {"preset_id": "bold_impact", "y_pct": .75, "entry_pop": outside_clip,
          "chunks": [{"text": "HELLO", "start": 5. if outside_clip else 0.,
                      "end": 6. if outside_clip else 1.}]}
    async with async_session() as s:
        s.add(ProjectModel(id=pid, title="gap consistency", source_kind="file", status="ready",
                           video_path=str(src), width=320, height=180, fps=24,
                           clipper_settings={"dynamic_edit": False, "trim_silence": False}))
        s.add(ClipModel(id=cid, project_id=pid, title="gap", status="candidate",
                       start_time=0., end_time=4., duration=4., caption_plan=cp))
        await s.commit()
    response = await client.put(f"/api/clipper/clips/{cid}/reaction-layout", json={
        "content_rect": {"x": 0, "y": 0, "w": 100 if outside_clip else 320, "h": 180},
        "face_rect": {"x": 0, "y": 0, "w": 84, "h": 60},
        "source_version": compute_source_version(str(src), 320, 180),
        "source_start": 0., "source_end": 4., "src_w": 320, "src_h": 180})
    assert response.status_code == 200, response.text
    saved_y = response.json()["clip"]["caption_plan"]["y_pct"]
    assert saved_y == .75 if outside_clip else .5 < saved_y < .55
    async with async_session() as s:
        clip, project = await s.get(ClipModel, cid), await s.get(ProjectModel, pid)
    decision = await _decide_render(clip, project, tmp_path)
    assert decision["caption_y"] is None if outside_clip else decision["caption_y"] == saved_y
    assert decision["ass_path"] is not None
