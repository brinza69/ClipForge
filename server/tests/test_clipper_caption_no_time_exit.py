"""BURST1r2 (codex next-20 §1): text whose every card `settle` gave no display time is not "no text".

`caption_plan_to_overlays` skips a settled plan's `no_time` cards, so when all of them are `no_time`
it returns `[]`, and `_write_ass` used to leave at `if not overlays` as `empty / no_caption_text` —
before the report, with no card named (next20-check/all_no_time.json). Every case goes through the
production `_write_ass`, with plans the builder produced, and checks the whole state and the warning.
"""
from __future__ import annotations

import copy
from types import SimpleNamespace

from services.clipper import caption_display
from services.clipper.captions import MIN_CHUNK_S, build_caption_plan
from workers import clipper_captions

WARN = "no caption card kept any display time"


def _built(words, start=100.0, end=110.0):
    return build_caption_plan({"start": start, "end": end},
                              {"segments": [{"start": start, "end": end, "text": "", "words": words}]},
                              preset_id="bold_impact", max_words=3, position="bottom", layout={})


def _write(tmp_path, plan, drop=None):
    frozen = copy.deepcopy(plan)
    state = {"origin": "built", "outcome": "burn", "reason": None}
    path = clipper_captions._write_ass(SimpleNamespace(id="nt", caption_plan=plan), tmp_path, drop,
                                       None, state)
    assert plan == frozen                                  # the plan is never modified
    return path, state


def _report(unshown, limits):
    return {"overlapping_pairs": 0, "empty_events": 0, "short_cards": 0, "short_intervals": [],
            "unshown_cards": unshown, "ass_agrees": True, "plan_settled": True,
            "plan_limits": limits}


def test_one_card_the_builder_left_no_time(tmp_path):
    # codex's probe: one word 3 ms before the end of a 10 s clip
    plan = _built([{"word": "LAST", "start": 109.997, "end": 109.997}])
    assert [c["display"] for c in plan["chunks"]] == ["no_time"]
    path, state = _write(tmp_path, plan)
    assert path is None and not list(tmp_path.glob("*.ass"))
    assert state == {"origin": "built", "outcome": "unavailable", "reason": "no_card_representable",
                     "display": _report([{"text": "LAST", "start": 10.0, "end": 10.0,
                                          "why": "no_time"}], 1)}
    warned = clipper_captions._caption_warnings(["Layout: kept"], state)
    assert warned[0] == "Layout: kept" and len(warned) == 2
    assert "(no_card_representable)" in warned[1] and WARN in warned[1]


def test_codex_settled_card_exactly(tmp_path):
    # next20-check/all_no_time.json: settle() directly, then the writer
    chunks, limits = caption_display.settle([{"text": "LAST", "start": 9.997, "end": 10.117}],
                                            10.0, MIN_CHUNK_S)
    plan = {"chunks": chunks, "preset_id": "bold_impact",
            "display": {"rule": caption_display.RULE, "limits": limits}}
    path, state = _write(tmp_path, plan)
    assert path is None
    assert (state["outcome"], state["reason"]) == ("unavailable", "no_card_representable")
    assert state["display"]["unshown_cards"] == [{"text": "LAST", "start": 10.0, "end": 10.0,
                                                  "why": "no_time"}]


def test_several_cards_the_builder_left_no_time(tmp_path):
    words = [{"word": f"w{i}", "start": 109.990 + 0.001 * i, "end": 109.990 + 0.001 * i}
             for i in range(6)]
    plan = _built(words)
    assert [c["display"] for c in plan["chunks"]] == ["no_time", "no_time"]
    path, state = _write(tmp_path, plan)
    assert path is None
    assert state == {"origin": "built", "outcome": "unavailable", "reason": "no_card_representable",
                     "display": _report([{"text": "w0 w1 w2", "start": 10.0, "end": 10.0,
                                          "why": "no_time"},
                                         {"text": "w3 w4 w5", "start": 10.0, "end": 10.0,
                                          "why": "no_time"}], 1)}
    assert WARN in clipper_captions._caption_warnings([], state)[0]


def test_empty_text_chunks_beside_no_time_cards(tmp_path):
    plan = _built([{"word": "LAST", "start": 109.997, "end": 109.997}])
    plan["chunks"] = ([{"text": " ", "start": 1.0, "end": 2.0},
                       {"text": "", "start": 3.0, "end": 3.0, "display": "no_time"}]
                      + plan["chunks"])
    path, state = _write(tmp_path, plan)
    assert path is None
    assert state["reason"] == "no_card_representable"
    # only the card with text is named; an empty chunk is not a card
    assert state["display"] == _report([{"text": "LAST", "start": 10.0, "end": 10.0,
                                         "why": "no_time"}], 1)
    assert WARN in clipper_captions._caption_warnings([], state)[0]


def test_control_a_plan_with_no_text_is_still_no_caption_text(tmp_path):
    plan = {"chunks": [{"text": " ", "start": 1.0, "end": 2.0},
                       {"text": "", "start": 3.0, "end": 3.0, "display": "no_time"}],
            "preset_id": "bold_impact", "display": {"rule": caption_display.RULE, "limits": []}}
    path, state = _write(tmp_path, plan)
    assert path is None
    assert state == {"origin": "built", "outcome": "empty", "reason": "no_caption_text"}
    assert clipper_captions._caption_warnings([], state) == []   # "empty" is an answer, no warning
    # a person blanked every word in the editor; the plan's old limit is still on it
    blanked = _built([{"word": "LAST", "start": 109.997, "end": 109.997}])
    blanked["chunks"][0]["text"] = ""
    path, state = _write(tmp_path, blanked)
    assert state == {"origin": "built", "outcome": "empty", "reason": "no_caption_text"}


def test_control_every_event_removed_by_the_remap_keeps_k2(tmp_path):
    plan = _built([{"word": w, "start": 101.0 + 0.4 * i, "end": 101.3 + 0.4 * i}
                   for i, w in enumerate("one two three".split())])
    assert all("display" not in c or c["display"] != "no_time" for c in plan["chunks"])
    path, state = _write(tmp_path, plan, [(0.0, 5.0)])
    assert path is None
    assert state == {"origin": "built", "outcome": "empty_after_remap",
                     "reason": "all_captions_in_removed_time"}
    warned = clipper_captions._caption_warnings([], state)
    assert len(warned) == 1 and "empty_after_remap" in warned[0] and WARN not in warned[0]
