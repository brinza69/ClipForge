"""SC3 (codex-verdict-next-33 §3): the project's mask store, the treatment a render executes, and the layer.

`source_treatment_store.for_render` is what `_decide_render` merges into every decision (export, preview video,
editor still): nothing stored and no layer choice leaves a decision exactly as before; a stored none resolves
inactive; a stored blur re-validates from the store NOW; erase is never offered; a person's burn over source
text that stays — declared or not known — needs an executable blur. `caption_policy.decide(layer=None)` is
byte-identical to before.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from sc3_fixtures import write_store
from services.clipper import caption_policy as cp
from services.clipper import source_treatment as st
from services.clipper import source_treatment_store as store
from test_clipper_clip_caption_source import _bound_plan, _render_objects  # noqa: F401 (fixture)
from test_clipper_source_treatment_render import START, encode_source


@pytest.fixture(scope="module")
def src(tmp_path_factory):
    return encode_source(tmp_path_factory.mktemp("sc3-store") / "plain.mp4")


def _objects(pid: str, src, *, setting=None, layer=None, burned=True):
    clip = SimpleNamespace(id=pid + "-c", start_time=START, end_time=3.0, source_caption_treatment=setting,
                           caption_layer=layer, source_has_burned_captions=burned)
    project = SimpleNamespace(id=pid, video_path=str(src), clipper_settings={})
    return clip, project


def _policy(clip):
    return cp.decide(None, clip_setting=clip.source_has_burned_captions, layer=clip.caption_layer)


def test_nothing_stored_and_no_layer_choice_adds_nothing(src):
    clip, project = _objects("sc3s-legacy", src)
    assert store.for_render(clip, project, _policy(clip), str(src)) == {}


def test_a_stored_none_resolves_inactive(src):
    clip, project = _objects("sc3s-none", src, setting={"treatment": "none", "decided_by": "human"})
    got = store.for_render(clip, project, _policy(clip), str(src))
    assert got["source_treatment"]["treatment"] == "none" and got["source_treatment"]["active"] is False
    assert "source_treatment_inputs" not in got


def test_a_stored_blur_is_validated_from_the_store_now(src):
    pid = "sc3s-blur"
    sha = write_store(pid, pid + "-c", src)
    clip, project = _objects(pid, src, setting={"treatment": "blur", "decided_by": "human", "mask_sha256": sha})
    got = store.for_render(clip, project, _policy(clip), str(src))
    assert got["source_treatment"]["active"] is True and got["source_treatment"]["treatment"] == "blur"
    assert {s["treatment"] for s in got["source_treatment"]["per_line"]} == {"blur"}
    inputs = got["source_treatment_inputs"]
    assert inputs["mask_path"] == str(store.mask_path(pid, sha))
    assert inputs["params_path"] == str(store.params_path(pid))
    clip.end_time = 9.0                                  # an edited window is re-checked on the next render
    clip.start_time = 0.0
    with pytest.raises(st.SourceTreatmentRefused) as e:
        store.for_render(clip, project, _policy(clip), str(src))
    assert e.value.reason == "mask_window_does_not_cover_clip"


@pytest.mark.parametrize("setting", [
    {"treatment": "erase", "decided_by": "human", "mask_sha256": "0" * 64},
    {"treatment": "sharpen", "decided_by": "human"}, "blur", 1])
def test_anything_but_none_or_blur_is_not_offered(src, setting):
    clip, project = _objects("sc3s-erase", src, setting=setting)
    with pytest.raises(st.SourceTreatmentRefused) as e:
        store.for_render(clip, project, _policy(clip), str(src))
    assert e.value.reason == "treatment_not_offered"


@pytest.mark.parametrize("burned, refused", [(True, True), (None, True), (False, False)])
def test_a_chosen_burn_needs_a_blur_unless_the_source_has_no_text(src, burned, refused):
    clip, project = _objects("sc3s-burn", src, layer="burn", burned=burned)
    if refused:
        with pytest.raises(st.SourceTreatmentRefused) as e:
            store.for_render(clip, project, _policy(clip), str(src))
        assert e.value.reason == "burn_over_untreated_source_captions"
    else:
        assert store.for_render(clip, project, _policy(clip), str(src)) == {}


def test_a_chosen_burn_over_a_blur_is_rendered(src):
    pid = "sc3s-burnblur"
    sha = write_store(pid, pid + "-c", src)
    clip, project = _objects(pid, src, layer="burn",
                             setting={"treatment": "blur", "decided_by": "human", "mask_sha256": sha})
    assert store.for_render(clip, project, _policy(clip), str(src))["source_treatment"]["active"] is True


def test_the_store_needs_exactly_one_params_record(src, tmp_path):
    pid = "sc3s-params"
    write_store(pid, pid + "-c", src)
    extra = store.store_dir(pid) / "params" / "other.json"
    extra.write_text("{}", encoding="utf-8")
    with pytest.raises(st.SourceTreatmentRefused) as e:
        store.params_path(pid)
    assert e.value.reason == "params_missing"


@pytest.mark.parametrize("setting, clip_setting", [(None, None), (True, None), (False, None), (None, True),
                                                   (True, False)])
@pytest.mark.parametrize("junk", [None, "BURN", True, 1, "none"])
def test_decide_without_a_valid_layer_is_unchanged(setting, clip_setting, junk):
    assert cp.decide(setting, clip_setting=clip_setting, layer=junk) == cp.decide(setting, clip_setting=clip_setting)


@pytest.mark.parametrize("layer", ["burn", "suppress"])
def test_a_layer_choice_wins_for_the_layer_only(layer):
    got = cp.decide(True, clip_setting=True, layer=layer)
    assert (got["action"], got["decided_by"], got["scope"]) == (layer, "human", "clip")
    assert cp.effective(True, True, layer)["action"] == layer


async def test_decide_render_merges_the_stores_keys_and_passes_the_layer(_bound_plan, tmp_path,  # noqa: F811
                                                                           monkeypatch):
    from workers.clipper_render_plan import _decide_render

    plan, fake_src = _bound_plan
    seen = []

    def fake(clip, project, decision, src):
        seen.append((decision["action"], src))
        return {"source_treatment": "SENTINEL"}

    monkeypatch.setattr(store, "for_render", fake)
    clip, project = _render_objects(plan, fake_src, {"chunks": []}, clip_setting=False)
    clip.caption_layer = "suppress"
    decision = await _decide_render(clip, project, tmp_path)
    assert seen == [("suppress", fake_src)] and decision["source_treatment"] == "SENTINEL"
    assert decision["caption_policy"]["why"] == "a_person_chose_to_suppress_the_layer"


async def test_decide_render_refuses_before_any_ass_is_written(_bound_plan, tmp_path):  # noqa: F811
    from workers.clipper_render_plan import _decide_render

    plan, fake_src = _bound_plan
    clip, project = _render_objects(plan, fake_src, {"chunks": [{"text": "x", "start": 0., "end": 1.}]},
                                    clip_setting=True)
    clip.caption_layer, clip.source_caption_treatment = "burn", None
    with pytest.raises(st.SourceTreatmentRefused) as e:
        await _decide_render(clip, project, tmp_path)
    assert e.value.reason == "burn_over_untreated_source_captions"
    assert list(tmp_path.glob("*.ass")) == []
