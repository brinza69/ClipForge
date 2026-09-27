"""BURST1 (closure-3 §1 B): caption cards on one anchor never overlap, display time is declared as
display, a burst with no room for a legible card is a reported limit, and a stored plan is reported,
never re-timed.

The shapes are the measured ones (B/BURST1-result.md §2): a Whisper burst of point words (start == end)
that stacked five cards on [s, s + 0.12], and the display minimum pushing an end past the next start.
"""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pysubs2

from services.caption_overlays import build_overlays_ass
from services.clipper import caption_display
from services.clipper.captions import (MIN_CHUNK_S, build_caption_plan,
                                       caption_plan_to_overlays)
from workers import clipper_captions


def _chunk(text, start, end, words=None):
    toks = text.split()
    return {"text": text, "start": start, "end": end,
            "words": words if words is not None else
            [{"word": t, "start": start, "end": start} for t in toks]}


def _intervals(chunks):
    return [(c["start"], c["end"]) for c in chunks]


def _no_overlap(intervals):
    return all(b[0] >= a[1] for a, b in zip(intervals, intervals[1:]))


# ── the rule ────────────────────────────────────────────────────────────────

def test_a_point_word_stack_is_spread_in_order_and_reported():
    # 1da8b05cacf7's shape: four cards of point words at 28.95, then a timed card at the same instant.
    stack = [_chunk("to be focused.", 28.95, 29.07), _chunk("You already know", 28.95, 29.07),
             _chunk("that you need", 28.95, 29.07), _chunk("to be focused.", 28.95, 29.07),
             _chunk("So what's stopping", 28.95, 29.41,
                    [{"word": "So", "start": 28.95, "end": 29.01},
                     {"word": "what's", "start": 29.01, "end": 29.17},
                     {"word": "stopping", "start": 29.17, "end": 29.41}]),
             _chunk("us from being", 29.41, 30.09,
                    [{"word": "us", "start": 29.41, "end": 29.65},
                     {"word": "from", "start": 29.65, "end": 29.83},
                     {"word": "being", "start": 29.83, "end": 30.09}])]
    out, limits = caption_display.settle(stack, 37.39, MIN_CHUNK_S)

    assert [c["text"] for c in out] == [c["text"] for c in stack]
    assert _no_overlap(_intervals(out))
    assert out[0]["start"] == 28.95 and out[4]["end"] == 29.41       # never past the next card
    assert out[5]["start"] == 29.41 and "display" not in out[5]       # a card with room is untouched
    assert {c["display"] for c in out[:5]} == {"redistributed"}
    assert [(round(c["end"] - c["start"], 3)) for c in out[:5]] == [0.092] * 5
    assert limits == [{"chunks": [0, 4], "start": 28.95, "end": 29.41, "available_s": 0.46,
                       "needed_s": 0.6, "unshown": []}]
    # the measured word clock is left as it was
    assert [c["words"] for c in out] == [c["words"] for c in stack]


def test_the_display_minimum_no_longer_pushes_an_end_past_the_next_start():
    # "MEAN?" measured 4.45-4.47, "LOOK TO MY" starts 4.55: old ends 4.57 > 4.55.
    chunks = [_chunk("MEAN?", 4.45, 4.57, [{"word": "MEAN?", "start": 4.45, "end": 4.47}]),
              _chunk("LOOK TO MY", 4.55, 4.9,
                     [{"word": "LOOK", "start": 4.55, "end": 4.7}, {"word": "TO", "start": 4.7, "end": 4.8},
                      {"word": "MY", "start": 4.8, "end": 4.9}])]
    out, limits = caption_display.settle(chunks, 10.0, MIN_CHUNK_S)
    assert _intervals(out) == [(4.45, 4.675), (4.675, 4.9)]
    assert limits == []                                   # 0.225 s each: room for both
    assert [c["display"] for c in out] == ["redistributed", "redistributed"]


def test_a_card_with_room_keeps_its_measured_end_or_says_it_was_held():
    measured = _chunk("a b", 1.0, 1.5, [{"word": "a", "start": 1.0, "end": 1.2},
                                        {"word": "b", "start": 1.2, "end": 1.5}])
    held = _chunk("c", 3.0, 3.12, [{"word": "c", "start": 3.0, "end": 3.05}])
    out, limits = caption_display.settle([measured, held], 10.0, MIN_CHUNK_S)
    assert _intervals(out) == [(1.0, 1.5), (3.0, 3.12)] and limits == []
    assert "display" not in out[0]
    assert out[1]["display"] == "min_duration"


def test_an_end_is_clamped_to_the_clip_and_the_shortfall_reported():
    out, limits = caption_display.settle(
        [_chunk("last", 9.95, 10.07, [{"word": "last", "start": 9.95, "end": 9.95}])], 10.0, MIN_CHUNK_S)
    assert out[0]["end"] == 10.0
    assert limits and limits[0]["available_s"] == 0.05 and limits[0]["unshown"] == []


def test_a_card_the_clip_ends_before_is_no_time_listed_and_not_drawn():
    chunks = [_chunk("in time", 9.0, 9.5), _chunk("too late", 10.0, 10.12)]
    out, limits = caption_display.settle(chunks, 10.0, MIN_CHUNK_S)
    assert out[1]["display"] == "no_time"
    assert limits[-1]["chunks"] == [1, 1] and limits[-1]["unshown"] == [1]
    plan = {"chunks": out, "display": {"rule": caption_display.RULE, "limits": limits}}
    assert [o["text"] for o in caption_plan_to_overlays(plan)] == ["in time"]


def test_the_next_run_starts_where_it_was_measured_and_the_cards_without_room_are_named():
    # codex next-17 R1: 15 point cards at 5.000, the next card measured at 5.120. The 11 ms floor used
    # to stretch the burst to 5.165 and start the next card there.
    burst = [_chunk(f"w{i}", 5.0, 5.12) for i in range(15)]
    after = _chunk("next", 5.12, 5.6, [{"word": "next", "start": 5.12, "end": 5.6}])
    out, limits = caption_display.settle(burst + [after], 10.0, MIN_CHUNK_S)
    assert (out[-1]["start"], out[-1]["end"]) == (5.12, 5.6)
    assert _no_overlap(_intervals(out)) and max(c["end"] for c in out[:15]) == 5.12
    shown = [c for c in out[:15] if c["display"] != "no_time"]
    assert [c["text"] for c in shown] == [f"w{i}" for i in range(10)]    # 120 ms // 11 ms, in order
    assert all(round((c["end"] - c["start"]) * 1000) >= 11 for c in shown)
    assert [c["display"] for c in out[10:15]] == ["no_time"] * 5
    assert {(c["start"], c["end"]) for c in out[10:15]} == {(5.12, 5.12)}      # at the window's end
    assert limits == [{"chunks": [0, 14], "start": 5.0, "end": 5.12, "available_s": 0.12,
                       "needed_s": 1.8, "unshown": [10, 11, 12, 13, 14]}]


def test_the_cap_holds_on_the_file_clock(tmp_path):
    # after quantisation too: no burst event ends past the next card's start in the written file
    burst = [_chunk(f"w{i}", 5.0, 5.12) for i in range(15)]
    after = _chunk("next", 5.12, 5.6, [{"word": "next", "start": 5.12, "end": 5.6}])
    out, limits = caption_display.settle(burst + [after], 10.0, MIN_CHUNK_S)
    plan = {"chunks": out, "display": {"rule": caption_display.RULE, "limits": limits}}
    state = {"origin": "built", "outcome": "burn", "reason": None}
    path = clipper_captions._write_ass(SimpleNamespace(id="floor", caption_plan=plan), tmp_path, None,
                                       None, state)
    ev = [(e.start, e.end, e.plaintext) for e in pysubs2.load(path).events]
    assert [e[2] for e in ev] == [f"W{i}" for i in range(10)] + ["NEXT"]  # the preset upper-cases
    assert all(e[1] > e[0] for e in ev) and all(b[0] >= a[1] for a, b in zip(ev, ev[1:]))
    assert ev[-1][:2] == (5120, 5600)
    assert [c["text"] for c in state["display"]["unshown_cards"]] == [f"w{i}" for i in range(10, 15)]
    assert state["display"]["ass_agrees"] is True and state["outcome"] == "burn"


def test_a_float_hair_past_the_next_start_is_not_a_pair(tmp_path):
    # a legacy end is start + 0.12 in floats; where that is 1e-15 past the next start, the .ass has
    # them back to back, so the report must not count a pair there
    a = next(s / 100 for s in range(100, 5000) if s / 100 + MIN_CHUNK_S > round(s / 100 + MIN_CHUNK_S, 2))
    plan = {"chunks": [_chunk("one", a, a), _chunk("two", round(a + MIN_CHUNK_S, 2), a + 1.0)]}
    ovs = caption_plan_to_overlays(plan)
    assert ovs[0]["end_t"] > ovs[1]["start_t"]                     # the float says "overlap"
    state = {"origin": "stored", "outcome": "burn", "reason": None}
    clipper_captions._write_ass(SimpleNamespace(id="hair", caption_plan=plan), tmp_path, None, None, state)
    assert state["display"] == {}                                   # measured: nothing to report


# ── the builder, the overlays, the writer: the production path ──────────────

TRANSCRIPT = {"segments": [{"start": 28.4, "end": 31.0, "text": "", "words": [
    {"word": "So", "start": 28.65, "end": 28.95}, {"word": "you", "start": 28.95, "end": 28.95},
    {"word": "need", "start": 28.95, "end": 28.95}, {"word": "to", "start": 28.95, "end": 28.95},
    {"word": "be", "start": 28.95, "end": 28.95}, {"word": "focused.", "start": 28.95, "end": 28.95},
    {"word": "You", "start": 28.95, "end": 28.95}, {"word": "already", "start": 28.95, "end": 28.95},
    {"word": "know", "start": 28.95, "end": 28.95}, {"word": "that", "start": 28.95, "end": 28.95},
    {"word": "you", "start": 28.95, "end": 28.95}, {"word": "need", "start": 28.95, "end": 28.95},
    {"word": "to", "start": 28.95, "end": 28.95}, {"word": "be", "start": 28.95, "end": 28.95},
    {"word": "focused.", "start": 28.95, "end": 28.95}, {"word": "So", "start": 28.95, "end": 29.01},
    {"word": "what's", "start": 29.01, "end": 29.17}, {"word": "stopping", "start": 29.17, "end": 29.41},
    {"word": "us", "start": 29.41, "end": 29.65}, {"word": "from", "start": 29.65, "end": 29.83},
    {"word": "being", "start": 29.83, "end": 30.09}]}]}


def _plan():
    return build_caption_plan({"start": 28.37, "end": 65.76}, TRANSCRIPT, preset_id="bold_impact",
                              max_words=3, position="bottom", layout={})


def test_the_builder_declares_display_and_keeps_words_and_order():
    plan = _plan()
    assert plan["display"]["rule"] == caption_display.RULE
    assert plan["display"]["min_chunk_s"] == MIN_CHUNK_S
    assert _no_overlap(_intervals(plan["chunks"]))
    words = [w["word"] for c in plan["chunks"] for w in c["words"]]
    assert words == [w["word"] for w in TRANSCRIPT["segments"][0]["words"]]
    assert " ".join(c["text"].replace("\n", " ") for c in plan["chunks"]).split() == words
    # collapsed_run.txt: five cards at 28.95 before "us from being" at 29.41 — no room for five
    # (the plan's clock is the clip's: 28.95 - 28.37 = 0.58)
    assert plan["display"]["limits"] == [{"chunks": [1, 5], "start": 0.58, "end": 1.04,
                                          "available_s": 0.46, "needed_s": 0.6, "unshown": []}]


def _ass_events(path):
    return sorted((e.start, e.end, e.text) for e in pysubs2.load(path).events)


def test_the_written_ass_has_no_two_cards_on_the_anchor_at_once(tmp_path):
    state = {"origin": "built", "outcome": "burn", "reason": None}
    path = clipper_captions._write_ass(SimpleNamespace(id="burst", caption_plan=_plan()),
                                       tmp_path, None, None, state)
    ev = _ass_events(path)
    assert len({e[2].split("}")[0] for e in ev}) == 1                  # one anchor
    assert all(b[0] >= a[1] for a, b in zip(ev, ev[1:]))
    # the five short cards, by name, on the export clock: they tile the limit's window [0.58, 1.04]
    short = [("to be focused.", 0.58, 0.672), ("You already know", 0.672, 0.764),
             ("that you need", 0.764, 0.856), ("to be focused.", 0.856, 0.948),
             ("So what's stopping", 0.948, 1.04)]
    assert state["display"] == {"overlapping_pairs": 0, "empty_events": 0, "short_cards": 5,
                                "short_intervals": [{"text": t, "start": s, "end": e}
                                                    for t, s, e in short],
                                "unshown_cards": [], "ass_agrees": True, "plan_settled": True,
                                "plan_limits": 1}


def test_a_settled_plan_is_drawn_as_settled():
    plan = _plan()
    ovs = caption_plan_to_overlays(plan)
    assert [(o["start_t"], o["end_t"]) for o in ovs] == _intervals(plan["chunks"])
    for o, c in zip(ovs, plan["chunks"]):
        assert ("words" in o) is (c.get("display") != "redistributed")


def test_a_settled_card_edited_to_nothing_still_gets_the_minimum():
    plan = {"chunks": [{"text": "x", "start": 1.0, "end": 1.0}],
            "display": {"rule": caption_display.RULE, "limits": []}}
    assert caption_plan_to_overlays(plan)[0]["end_t"] == 1.0 + MIN_CHUNK_S


def test_a_stored_plan_is_not_retimed_only_reported(tmp_path):
    # a legacy / person's plan with the old stack: rendered with its own times, reported
    stored = {"chunks": [_chunk("one", 2.0, 2.12), _chunk("two", 2.0, 2.12), _chunk("three", 2.05, 2.1)],
              "x_pct": 0.5, "y_pct": 0.75, "preset_id": "bold_impact"}
    frozen = copy.deepcopy(stored)
    ovs = caption_plan_to_overlays(stored)
    assert [(o["start_t"], o["end_t"]) for o in ovs] == [(2.0, 2.12), (2.0, 2.12), (2.05, 2.17)]
    state = {"origin": "stored", "outcome": "burn", "reason": None}
    clipper_captions._write_ass(SimpleNamespace(id="stored", caption_plan=stored), tmp_path, None,
                                None, state)
    assert stored == frozen
    assert state["display"] == {"overlapping_pairs": 3, "empty_events": 0, "short_cards": 0,
                                "short_intervals": [], "unshown_cards": [], "ass_agrees": True,
                                "plan_settled": False,
                                "plan_limits": 0}


def test_a_clean_stored_plan_keeps_its_outcome_and_reports_nothing(tmp_path):
    stored = {"chunks": [_chunk("one", 0.0, 0.5), _chunk("two", 0.5, 1.0)], "preset_id": "bold_impact"}
    state = {"origin": "stored", "outcome": "burn", "reason": None}
    clipper_captions._write_ass(SimpleNamespace(id="clean", caption_plan=stored), tmp_path, None,
                                None, state)
    assert state == {"origin": "stored", "outcome": "burn", "reason": None, "display": {}}


def _report(tmp_path, ovs):
    path = build_overlays_ass(ovs, 1080, 1920, str(tmp_path / "r.ass"))
    return caption_display.effective_report(caption_display.ass_events(path), ovs, [], {}, MIN_CHUNK_S)


def test_the_report_counts_per_anchor(tmp_path):
    ovs = [{"text": "a", "start_t": 0.0, "end_t": 1.0, "x_pct": 0.5, "y_pct": 0.75},
           {"text": "b", "start_t": 0.5, "end_t": 1.5, "x_pct": 0.5, "y_pct": 0.3}]  # other anchor
    assert _report(tmp_path, ovs) == {}
    short = [{"text": "a", "start_t": 0.0, "end_t": 0.05, "x_pct": 0.5, "y_pct": 0.75}]
    assert _report(tmp_path, short)["short_cards"] == 1
