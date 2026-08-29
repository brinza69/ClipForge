"""The wire between the dynamic editor and the export path.

The editor, its cameras and its renderer were complete and tested for months
while NOTHING in workers/ or routers/ imported any of them — every clip the
pipeline ever produced was a static split screen. These tests exist so that
cannot silently become true again, and so the fallbacks are the ones intended:
a clip that cannot be cut dynamically must still export, statically.
"""

from __future__ import annotations

from pathlib import Path

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
    # Present because the model has it. Left off, `_caption_y` raised, was
    # swallowed by its own never-lose-the-export guard, and returned None — the
    # same answer it gives when there is nothing to do, so a test could "pass"
    # while proving nothing.
    layout_plan: dict | None = None
    # The three R2 carries into the render decision. `content_confidence` is
    # None here on purpose: that is the state most real clips are in, and it is
    # the one that must resolve to the conservative profile.
    # The clip's own length. R3b clamps the proposal's timeline to it, so a
    # sample at the end of the dense track cannot describe video that is not
    # there.
    duration = 30.0
    content_type: str | None = "gaming"
    content_confidence: float | None = None
    content_type_origin: str | None = None


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


def test_both_handlers_reach_the_dynamic_renderer():
    """The assertion that would have caught the whole gap: a render path has to
    NAME the dynamic renderer, not merely have one available in the tree.

    Widened to the preview on 2026-08-17. It took the static renderer
    unconditionally while the export could take the multi-shot one, so a person
    approved a fixed split screen and received an edit with a dozen cuts in it.
    The shot list is planned once now, in `_decide_render`, and both handlers
    consume the same answer — which is why this checks the decision function
    rather than either handler's body.
    """
    import inspect

    decide = inspect.getsource(jobs._decide_render)
    assert "_dynamic_plan" in decide, "nothing plans a shot list"

    for handler in (jobs.handle_export, jobs.handle_preview):
        src = inspect.getsource(handler)
        assert "_decide_render" in src, (
            f"{handler.__name__} decides what to render on its own again")
        assert "dynamic_render" in src, (
            f"{handler.__name__} cannot reach the multi-shot renderer")


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


def test_a_hand_placed_caption_is_not_moved_by_the_export():
    """The export re-places the caption around the game UI it detects in the
    cut. That is right when nobody has expressed a preference and wrong the
    moment somebody has — an edit the next export silently undoes is worse than
    having no editor at all."""
    clip = _Clip()
    clip.caption_plan = {"chunks": [{"text": "x"}], "y_pct": 0.62,
                         "y_pct_manual": True}
    dyn = {"_panels": [{"x": 0, "y": 800, "w": 300, "h": 120}],
           "shots": [{"rect": {"x": 0, "y": 0, "w": 606, "h": 1080}}]}
    assert jobs._caption_y(clip, dyn) is None

    # Without the flag the same clip IS re-placed, or the check above proves
    # nothing about the flag.
    clip.caption_plan = {"chunks": [{"text": "x"}], "y_pct": 0.62}
    assert jobs._caption_y(clip, dyn) is not None


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


# ── the two paths take the same options ──────────────────────────────────────
#
# `dynamic_edit` became the default on 2026-08-17 and the dynamic call site was
# never given `watermark`, `drop_spans`, `has_audio` or `is_cancelled` — four
# arguments the static call beside it had always had. So the watermark silently
# vanished from every export, `trim_silence` did nothing, and a queued
# cancellation was ignored.
#
# Verified on a real render before these were written: same clip, options off
# then on, 35.83s -> 33.43s with the captions shifted by exactly the 2.40s
# removed, and "ClipForge" burned across the bottom.


def _plan():
    return {"duration": 30.0, "hits": [],
            "shots": [{"rect": {"x": 0, "y": 0, "w": 608, "h": 1080}}]}


def _cmd(**kw):
    from services.clipper.dynamic_render import build_dynamic_cmd

    return build_dynamic_cmd("src.mp4", _plan(), "c.txt", None, "out.mp4",
                             start=10.0, duration=30.0, src_w=1920, src_h=1080,
                             **kw)


def test_the_dynamic_renderer_burns_the_watermark():
    graph = _cmd(watermark="ClipForge")[_cmd(watermark="ClipForge").index("-filter_complex") + 1]
    assert "drawtext" in graph and "ClipForge" in graph
    assert "drawtext" not in _cmd()[_cmd().index("-filter_complex") + 1]


def test_the_dynamic_renderer_cuts_dead_air_and_shortens_the_output():
    """`-t` sits after `-i`, so it is an OUTPUT duration: left at the window
    length ffmpeg reads PAST the window to refill the seconds `select` dropped,
    and the dead air comes back as whatever followed it."""
    cmd = _cmd(drop_spans=[(5.0, 8.5)])
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "select=" in graph and "setpts=" in graph
    assert float(cmd[cmd.index("-t") + 1]) == pytest.approx(26.5)
    assert float(_cmd()[_cmd().index("-t") + 1]) == pytest.approx(30.0)


def test_a_silent_source_survives_a_trim():
    """`-map 0:a?` tolerates a file with no audio track; `[0:a]` inside a
    filtergraph does not, and would fail the whole render."""
    cmd = _cmd(drop_spans=[(5.0, 8.5)], has_audio=False)
    assert "[0:a]" not in cmd[cmd.index("-filter_complex") + 1]
    assert "0:a?" in cmd


def test_loudness_moves_into_the_graph_only_when_the_audio_was_filtered():
    """ffmpeg will not run `-af` on a stream a complex graph produced, so with
    dead air removed the chain has to go inside. With no drops the command is
    exactly what it was before the option existed."""
    trimmed = _cmd(drop_spans=[(5.0, 8.5)])
    assert "-af" not in trimmed
    assert "loudnorm" in trimmed[trimmed.index("-filter_complex") + 1]
    assert "-af" in _cmd()


def test_a_cancelled_export_does_not_start_the_encode():
    from services.clipper.dynamic_render import render_dynamic_clip

    with pytest.raises(Exception) as caught:
        render_dynamic_clip("src.mp4", _plan(), "out.mp4", start=0.0,
                            work_dir=".", is_cancelled=lambda: True)
    assert "cancel" in type(caught.value).__name__.lower() + str(caught.value).lower()


def test_dynamic_renderer_publishes_the_final_path_atomically(tmp_path, monkeypatch):
    from services.clipper import dynamic_render
    from services.clipper.dynamic_render import render_dynamic_clip

    final = tmp_path / "clip.mp4"
    final.write_bytes(b"previous render")
    seen = {}

    def fake_run(cmd, **_kwargs):
        seen["temp"] = Path(cmd[-1])
        seen["temp"].write_bytes(b"new render" * 300)

    monkeypatch.setattr(dynamic_render, "run", fake_run)
    plan = {
        "duration": 30.0,
        "hits": [],
        "style": {},
        "shots": [{
            "t0": 0.0,
            "t1": 30.0,
            "rect": {"x": 0, "y": 0, "w": 608, "h": 1080},
            "anchor": [960, 540],
        }],
    }
    result = render_dynamic_clip(
        "source.mp4", plan, str(final), start=0.0,
        work_dir=tmp_path,
    )

    assert result["path"] == str(final)
    assert final.read_bytes() == b"new render" * 300
    assert seen["temp"] != final
    assert not seen["temp"].exists()


def test_the_export_handler_passes_all_four_to_the_dynamic_call():
    """The regression that mattered was at the CALL SITE, not in the renderer:
    every option above existed on the static side and simply was not handed
    over."""
    import inspect

    src = inspect.getsource(jobs.handle_export)
    dynamic_call = src[src.index("render_dynamic_clip("):]
    dynamic_call = dynamic_call[:dynamic_call.index("else:")]
    for arg in ("watermark=", "drop_spans=", "has_audio=", "is_cancelled="):
        assert arg in dynamic_call, f"the dynamic call lost {arg}"


# --- Batch R0: the sidecar contract -----------------------------------------
#
# The sidecar is what the audit reads. Everything below is about it describing
# the file that was actually written — not the row the render started from.


def test_the_sidecar_names_the_renderer_that_actually_ran():
    """Stamped at write time, never backfilled, and NOT one constant for both
    paths: a static export filed under the dynamic renderer's version is
    attributable to a grammar of shots it never had. The 58 pilot exports
    predate the key and must keep reading as unavailable."""
    import inspect

    src = inspect.getsource(jobs.handle_export)
    assert "dynamic_render.RENDER_VERSION if dyn" in src
    assert "else static_render.RENDER_VERSION" in src

    from services.clipper import dynamic_render, render as static_render

    assert dynamic_render.RENDER_VERSION != static_render.RENDER_VERSION


def test_the_sidecar_carries_every_key_the_fingerprint_is_taken_over():
    """The digest is computed from the sidecar through `edit_quality`. A key the
    projection reads and the writer never writes fingerprints as `null` for
    every export — silently, and identically for all of them."""
    import inspect

    from services.clipper import edit_quality

    body = inspect.getsource(jobs.handle_export)
    body = body[body.index("body = {"):body.index('body["input_fingerprint"]')]
    for key in edit_quality.FINGERPRINT_KEYS:
        assert f'"{key}"' in body, f"the sidecar never writes {key}"


def test_the_fingerprint_is_computed_through_the_audits_own_projection():
    """Not a payload built by hand here. Two definitions drift, and then the
    check passes for a file whose plan has changed underneath it."""
    import inspect

    src = inspect.getsource(jobs.handle_export)
    assert 'body["input_fingerprint"] = edit_quality.input_fingerprint(body)' in src


def test_the_sidecar_records_the_seconds_the_render_removed():
    """Without `drop_spans` the sidecar describes a longer clip than the file,
    and every time in it — captions, shot boundaries — is on a clock the mp4
    does not keep."""
    import inspect

    assert '"drop_spans": drop' in inspect.getsource(jobs.handle_export)


def test_the_sidecar_keeps_the_stored_caption_plan_and_the_height_separately():
    """Not a pre-merged "effective" plan. Merging here would put a second copy
    of `_write_ass`'s rule in the worker, and the merged result cannot be taken
    apart again by anything that needs to know what was decided at score time
    and what was decided at render time."""
    import inspect

    src = inspect.getsource(jobs.handle_export)
    assert '"caption_plan": clip.caption_plan' in src
    assert '"caption_y": caption_y' in src


def test_a_missing_source_sizes_to_none_not_zero(tmp_path):
    """Part of the fingerprint: the same path with a different file behind it is
    a different input. A source that is gone and a source that is empty are
    different facts, and 0 would spell them the same."""
    real = tmp_path / "source.mp4"
    real.write_bytes(b"x" * 17)
    assert jobs._size_bytes(real) == 17
    assert jobs._size_bytes(tmp_path / "gone.mp4") is None


# --- Batch R1: removing an invisible cut must not change the renderer --------


async def test_a_plan_merged_down_to_one_shot_stays_dynamic(wired, monkeypatch):
    """The R1 merge joins two `fit` shots that deliver one image. A clip whose
    only fault was that invisible cut comes back as a single shot, and counting
    THAT would drop it onto the static path: different crop, different captions,
    different renderer version — for a change that was supposed to remove one
    redundant command and nothing else."""
    dynamic_edit, _proxy = wired
    monkeypatch.setattr(dynamic_edit, "plan_dynamic_edit", lambda *a, **k: {
        "shots": [{"camera": "face"}], "warnings": [],
        # What the planner decided, before the merge absorbed the second shot.
        "shot_count_before_merge": 2, "equivalent_cuts_removed": 1})

    plan = await jobs._dynamic_plan(_Clip(), _Project(), 1920, 1080)
    assert plan is not None, "an invisible cut cost this clip the dynamic path"
    assert len(plan["shots"]) == 1


async def test_a_natively_single_shot_plan_still_falls_back(wired, monkeypatch):
    """The other direction, unchanged: a planner that only ever found one shot
    is a static crop with extra steps."""
    dynamic_edit, _proxy = wired
    monkeypatch.setattr(dynamic_edit, "plan_dynamic_edit", lambda *a, **k: {
        "shots": [{"camera": "face"}], "warnings": [],
        "shot_count_before_merge": 1, "equivalent_cuts_removed": 0})

    assert await jobs._dynamic_plan(_Clip(), _Project(), 1920, 1080) is None


# --- Batch R2: the shadow must be inert -------------------------------------


async def _decide_in(mode: str, wired, monkeypatch, tmp_path):
    dynamic_edit, _proxy = wired
    monkeypatch.setattr(dynamic_edit, "plan_dynamic_edit", lambda *a, **k: {
        "shots": [{"camera": "face", "t0": 0.0, "t1": 2.0, "composition": "crop",
                   "rect": {"x": 0, "y": 0, "w": 540, "h": 960}, "anchor": [270, 480]},
                  {"camera": "game", "t0": 2.0, "t1": 4.0, "composition": "crop",
                   "rect": {"x": 700, "y": 0, "w": 606, "h": 1080}, "anchor": [1003, 540]}],
        "warnings": [], "style": {}})

    class _P(_Project):
        clipper_settings = {"edit_mode": mode}

    return await jobs._decide_render(_Clip(), _P(), tmp_path)


async def test_the_shadow_changes_nothing_about_the_render(wired, monkeypatch, tmp_path):
    """R2's gate, end to end rather than by reading source. The profile is
    resolved and recorded in both modes; every decision the renderer acts on —
    the plan, the crop, the captions, the trim, the fps, the watermark — has to
    come back identical, or "shadow" is not a shadow."""
    from services.clipper import edit_profiles, render_input

    legacy = await _decide_in(edit_profiles.LEGACY_DYNAMIC, wired, monkeypatch, tmp_path)
    shadow = await _decide_in(edit_profiles.CONTENT_AWARE_SHADOW, wired, monkeypatch, tmp_path)

    assert legacy["edit_profile"]["profile"] == shadow["edit_profile"]["profile"]
    assert legacy["edit_profile"]["mode"] != shadow["edit_profile"]["mode"]
    # Neither mode applies it, and that is the point of the batch.
    assert not legacy["edit_profile"]["applied"]
    assert not shadow["edit_profile"]["applied"]

    for key in ("plan", "dyn", "drop", "fps", "caption_y", "watermark"):
        assert legacy[key] == shadow[key], key

    # And the digest the audit rechecks does not move either.
    def _fingerprint(decision):
        return render_input.input_fingerprint({
            "layout_plan": decision["plan"], "dynamic_plan": decision["dyn"],
            "drop_spans": decision["drop"],
            "render": {"fps": decision["fps"], "watermark": decision["watermark"]},
        })

    assert _fingerprint(legacy) == _fingerprint(shadow)


async def test_a_rig_configured_to_an_unavailable_mode_still_delivers_legacy(
        wired, monkeypatch, tmp_path):
    """config.py is a second door into this setting, and the router's refusal
    only guards the third. A rig set to `content_aware` used to produce
    `applied: true` on every render while no grammar was connected to it."""
    from config import settings
    from services.clipper import edit_profiles

    monkeypatch.setattr(settings, "clipper_edit_mode", edit_profiles.CONTENT_AWARE)
    decision = await _decide_in("", wired, monkeypatch, tmp_path)
    assert decision["edit_profile"]["mode"] == edit_profiles.DEFAULT_MODE
    assert not decision["edit_profile"]["applied"]


# --- Batch R3a: the shadow instrumentation, executed rather than read --------


async def test_the_creator_view_reaches_the_export_and_the_working_key_does_not(
        wired, monkeypatch, tmp_path):
    """Grepping the source proves the line exists, not that it runs. This runs
    the decision and looks at what would be written."""
    from services.clipper import edit_profiles

    decision = await _decide_in(edit_profiles.CONTENT_AWARE_SHADOW, wired,
                                monkeypatch, tmp_path)
    view = decision["creator_view"]
    assert view is not None
    assert view["schema"] == "creator_view_v1"
    assert view["scope"] == "composition_only_existing_shots"
    # One entry per shot the planner produced, each carrying both answers.
    assert len(view["shots"]) == len(decision["dyn"]["shots"])
    assert all("composition" in s and "delivered" in s for s in view["shots"])
    assert all(s["composition"] in ("crop", "fit") for s in view["shots"])

    # The anchor rides on the plan to get here, and must not ride any further:
    # the sidecar is a deliverable, and a face track is not part of it.
    dyn = decision["dyn"]
    for key in ("_review_faces", "_panels", "_stable_track", "_motion", "_motion_hop"):
        dyn.pop(key, None)
    assert not [k for k in dyn if k.startswith("_")], "working data in the plan"


async def test_the_creator_view_changes_nothing_the_renderer_reads(
        wired, monkeypatch, tmp_path):
    """R3a is instrumentation. If the proposal moved a single delivered frame,
    the shadow would stop being comparable with what R2 froze."""
    from services.clipper import edit_profiles, render_input

    legacy = await _decide_in(edit_profiles.LEGACY_DYNAMIC, wired, monkeypatch, tmp_path)
    shadow = await _decide_in(edit_profiles.CONTENT_AWARE_SHADOW, wired,
                              monkeypatch, tmp_path)

    for key in ("plan", "dyn", "drop", "fps", "caption_y", "watermark"):
        assert legacy[key] == shadow[key], key

    # And a fingerprint taken over the recipe does not move when only the
    # proposal differs — which is why `creator_view` is not one of its keys.
    assert "creator_view" not in render_input.FINGERPRINT_KEYS
    fingerprints = {
        render_input.input_fingerprint({
            "layout_plan": d["plan"], "dynamic_plan": d["dyn"],
            "drop_spans": d["drop"],
            "render": {"fps": d["fps"], "watermark": d["watermark"]},
        })
        for d in (legacy, shadow)
    }
    assert len(fingerprints) == 1


async def test_the_regime_view_is_recorded_beside_the_creator_view(
        wired, monkeypatch, tmp_path):
    """R3b, executed rather than read. Both proposals describe the same
    timeline: the hop comes from the first, so they cannot drift apart."""
    from services.clipper import edit_profiles

    decision = await _decide_in(edit_profiles.CONTENT_AWARE_SHADOW, wired,
                                monkeypatch, tmp_path)
    view = decision["regime_view"]
    assert view["schema"] == "regime_view_v2"
    assert view["scope"] == "regime_segments_at_timeline_resolution"
    assert view["sample_hop_s"] == decision["creator_view"]["sample_hop_s"]

    # Contiguous and gapless, and at the SAMPLE rate rather than the shot's:
    # the boundaries fall where the signals change, not where the legacy edit
    # happened to cut.
    segments = view["segments"]
    assert segments[0]["t0"] == 0.0
    for a, b in zip(segments, segments[1:]):
        assert a["t1"] == b["t0"], "a gap between segments is time nobody owns"
    assert sum(s["samples"] for s in segments) == view["samples"]

    # And the treatment merge never asks for more cuts than there are regimes.
    assert view["treatment_boundaries"] <= view["regime_boundaries"]


async def test_neither_proposal_moves_the_delivered_plan(wired, monkeypatch, tmp_path):
    """Two batches of instrumentation now ride on every render. If either had
    moved a frame, the shadow would stop being comparable with what R2 froze."""
    from services.clipper import edit_profiles

    legacy = await _decide_in(edit_profiles.LEGACY_DYNAMIC, wired, monkeypatch, tmp_path)
    shadow = await _decide_in(edit_profiles.CONTENT_AWARE_SHADOW, wired,
                              monkeypatch, tmp_path)
    for key in ("plan", "dyn", "drop", "fps", "caption_y", "watermark"):
        assert legacy[key] == shadow[key], key
    assert legacy["regime_view"] == shadow["regime_view"]
