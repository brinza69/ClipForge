"""Independent contract tests: a stored automatic layout plan is reusable only
when it carries explicit, valid pixel dimensions equal to the planner's.

See PRPs/clipper-legacy-layout-dimensions-2026-09-24.md. The legacy fixture is
the actual shape of data/clipper/slice4h00test/exports/02dea6f0a9e9.json
(rects authored for 854x480, no src_w/src_h) copied as literals — the suite does
not depend on the user's corpus. The planner under test is the real one; only
the on-disk artifacts it reads (regions/faces) are supplied.
"""
from __future__ import annotations

import copy
import inspect
import math
from types import SimpleNamespace

import pytest

from workers import clipper_render_jobs, clipper_render_plan as crp

LEGACY_854 = {  # 02dea6f0a9e9 / 3e42c5a399c2 as stored
    "face_rect": {"x": 0, "y": 0, "w": 216, "h": 134},
    "game_rect": {"x": 218, "y": 0, "w": 414, "h": 480},
    "chat_rect": None,
}

REGIONS_854 = {  # observations in their explicit proxy frame
    "frame_width": 854, "frame_height": 480,
    "webcam": {"x": 0, "y": 0, "w": 216, "h": 134},
    "gameplay": {"x": 218, "y": 0, "w": 414, "h": 480},
}


def _with_dims(w, h, base=LEGACY_854):
    plan = copy.deepcopy(base)
    plan["src_w"], plan["src_h"] = w, h
    return plan


# --- _plan_fits -------------------------------------------------------------

def test_legacy_small_rects_without_dims_are_not_reusable_on_hd():
    # Old code accepted this by bounds: every rect fits inside 1920x1080.
    assert crp._plan_fits(copy.deepcopy(LEGACY_854), 1920, 1080) is False


def test_legacy_without_dims_not_reusable_even_on_its_own_frame():
    # Unverifiable is unverifiable; there is no bounds-as-identity fallback.
    assert crp._plan_fits(copy.deepcopy(LEGACY_854), 854, 480) is False


def test_explicit_mismatched_dims_not_reusable():
    assert crp._plan_fits(_with_dims(854, 480), 1920, 1080) is False


def test_explicit_matching_dims_reusable():
    assert crp._plan_fits(_with_dims(1920, 1080), 1920, 1080) is True
    assert crp._plan_fits(_with_dims(854, 480), 854, 480) is True


@pytest.mark.parametrize("w,h", [
    (None, 1080), (1920, None), (0, 1080), (1920, 0), (-1920, 1080),
    (True, 1080), (1920, True), ("1920", "1080"), (1920.0, 1080.0),
    (1920.5, 1080), (math.nan, 1080), (math.inf, 1080), (1920, -math.inf),
    ([1920], 1080), ({}, 1080),
])
def test_invalid_plan_dims_false_without_exception(w, h):
    assert crp._plan_fits(_with_dims(w, h), 1920, 1080) is False


def test_missing_one_dimension_key():
    plan = _with_dims(1920, 1080)
    del plan["src_h"]
    assert crp._plan_fits(plan, 1920, 1080) is False


@pytest.mark.parametrize("w,h", [
    (0, 1080), (1920, 0), (-1, 1080), (True, 1080), ("1920", 1080),
    (1920.5, 1080), (math.nan, 1080), (math.inf, 1080), (None, 1080),
])
def test_invalid_target_dims_false_without_exception(w, h):
    assert crp._plan_fits(_with_dims(1920, 1080), w, h) is False


@pytest.mark.parametrize("plan", [None, [], "plan", 3])
def test_non_dict_plan_false(plan):
    assert crp._plan_fits(plan, 1920, 1080) is False


# --- _layout_plan with the real planner --------------------------------------

def _clip(plan, **kw):
    return SimpleNamespace(id="c1", layout_plan=plan, start_time=10.0, end_time=40.0,
                           transcript_text="hi", headline_text="", words=[],
                           content_type=None, **kw)


def _project(w=1920, h=1080, video_path=""):
    return SimpleNamespace(id="p1", width=w, height=h, video_path=video_path,
                           clipper_settings={}, content_type=None,
                           content_type_override=None)


@pytest.fixture
def artifacts(monkeypatch):
    blobs = {"regions": REGIONS_854, "regions_by_segment": [], "faces": {"samples": []}}
    monkeypatch.setattr(crp.storage, "read_artifact", lambda pid, name: blobs.get(name))
    monkeypatch.setattr(crp, "_clip_words", lambda clip: [], raising=False)
    return blobs


def _in_frame(rect, w, h):
    return (rect["x"] >= 0 and rect["y"] >= 0
            and rect["x"] + rect["w"] <= w and rect["y"] + rect["h"] <= h)


def test_layout_plan_replans_legacy_plan_with_real_planner(artifacts):
    stored = copy.deepcopy(LEGACY_854)
    out = crp._layout_plan(_clip(stored), _project())
    assert out is not stored
    assert out.get("src_w") == 1920 and out.get("src_h") == 1080
    assert out.get("game_rect") != LEGACY_854["game_rect"]
    for key in ("face_rect", "game_rect"):
        if out.get(key):
            assert _in_frame(out[key], 1920, 1080)
    # geometry comes from the observations scaled out of their 854x480 frame,
    # so the gameplay crop spans the HD frame height, not the old 480px strip
    assert out["game_rect"]["h"] > 480 * 2
    assert stored == LEGACY_854  # the stored plan is not restamped


def test_layout_plan_reuses_valid_matching_plan_unchanged(artifacts):
    stored = _with_dims(1920, 1080, {
        "face_rect": {"x": 0, "y": 0, "w": 486, "h": 302},
        "game_rect": {"x": 490, "y": 0, "w": 932, "h": 1080}, "chat_rect": None})
    snapshot = copy.deepcopy(stored)
    out = crp._layout_plan(_clip(stored), _project())
    assert out == snapshot


def test_bound_reaction_layout_with_stale_source_raises(artifacts, tmp_path):
    stored = _with_dims(1920, 1080)
    stored["game_content_fit"] = True
    missing = tmp_path / "gone.mp4"
    with pytest.raises(Exception):
        crp._layout_plan(_clip(stored), _project(video_path=str(missing)))


def test_bound_reaction_layout_never_silently_replanned(artifacts, tmp_path):
    bogus = tmp_path / "not-a-video.mp4"
    bogus.write_bytes(b"not a video")
    stored = copy.deepcopy(LEGACY_854)  # no dims, bound composition
    stored["game_content_fit"] = True
    with pytest.raises(Exception):
        crp._layout_plan(_clip(stored), _project(video_path=str(bogus)))


# --- preview and export share the decision -----------------------------------

def test_preview_and_export_go_through_common_decision():
    assert "_layout_plan(" in inspect.getsource(crp._decide_render)
    for handler in (clipper_render_jobs.handle_export, clipper_render_jobs.handle_preview):
        src = inspect.getsource(handler)
        assert "_decide_render(" in src
        assert "_plan_fits" not in src and ".layout_plan" not in src
