"""BURST1r R2 (codex next-17 §2): the render's caption report describes the .ass actually written —
after the remap and the highlight spans — and an interval the .ass clock cannot hold is never handed
to the writer, whose `end <= start -> start + 1 s` fallback would draw it over the next card.

The two counterexamples are next-17's, reproduced through the production path
(`data/claude-master-20260924/next17-check/`).
"""
from __future__ import annotations

from types import SimpleNamespace

import pysubs2

from services.caption_overlays import build_overlays_ass
from services.clipper import caption_display
from services.clipper.captions import MIN_CHUNK_S, build_caption_plan
from workers import clipper_captions


def _settled(*chunks):
    return {"chunks": [dict(c) for c in chunks], "x_pct": 0.5, "y_pct": 0.75,
            "preset_id": "bold_impact", "display": {"rule": caption_display.RULE, "limits": []}}


def _card(text, start, end, **extra):
    return {"text": text, "start": start, "end": end, **extra}


def _burn():
    return {"origin": "built", "outcome": "burn", "reason": None}


def _write(tmp_path, plan, drop=None):
    state = _burn()
    path = clipper_captions._write_ass(SimpleNamespace(id="r2", caption_plan=plan), tmp_path, drop,
                                       None, state)
    ev = [(e.start, e.end, e.plaintext) for e in pysubs2.load(path).events] if path else None
    return path, ev, state


# ── the two counterexamples ─────────────────────────────────────────────────

def test_a_card_in_the_last_three_ms_of_a_10s_clip_is_named_not_drawn(tmp_path):
    # built: settle has no tick for it, so it is `no_time` and its limit names it
    word = [{"word": "last", "start": 9.997, "end": 9.997}]
    out, limits = caption_display.settle([_card("last", 9.997, 10.1, words=word)], 10.0, MIN_CHUNK_S)
    assert out[0]["display"] == "no_time" and limits[0]["unshown"] == [0]
    # the plan BURST1 wrote for it ([9.997, 10.000], `min_duration`) — the ASS had it at [10.00, 10.00]
    path, _ev, state = _write(tmp_path, _settled(_card("last", 9.997, 10.0, display="min_duration")))
    assert path is None
    assert (state["outcome"], state["reason"]) == ("unavailable", "no_card_representable")
    assert state["display"]["unshown_cards"] == [{"text": "last", "start": 9.997, "end": 10.0,
                                                  "why": "under_one_tick"}]


def test_a_1ms_remainder_is_not_revived_as_a_second_over_the_next_card(tmp_path):
    plan = _settled(_card("one", 1.0, 1.12), _card("next", 1.5, 2.0))
    path, ev, state = _write(tmp_path, plan, [(1.001, 1.12)])
    assert ev == [(1380, 1880, "NEXT")]                  # was ONE [1000, 2000] over NEXT: 500 ms
    assert state["outcome"] == "burn"
    assert state["display"] == {"overlapping_pairs": 0, "empty_events": 0, "short_cards": 0,
                                "short_intervals": [],
                                "unshown_cards": [{"text": "one", "start": 1.0, "end": 1.001,
                                                   "why": "under_one_tick"}],
                                "ass_agrees": True, "plan_settled": True, "plan_limits": 0}


def test_a_stored_plan_gets_no_fallback_second_either(tmp_path):
    plan = {k: v for k, v in _settled(_card("one", 1.0, 1.12), _card("next", 1.5, 2.0)).items()
            if k != "display"}
    _path, ev, state = _write(tmp_path, plan, [(1.001, 1.12)])
    assert ev == [(1380, 1880, "NEXT")]
    assert state["display"]["unshown_cards"][0]["text"] == "one"
    assert state["display"]["plan_settled"] is False


# ── all / some cards unrepresentable ────────────────────────────────────────

def test_all_cards_unrepresentable_writes_nothing_and_says_so(tmp_path):
    plan = _settled(_card("one", 1.0, 1.12), _card("two", 1.2, 1.32))
    path, _ev, state = _write(tmp_path, plan, [(1.001, 1.12), (1.203, 1.32)])
    assert path is None and not list(tmp_path.glob("*.ass"))
    assert [c["text"] for c in state["display"]["unshown_cards"]] == ["one", "two"]
    assert state["reason"] == "no_card_representable"
    warned = clipper_captions._caption_warnings([], state)
    assert len(warned) == 1 and "no caption card kept any display time" in warned[0]


def test_some_cards_unrepresentable_the_rest_drawn_in_order(tmp_path):
    plan = _settled(_card("a", 0.0, 0.5), _card("b", 0.5, 0.62), _card("c", 1.0, 1.5))
    _path, ev, state = _write(tmp_path, plan, [(0.503, 0.62)])
    assert [e[2] for e in ev] == ["A", "C"]
    assert [c["text"] for c in state["display"]["unshown_cards"]] == ["b"]
    assert state["display"]["ass_agrees"] is True and state["outcome"] == "burn"


# ── the export's final limit ────────────────────────────────────────────────

def test_nothing_passes_the_exports_end_after_the_remap(tmp_path):
    # a 10 s window whose last second is cut: the export ends at 9.000. The card at 8.997 keeps 3 ms;
    # the writer's fallback would have drawn it [9.00, 10.00] — past the end of the export.
    plan = _settled(_card("a", 8.0, 8.9), _card("b", 8.997, 9.6), _card("c", 9.7, 9.9))
    _path, ev, state = _write(tmp_path, plan, [(9.0, 10.0)])
    assert ev == [(8000, 8900, "A")]
    assert max(e[1] for e in ev) <= 9000
    assert [c["text"] for c in state["display"]["unshown_cards"]] == ["b"]   # c: dead air (K2)


# ── what the report reads: the file ─────────────────────────────────────────

def test_a_highlight_span_with_no_length_is_counted(tmp_path):
    # the last word starts 3 ms before the card ends: its span is [1.197, 1.200] -> [1.20, 1.20]
    words = [{"word": "a", "start": 1.0, "end": 1.1}, {"word": "b", "start": 1.197, "end": 1.2}]
    plan = _settled(_card("a b", 1.0, 1.2, words=words, display="min_duration"))
    _path, ev, state = _write(tmp_path, plan)
    assert [(e[0], e[1]) for e in ev] == [(1000, 1200), (1200, 1200)]
    assert state["display"] == {"overlapping_pairs": 0, "empty_events": 1, "short_cards": 0,
                                "short_intervals": [], "unshown_cards": [], "ass_agrees": True,
                                "plan_settled": True, "plan_limits": 0}


def test_an_event_with_no_length_is_not_a_pair():
    report = caption_display.effective_report([(0, 500, "p"), (100, 100, "p")], [], [], {}, MIN_CHUNK_S)
    assert report["overlapping_pairs"] == 0 and report["empty_events"] == 1


def test_a_no_time_label_on_a_stored_plan_is_not_a_missing_card(tmp_path):
    # the label means "not drawn" only on a settled plan; a stored plan's card is drawn as it always was
    stored = {k: v for k, v in _settled(_card("kept", 1.0, 1.5, display="no_time")).items()
              if k != "display"}
    _path, ev, state = _write(tmp_path, stored)
    assert [e[2] for e in ev] == ["KEPT"] and state["display"] == {}
    settled = _settled(_card("a", 1.0, 1.5), _card(" ", 2.0, 2.0, display="no_time"))
    _path, ev, state = _write(tmp_path, settled)
    assert [e[2] for e in ev] == ["A"] and state["display"] == {}        # no text: not a card


def test_a_card_starting_after_the_clip_has_no_time_and_moves_nothing():
    out, limits = caption_display.settle([_card("in", 9.0, 9.5), _card("after", 10.5, 10.62)],
                                         10.0, MIN_CHUNK_S)
    assert (out[0]["start"], out[0]["end"]) == (9.0, 9.5)
    assert out[1]["display"] == "no_time" and out[1]["start"] == out[1]["end"] == 10.5
    assert limits[-1]["unshown"] == [1] and limits[-1]["available_s"] == 0


def test_a_file_that_is_not_the_cards_does_not_agree(tmp_path):
    ov = {"text": "a", "start_t": 0.0, "end_t": 0.5, "x_pct": 0.5, "y_pct": 0.75}
    path = build_overlays_ass([ov], 1080, 1920, str(tmp_path / "x.ass"))
    events = caption_display.ass_events(path)
    other = {**ov, "start_t": 1.0, "end_t": 1.5}
    report = caption_display.effective_report(events, [ov, other], [], {}, MIN_CHUNK_S)
    assert report["ass_agrees"] is False
    assert caption_display.effective_report(events, [ov], [], {}, MIN_CHUNK_S) == {}


def test_agreement_needs_every_card_whole_and_nothing_else():
    agrees = caption_display._agrees
    assert agrees([(0, 50, "p"), (50, 100, "p")], [(0, 100)])
    assert not agrees([(0, 50, "p"), (60, 100, "p")], [(0, 100)])        # a gap
    assert not agrees([(0, 100, "p"), (100, 200, "p")], [(0, 100)])      # an extra event
    assert not agrees([(0, 120, "p")], [(0, 100)])                       # past the card
    assert not agrees([(10, 100, "p")], [(0, 100)])                      # late start
    assert agrees([(0, 100, "p"), (100, 100, "p")], [(0, 100)])          # empty at the boundary
    assert not agrees([], [(0, 100)])


# ── controls ────────────────────────────────────────────────────────────────

def test_a_well_timed_built_plan_reports_nothing(tmp_path):
    words = [{"word": w, "start": 1.0 + 0.4 * i, "end": 1.3 + 0.4 * i}
             for i, w in enumerate("one two three four five six".split())]
    plan = build_caption_plan({"start": 0.0, "end": 5.0}, {"segments": [
        {"start": 1.0, "end": 3.5, "text": "", "words": words}]},
        preset_id="bold_impact", max_words=3, position="bottom", layout={})
    path, ev, state = _write(tmp_path, plan)
    assert path and state == {**_burn(), "display": {}}
    assert ev[0][0] == 1000 and ev[-1][1] == 3300


def test_other_callers_keep_the_writers_fallback(tmp_path):
    # remix / captions router / tiktok call the writer directly: unchanged, an end at the start still
    # becomes start + 1 s there. Only the clipper path holds such an interval back.
    ov = {"text": "x", "start_t": 2.0, "end_t": 2.0, "x_pct": 0.5, "y_pct": 0.5}
    path = build_overlays_ass([ov], 1080, 1920, str(tmp_path / "o.ass"))
    assert [(e.start, e.end) for e in pysubs2.load(path).events] == [(2000, 3000)]
