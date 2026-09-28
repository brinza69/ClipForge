"""BURST1: the per-word highlight spans of one card TILE its interval — a span held to
MIN_HIGHLIGHT_MS moves the next one, it never draws a second copy of the card over it.
Measured: 276 of the 878 overlapping pairs on the burned exports were spans of one card (60 ms)."""
from __future__ import annotations

import re

import pysubs2

from services.caption_overlays import MIN_HIGHLIGHT_MS, _word_highlight_spans, build_overlays_ass


def _spans(words, start_ms, end_ms):
    text = " ".join(w["word"] for w in words)
    return _word_highlight_spans(text, {"words": words}, {}, start_ms, end_ms)


def _tiles(spans, start_ms, end_ms):
    return (spans[0][0] == start_ms and spans[-1][1] == end_ms
            and all(b[0] == a[1] for a, b in zip(spans, spans[1:])))


def test_close_words_are_pushed_not_stacked():
    # THAT'S A GOOD: 28.01 / 28.05 / 28.10 -> the old spans [28.01,28.07] and [28.05, ...] overlapped
    words = [{"word": "THAT'S", "start": 28.01}, {"word": "A", "start": 28.05},
             {"word": "GOOD", "start": 28.10}]
    spans = _spans(words, 28010, 28500)
    assert [(s, e) for s, e, _ in spans] == [(28010, 28070), (28070, 28130), (28130, 28500)]
    assert _tiles(spans, 28010, 28500)


def test_a_burst_of_point_words_tiles_and_drops_only_the_highlight_that_has_no_time():
    words = [{"word": w, "start": 2.92} for w in ("HOW", "CAN", "I")]
    spans = _spans(words, 2920, 3040)
    assert [(s, e) for s, e, _ in spans] == [(2920, 2980), (2980, 3040)]
    assert all(e - s >= MIN_HIGHLIGHT_MS for s, e, _ in spans)
    assert all(re.sub(r"\{[^}]*\}", "", t) == "HOW CAN I" for _s, _e, t in spans)   # whole card


def test_spaced_words_are_drawn_as_before():
    words = [{"word": "IF", "start": 0.0}, {"word": "I", "start": 0.3}, {"word": "WANTED", "start": 0.6}]
    assert [(s, e) for s, e, _ in _spans(words, 0, 1000)] == [(0, 300), (300, 600), (600, 1000)]


def test_the_written_file_has_no_overlapping_spans(tmp_path):
    ov = {"text": "THAT'S A GOOD", "start_t": 28.01, "end_t": 28.5, "x_pct": 0.5, "y_pct": 0.75,
          "words": [{"word": "THAT'S", "start": 28.01}, {"word": "A", "start": 28.05},
                    {"word": "GOOD", "start": 28.1}]}
    path = build_overlays_ass([ov], 1080, 1920, str(tmp_path / "t.ass"))
    ev = sorted((e.start, e.end) for e in pysubs2.load(path).events)
    assert len(ev) == 3 and all(b[0] >= a[1] for a, b in zip(ev, ev[1:]))
