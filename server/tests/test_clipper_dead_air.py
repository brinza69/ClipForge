"""Dead seconds inside a chosen window, and the arithmetic of removing them.

§15. The window's EDGES were already right — `story.variants_from_anchor`
competes cuts that open and close in different places — and its middle was
never looked at.

The subtle half is not the detector, it is what removing time does to
everything measured against the old clock: captions are positioned absolutely,
so an overlay that is not remapped drifts further out of sync with every second
cut.
"""

from __future__ import annotations

import pytest

from services.clipper import dead_air
from services.clipper.candidate_terms import PAUSE_KEEP_S


def _cand(start=100.0, end=140.0):
    return {"start": start, "end": end}


def _sig(*spans):
    return {"silence": [list(s) for s in spans]}


def _words(*pairs):
    return [{"word": "x", "start": a, "end": b} for a, b in pairs]


# ── what counts as dead ──────────────────────────────────────────────────────


def test_a_long_silence_in_the_middle_is_cut():
    spans = dead_air.dead_spans(_cand(), _sig((110.0, 115.0)), [])
    assert len(spans) == 1
    lo, hi = spans[0]
    # A beat survives on each side, so what is left still sounds like a pause.
    assert lo > 10.0 and hi < 15.0
    assert hi - lo == pytest.approx(5.0 - PAUSE_KEEP_S / 2.0, abs=0.01)


def test_a_beat_is_not_dead_air():
    """`PAUSE_KEEP_S` already decides this for the boundary rules."""
    assert dead_air.dead_spans(_cand(), _sig((110.0, 110.0 + PAUSE_KEEP_S - 0.1)), []) == []


def test_a_silence_holding_a_word_is_left_alone():
    """The RMS floor is an audio measure and marks quiet speech as silence;
    the word timings are the better evidence and they veto."""
    assert dead_air.dead_spans(
        _cand(), _sig((110.0, 115.0)), _words((112.0, 112.4))) == []


def test_the_edges_belong_to_the_boundary_rules():
    """Trimming inward from the ends would silently undo the reaction keep and
    the tail release, which were chosen for their own measured reasons."""
    assert dead_air.dead_spans(_cand(), _sig((100.0, 105.0)), []) == []
    assert dead_air.dead_spans(_cand(), _sig((135.0, 140.0)), []) == []


def test_a_very_short_clip_is_never_trimmed():
    assert dead_air.dead_spans(_cand(100.0, 101.0), _sig((100.2, 100.9)), []) == []


def test_spans_come_back_sorted_and_clip_relative():
    spans = dead_air.dead_spans(
        _cand(), _sig((125.0, 130.0), (110.0, 115.0)), [])
    assert [round(a) for a, _b in spans] == [10, 25]


# ── the arithmetic of removal ────────────────────────────────────────────────


def test_time_before_a_cut_does_not_move():
    assert dead_air.remap_time(5.0, [(10.0, 14.0)]) == 5.0


def test_time_after_a_cut_moves_back_by_what_was_removed():
    assert dead_air.remap_time(20.0, [(10.0, 14.0)]) == 16.0
    assert dead_air.remap_time(30.0, [(10.0, 14.0), (20.0, 22.0)]) == 24.0


def test_time_inside_a_cut_collapses_onto_its_start():
    """The only answer that keeps the sequence non-decreasing."""
    assert dead_air.remap_time(12.0, [(10.0, 14.0)]) == 10.0


def test_remapping_never_reorders_captions():
    spans = [(10.0, 14.0), (25.0, 27.5)]
    times = [0.0, 5.0, 9.9, 12.0, 15.0, 24.0, 26.0, 30.0]
    out = [dead_air.remap_time(t, spans) for t in times]
    assert out == sorted(out), out


def test_reconstructing_a_trim_splits_a_shot_and_names_the_jump():
    """The two surviving pieces meet on the delivered clock, but source time
    jumps across the removed span. That is not a normal planner cut."""
    shots = [{"t0": 0.0, "t1": 10.0, "composition": "fit"}]
    pieces, jumps = dead_air.delivered_shots(shots, [(4.0, 6.0)])

    assert [(p["t0"], p["t1"]) for p in pieces] == [(0.0, 4.0), (4.0, 8.0)]
    assert jumps == {1}
    assert shots == [{"t0": 0.0, "t1": 10.0, "composition": "fit"}], (
        "an audit must not rewrite the persisted plan it is measuring")


def test_a_shot_wholly_inside_removed_time_disappears():
    shots = [
        {"t0": 0.0, "t1": 2.0, "composition": "crop"},
        {"t0": 2.0, "t1": 4.0, "composition": "fit"},
        {"t0": 4.0, "t1": 8.0, "composition": "crop"},
    ]
    pieces, jumps = dead_air.delivered_shots(shots, [(2.0, 4.0)])

    assert [(p["t0"], p["t1"], p["composition"]) for p in pieces] == [
        (0.0, 2.0, "crop"), (2.0, 6.0, "crop")]
    assert jumps == {1}


def test_a_trim_that_does_not_intersect_a_shot_leaves_it_unchanged():
    shots = [{"t0": 0.0, "t1": 4.0, "composition": "crop"}]
    pieces, jumps = dead_air.delivered_shots(shots, [(6.0, 8.0)])
    assert pieces == shots
    assert jumps == set()


def test_multiple_removed_spans_produce_one_jump_each():
    pieces, jumps = dead_air.delivered_shots(
        [{"t0": 0.0, "t1": 12.0, "composition": "crop"}],
        [(2.0, 4.0), (6.0, 8.0)])
    assert [(p["t0"], p["t1"]) for p in pieces] == [
        (0.0, 2.0), (2.0, 4.0), (4.0, 8.0)]
    assert jumps == {1, 2}


# These three used `start`/`end` until 2026-08-17 and passed for months while
# the feature they cover removed every caption from the clip. The captioner
# emits `start_t`/`end_t` and `remap_overlays` read `start`/`end`, so every
# overlay remapped to (0, 0), was dropped as "wholly inside removed time", and
# `_write_ass` returned None for the empty list. The function and its tests
# agreed with each other and with nothing else.


def test_overlays_move_with_the_cut():
    overlays = [{"start_t": 2.0, "end_t": 4.0, "text": "a"},
                {"start_t": 20.0, "end_t": 22.0, "text": "b"}]
    out = dead_air.remap_overlays(overlays, [(10.0, 14.0)])
    assert out[0]["start_t"] == 2.0 and out[0]["end_t"] == 4.0
    assert out[1]["start_t"] == 16.0 and out[1]["end_t"] == 18.0
    assert out[1]["text"] == "b", "the rest of the overlay must survive"


def test_an_overlay_wholly_inside_removed_time_is_dropped():
    out = dead_air.remap_overlays([{"start_t": 11.0, "end_t": 12.0}],
                                  [(10.0, 14.0)])
    assert out == []


def test_no_spans_leaves_the_overlays_untouched():
    overlays = [{"start_t": 2.0, "end_t": 4.0}]
    assert dead_air.remap_overlays(overlays, []) == overlays


def test_the_overlay_shape_is_the_one_the_captioner_emits():
    """The guard the three tests above needed. It asserts the CONTRACT rather
    than the shape this module happens to use: whatever
    `caption_plan_to_overlays` produces has to survive a remap, and the keys
    `build_overlays_ass` reads have to be the keys that moved.
    """
    from services.clipper.captions import caption_plan_to_overlays

    plan = {
        "chunks": [{"start": 1.0, "end": 3.0, "text": "before the cut"},
                   {"start": 20.0, "end": 22.0, "text": "after the cut"}],
        "template_id": "bold_impact", "y_pct": 0.6,
    }
    overlays = caption_plan_to_overlays(plan)
    assert overlays, "the fixture no longer produces overlays"
    assert "start_t" in overlays[0] and "end_t" in overlays[0]

    out = dead_air.remap_overlays(overlays, [(10.0, 14.0)])
    assert len(out) == len(overlays), (
        f"remapping wiped {len(overlays) - len(out)} of {len(overlays)} "
        f"overlays — it is reading keys the captioner does not emit")
    assert out[-1]["start_t"] == overlays[-1]["start_t"] - 4.0


# ── the ffmpeg expression ────────────────────────────────────────────────────


def test_the_select_expression_keeps_everything_when_nothing_is_cut():
    assert dead_air.select_expr([]) == "1"


def test_the_select_expression_excludes_each_span():
    expr = dead_air.select_expr([(1.0, 2.0), (5.0, 6.5)])
    assert expr == "not(between(t,1.000,2.000)+between(t,5.000,6.500))"


def test_the_render_command_is_unchanged_when_nothing_is_cut():
    """Every export so far took this path; enabling the option must not alter
    the command for a clip with no dead air in it."""
    from services.clipper.render import build_render_cmd

    args = dict(fps=30, crf=18, preset="slow")
    plain = build_render_cmd("s.mp4", _cand(), {}, None, "o.mp4", **args)
    empty = build_render_cmd("s.mp4", _cand(), {}, None, "o.mp4",
                             drop_spans=[], **args)
    assert plain == empty
    # The audio is taken straight from the input rather than through a select.
    # It still passes the loudness chain — that is not a cut, and it is applied
    # to every render on both paths.
    graph = plain[plain.index("-filter_complex") + 1]
    assert ";[0:a]" in graph and "aselect" not in graph


def test_the_render_command_cuts_and_closes_the_gaps():
    from services.clipper.render import build_render_cmd

    cmd = build_render_cmd("s.mp4", _cand(), {}, None, "o.mp4",
                           fps=30, crf=18, preset="slow",
                           drop_spans=[(10.0, 14.0)])
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "select='not(between(t,10.000,14.000))'" in graph
    assert "aselect='not(between(t,10.000,14.000))'" in graph
    # Without the setpts pair the removed seconds come back as freezes.
    assert "setpts=N/FRAME_RATE/TB" in graph and "asetpts=N/SR/TB" in graph
    # The CUT audio is what reaches the output: [acut] feeds the loudness chain
    # and the untouched input is not mapped at all.
    assert ";[acut]" in graph and "0:a?" not in cmd
    assert "[aout]" in [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]


def test_the_output_duration_shrinks_by_what_was_cut():
    """`-t` sits after `-i`, so it caps the OUTPUT. Left at the window length,
    ffmpeg reads PAST the window to refill the dropped seconds — measured on a
    real export, a 39.1s clip with 3.5s cut still rendered 39.1s and the dead
    air had been replaced by whatever came next."""
    from services.clipper.render import build_render_cmd

    args = dict(fps=30, crf=18, preset="slow")
    whole = build_render_cmd("s.mp4", _cand(100.0, 140.0), {}, None, "o.mp4", **args)
    cut = build_render_cmd("s.mp4", _cand(100.0, 140.0), {}, None, "o.mp4",
                           drop_spans=[(10.0, 14.0)], **args)
    assert float(whole[whole.index("-t") + 1]) == pytest.approx(40.0)
    assert float(cut[cut.index("-t") + 1]) == pytest.approx(36.0)
