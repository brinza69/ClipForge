"""The wire between the dynamic editor and the export path.

The editor, its cameras and its renderer were complete and tested for months
while NOTHING in workers/ or routers/ imported any of them — every clip the
pipeline ever produced was a static split screen. These tests exist so that
cannot silently become true again, and so the fallbacks are the ones intended:
a clip that cannot be cut dynamically must still export, statically.
"""

from __future__ import annotations

import pytest

from workers import clipper_render_jobs as jobs


class _Clip:
    id = "c1"
    project_id = "p1"
    start_time = 100.0
    end_time = 130.0
    transcript_text = "hello there"
    headline_text = ""
    caption_plan: dict | None = None


class _Project:
    id = "p1"
    width = 1920
    height = 1080
    clipper_settings: dict = {}


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """_dynamic_plan with the media analysis and the planner replaced."""
    proxy = tmp_path / "proxy.mp4"
    proxy.write_bytes(b"x")

    monkeypatch.setattr(jobs.storage, "paths", lambda _pid: {"proxy": proxy})
    monkeypatch.setattr(jobs.storage, "read_artifact",
                        lambda _pid, _name: {"proxy_width": 480, "proxy_height": 270})

    from services.clipper import dynamic_edit, dynamic_window

    monkeypatch.setattr(dynamic_window, "analyse_window", lambda *a, **k: {
        "faces": [{"t": 100.0, "boxes": [[10, 10, 40, 40]]}],
        "motion": [0.1, 0.2], "focus": [-1.0, 120.0], "detail": [3.0, 4.0],
        "ui": [0.0, 0.0], "band": (0.25, 1.0, 0.0, 0.8), "hop": 0.25,
    })
    return dynamic_edit, proxy


async def test_a_planned_edit_records_the_frame_it_was_measured_in(wired, monkeypatch):
    """Same lesson as the static layout plan: a plan is crops in source pixels
    and is meaningless without the frame they were measured against."""
    dynamic_edit, _proxy = wired
    monkeypatch.setattr(dynamic_edit, "plan_dynamic_edit", lambda *a, **k: {
        "shots": [{"camera": "face"}, {"camera": "game"}], "warnings": []})

    plan = await jobs._dynamic_plan(_Clip(), _Project(), 1920, 1080)
    assert plan["src_w"] == 1920 and plan["src_h"] == 1080
    assert plan["band"] == [0.25, 1.0, 0.0, 0.8]
    assert plan["faces_seen"] == 1


async def test_a_single_shot_falls_back_to_the_static_layout(wired, monkeypatch):
    """One shot is a static crop with extra steps, and the static path does it
    better — it keeps the face band and the chat exclusion."""
    dynamic_edit, _proxy = wired
    monkeypatch.setattr(dynamic_edit, "plan_dynamic_edit", lambda *a, **k: {
        "shots": [{"camera": "face"}], "warnings": []})

    assert await jobs._dynamic_plan(_Clip(), _Project(), 1920, 1080) is None


async def test_no_proxy_means_no_dynamic_edit(monkeypatch, tmp_path):
    """The editor measures the PROXY. Without one there is nothing to plan from,
    and losing the export over it would be worse than a static render."""
    monkeypatch.setattr(jobs.storage, "paths",
                        lambda _pid: {"proxy": tmp_path / "missing.mp4"})
    assert await jobs._dynamic_plan(_Clip(), _Project(), 1920, 1080) is None


async def test_a_zero_length_window_is_refused(wired, monkeypatch):
    clip = _Clip()
    clip.end_time = clip.start_time
    assert await jobs._dynamic_plan(clip, _Project(), 1920, 1080) is None


def test_the_export_handler_reaches_the_dynamic_renderer():
    """The assertion that would have caught the whole gap: the export path has
    to name the dynamic renderer, not merely have one available in the tree."""
    import inspect

    src = inspect.getsource(jobs.handle_export)
    assert "dynamic_render" in src, "handle_export cannot reach the multi-shot renderer"
    assert "_dynamic_plan" in src, "handle_export never plans a shot list"


# ── the words the planner cuts on ────────────────────────────────────────────
#
# Second missing wire of the same shape as the one above. `_candidate` handed
# the planner four keys and no `words`, so `_boundaries` placed every cut on
# audio peaks and scene changes, and `_speech_ratio` reported the streamer as
# silent in every shot. Measured on clip 6b34b8d37259: 9 shots without the
# words against 11 with them, and the 9 are what shipped.


def _clip_with_captions() -> _Clip:
    clip = _Clip()
    clip.caption_plan = {"chunks": [
        {"text": "hello there", "start": 0.0, "end": 1.0,
         "words": [{"word": "hello", "start": 0.0, "end": 0.4},
                   {"word": "there", "start": 0.6, "end": 1.0}]},
        {"text": "again", "start": 2.0, "end": 2.5,
         "words": [{"word": "again", "start": 2.0, "end": 2.5}]},
    ]}
    return clip


def test_the_planner_is_given_the_words_on_the_source_clock():
    """The caption plan counts from the clip; `dynamic_edit` subtracts
    `clip_start` from every word, so the offset has to be put back."""
    cand = jobs._candidate(_clip_with_captions())
    assert [w["word"] for w in cand["words"]] == ["hello", "there", "again"]
    assert cand["words"][0]["start"] == 100.0        # not 0.0
    assert cand["words"][-1]["end"] == 102.5
    assert cand["start"] <= cand["words"][0]["start"] <= cand["end"]


def test_the_words_move_the_cuts():
    """The defect as a property, not a number: a planner that cannot see the
    pauses cuts somewhere else. If this ever passes with an empty word list the
    wire is disconnected again."""
    from services.clipper import dynamic_edit

    signals = {"audio": {"peaks": [101.0, 104.0]}, "scenes": [103.0]}
    faces = [{"t": 100.0, "boxes": [[10, 10, 40, 40]]}]
    common = dict(signals=signals, face_track=faces, src_w=1920, src_h=1080,
                  proxy_w=480, proxy_h=270)

    with_words = dynamic_edit.plan_dynamic_edit(
        jobs._candidate(_clip_with_captions()), **common)
    without = dynamic_edit.plan_dynamic_edit(
        jobs._candidate(_Clip()), **common)

    cuts_with = [s["t0"] for s in with_words["shots"]]
    cuts_without = [s["t0"] for s in without["shots"]]
    assert cuts_with != cuts_without, (
        "the word timings changed nothing — _candidate is not passing them")


def test_a_clip_without_captions_still_plans():
    """Captions are optional, so this must degrade to the old behaviour rather
    than raise on the way to a render."""
    assert jobs._candidate(_Clip())["words"] == []
    clip = _Clip()
    clip.caption_plan = {"chunks": [{"text": "no timings"}]}
    assert jobs._candidate(clip)["words"] == []


def test_the_export_handler_reviews_the_cut():
    """The assertion that catches the failure this repo has hit four times: a
    structure that is built, tested, and read by nothing."""
    import inspect

    src = inspect.getsource(jobs.handle_export)
    assert "_review" in src, "handle_export never reviews the cut it renders"
    assert '"review"' in src, "the verdict never reaches the sidecar"


async def test_the_working_signals_never_reach_the_sidecar(wired, monkeypatch):
    """The face track and the UI panels ride on the plan so Pass D and the
    caption placer do not decode the window again. They are working data, not
    deliverable, and every one of them has to be popped — this test exists to
    fail when a new one is added and forgotten, which it has already done once."""
    dynamic_edit, _proxy = wired
    monkeypatch.setattr(dynamic_edit, "plan_dynamic_edit", lambda *a, **k: {
        "shots": [{"camera": "face"}, {"camera": "game"}], "warnings": []})

    plan = await jobs._dynamic_plan(_Clip(), _Project(), 1920, 1080)
    assert plan["_review_faces"], "the reviewer would have to decode again"
    assert "_panels" in plan, "the caption placer has nothing to avoid"

    import inspect
    popped = inspect.getsource(jobs.handle_export)
    for key in [k for k in plan if k.startswith("_")]:
        assert f'pop("{key}"' in popped, f"{key} would be written to the sidecar"


def test_the_multi_shot_path_is_what_ships():
    """Turned on 2026-08-17 by the owner, after a viewer judged the edit and an
    A/B settled the one fault they named.

    Kept as a test because the value is a decision, not an accident: everything
    measured since — cuts on speech pauses, the wide gameplay framing, Pass D,
    the audio ceiling — lands on this path and NOWHERE else, so flipping it back
    silently would strand all of it."""
    from config import Settings

    assert Settings().clipper_dynamic_edit is True
