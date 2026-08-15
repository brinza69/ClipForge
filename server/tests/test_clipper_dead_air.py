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


def test_overlays_move_with_the_cut():
    overlays = [{"start": 2.0, "end": 4.0, "text": "a"},
                {"start": 20.0, "end": 22.0, "text": "b"}]
    out = dead_air.remap_overlays(overlays, [(10.0, 14.0)])
    assert out[0]["start"] == 2.0 and out[0]["end"] == 4.0
    assert out[1]["start"] == 16.0 and out[1]["end"] == 18.0
    assert out[1]["text"] == "b", "the rest of the overlay must survive"


def test_an_overlay_wholly_inside_removed_time_is_dropped():
    out = dead_air.remap_overlays([{"start": 11.0, "end": 12.0}], [(10.0, 14.0)])
    assert out == []


def test_no_spans_leaves_the_overlays_untouched():
    overlays = [{"start": 2.0, "end": 4.0}]
    assert dead_air.remap_overlays(overlays, []) == overlays


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
    assert "0:a?" in plain, "audio should still be mapped straight from the input"


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
    assert "[acut]" in cmd and "0:a?" not in cmd


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
