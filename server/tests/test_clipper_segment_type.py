"""One content type per STRETCH, not one per file.

Measured on slice4h00test against labels a person wrote by eye: the whole-file
answer (`talking_head`, 0.426) was right for 0 of 12 stretches; asking the same
classifier per stretch was right for 9. Nothing about HOW a type is decided
changed — only how often it is asked.

The slicing is the part worth testing. A stretch has to be handed signals that
look like a source of exactly that length, or every consumer that divides by a
duration or indexes a hop-series reads the whole file's numbers.
"""

from __future__ import annotations

import pytest

from services.clipper import segment_type


def _signals():
    return {
        "duration": 600.0,
        "motion": {"hop_s": 1.0, "motion": [float(i) for i in range(600)]},
        "speech": [[0.0, 100.0], [300.0, 400.0]],
        "silence": [[100.0, 300.0]],
        "faces": [{"t": float(t), "boxes": [[1, 2, 3, 4]]} for t in range(0, 600, 60)],
        "proxy_width": 480,
        "proxy_height": 270,
    }


# ── slicing ──────────────────────────────────────────────────────────────────


def test_a_slice_reports_its_own_length():
    out = segment_type.slice_signals(_signals(), 200.0, 400.0)
    assert out["duration"] == 200.0


def test_a_hop_series_is_cut_to_the_range():
    out = segment_type.slice_signals(_signals(), 100.0, 200.0)
    values = out["motion"]["motion"]
    assert values[0] == pytest.approx(100.0)
    assert 100 <= len(values) <= 102
    assert out["motion"]["hop_s"] == 1.0, "the hop must survive the cut"


def test_spans_are_clipped_and_rebased_to_zero():
    """Every consumer treats these as offsets from the start of what it was
    handed, so a span left on the source clock reads as being hours in."""
    out = segment_type.slice_signals(_signals(), 50.0, 350.0)
    assert out["speech"] == [[0.0, 50.0], [250.0, 300.0]]
    assert out["silence"] == [[50.0, 250.0]]


def test_faces_outside_the_range_are_dropped():
    """Samples at 120s and 180s survive, rebased to 20 and 80."""
    out = segment_type.slice_signals(_signals(), 100.0, 200.0)
    assert [f["t"] for f in out["faces"]] == [20.0, 80.0]


def test_slicing_a_signals_blob_with_nothing_in_it_is_not_an_error():
    out = segment_type.slice_signals({}, 0.0, 10.0)
    assert out["duration"] == 10.0 and out["faces"] == []


def test_speech_ratio_reads_the_slice_not_the_file():
    """The check that matters: the existing helpers must work unchanged on a
    slice, because re-implementing them per stretch is how the two answers
    would drift apart."""
    from services.clipper.content_geom import speech_ratio

    whole = speech_ratio(_signals())
    quiet = speech_ratio(segment_type.slice_signals(_signals(), 100.0, 300.0))
    loud = speech_ratio(segment_type.slice_signals(_signals(), 0.0, 100.0))
    assert quiet == pytest.approx(0.0)
    assert loud > whole > quiet


# ── the ranges ───────────────────────────────────────────────────────────────


def test_a_short_source_is_one_stretch():
    assert segment_type.clock_ranges(300.0) == [(0.0, 300.0)]


def test_a_long_source_is_cut_on_the_clock():
    out = segment_type.clock_ranges(3600.0, step=1200.0)
    assert out == [(0.0, 1200.0), (1200.0, 2400.0), (2400.0, 3600.0)]


def test_no_duration_means_no_stretches():
    assert segment_type.clock_ranges(0.0) == []


# ── looking a candidate up ───────────────────────────────────────────────────


def test_a_candidate_gets_the_type_of_the_stretch_it_sits_in():
    segs = [{"start": 0.0, "end": 600.0, "content_type": "irl"},
            {"start": 600.0, "end": 1200.0, "content_type": "gaming"}]
    assert segment_type.type_at(segs, 300.0, "unknown") == "irl"
    assert segment_type.type_at(segs, 900.0, "unknown") == "gaming"


def test_a_candidate_outside_every_stretch_keeps_the_whole_file_answer():
    """A stretch can be too short to classify, and a candidate in one of those
    is better scored by the source's overall profile than by nothing."""
    segs = [{"start": 0.0, "end": 600.0, "content_type": "irl"}]
    assert segment_type.type_at(segs, 5000.0, "gaming") == "gaming"
    assert segment_type.type_at([], 5.0, "podcast") == "podcast"


def test_a_stretch_too_short_to_classify_is_skipped():
    out = segment_type.classify_ranges(["a.jpg"], [1.0], {}, {}, [(0.0, 30.0)])
    assert out == []
