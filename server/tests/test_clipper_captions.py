"""
Tests for the AI Stream Clipper caption plan + headline (pure, no media).

Everything here runs without ffmpeg, a transcript model or a running server:
the judgement calls live in the timing re-base, the keep-out nudge and the
overlay shape, and all three are pure functions over dicts.
"""

import pytest

from services import caption_overlays
from services.captioner_presets import SAFE_CAPTION_BOTTOM, SAFE_TOP
from services.clipper import captions, headline


def _transcript(words):
    return {"segments": [{"start": words[0][1], "end": words[-1][2],
                          "text": " ".join(w[0] for w in words),
                          "words": [{"word": w, "start": s, "end": e}
                                    for w, s, e in words]}]}


def _cand(start, end, text=""):
    return {"id": "c1", "start": start, "end": end, "text": text}


# --------------------------------------------------------------------------
# Chunk timing
# --------------------------------------------------------------------------

def test_chunk_times_are_clip_relative_and_non_negative():
    # Candidate starts at 120s into the source; captions must start near zero.
    words = [(f"w{i}.", 120.0 + i * 0.5, 120.4 + i * 0.5) for i in range(8)]
    plan = captions.build_caption_plan(
        _cand(120.0, 124.0), _transcript(words),
        preset_id="bold_impact", max_words=3, position="bottom", layout={},
    )
    assert plan["chunks"], "expected chunks from 8 timed words"
    assert plan["chunks"][0]["start"] == pytest.approx(0.0, abs=0.01)
    for ch in plan["chunks"]:
        assert ch["start"] >= 0.0
        assert ch["end"] > ch["start"]
        assert ch["end"] <= 4.0 + 1e-6  # never past the clip duration


def test_words_straddling_the_boundary_are_clamped_not_dropped():
    words = [("first", 9.5, 10.4), ("second", 10.5, 11.0), ("last", 11.5, 12.6)]
    plan = captions.build_caption_plan(
        _cand(10.0, 12.0), _transcript(words),
        preset_id="clean_minimal", max_words=4, position="bottom", layout={},
    )
    joined = " ".join(c["text"].replace("\n", " ") for c in plan["chunks"])
    assert "first" in joined and "last" in joined
    assert plan["chunks"][0]["start"] == pytest.approx(0.0, abs=1e-6)
    assert max(c["end"] for c in plan["chunks"]) <= 2.0 + 1e-6


def test_segments_without_word_timestamps_are_distributed_evenly():
    transcript = {"segments": [
        {"start": 0.0, "end": 4.0, "text": "one two three four five six"}
    ]}
    plan = captions.build_caption_plan(
        _cand(0.0, 4.0), transcript,
        preset_id="bold_impact", max_words=3, position="bottom", layout={},
    )
    assert len(plan["chunks"]) == 2
    assert plan["chunks"][0]["start"] == pytest.approx(0.0)
    assert plan["chunks"][-1]["end"] == pytest.approx(4.0, abs=0.01)


def test_empty_transcript_yields_an_empty_plan_not_an_error():
    plan = captions.build_caption_plan(
        _cand(0.0, 3.0), {"segments": []},
        preset_id="bold_impact", max_words=3, position="bottom", layout={},
    )
    assert plan["chunks"] == []
    assert captions.caption_plan_to_overlays(plan) == []


# --------------------------------------------------------------------------
# Placement
# --------------------------------------------------------------------------

def test_caption_moves_off_a_keep_out_rect():
    clear = captions.resolve_position("bottom", {})
    # A HUD band sitting exactly where the default bottom caption lands.
    blocked = captions.resolve_position("bottom", {
        "safe_zones": {"hud": {"x": 0, "y": 1380, "w": 1080, "h": 200}}
    })
    assert blocked[1] != clear[1]
    assert blocked[1] < clear[1], "should nudge UP first"


def test_placement_avoids_a_face_rect_given_as_fractions():
    y = captions.resolve_position("center", {
        "safe_zones": [{"x": 0.1, "y": 0.40, "w": 0.8, "h": 0.12}]
    })[1]
    # Caption box is 10% tall; centre must clear the 0.40..0.52 band.
    assert y + captions.CAPTION_BOX_H_PCT / 2 <= 0.40 + 1e-6 or \
           y - captions.CAPTION_BOX_H_PCT / 2 >= 0.52 - 1e-6


def test_position_stays_inside_the_safe_band():
    lo = SAFE_TOP / 1920 + captions.CAPTION_BOX_H_PCT / 2
    hi = (1920 - SAFE_CAPTION_BOTTOM) / 1920
    # A keep-out rect covering the whole frame — nothing is clear, so the
    # least-overlapping position wins; it still may not leave the band.
    layouts = [
        {},
        {"safe_zones": {"all": {"x": 0, "y": 0, "w": 1080, "h": 1920}}},
        {"safe_zones": {"chat": {"x": 0, "y": 900, "w": 1080, "h": 900}}},
    ]
    for pos in ("top", "center", "bottom", "hook", "nonsense"):
        for layout in layouts:
            x, y = captions.resolve_position(pos, layout)
            assert x == 0.5
            assert lo - 1e-6 <= y <= hi + 1e-6, f"{pos} {layout} -> {y}"


def test_malformed_safe_zones_are_tolerated():
    for zones in (None, {}, [], {"face": None}, {"face": {"x": 0, "y": 0}},
                  {"hud": [{"x": 0, "y": 0, "w": 0, "h": 0}]}, "garbage"):
        x, y = captions.resolve_position("bottom", {"safe_zones": zones})
        assert x == 0.5 and 0.0 < y < 1.0


# --------------------------------------------------------------------------
# Text shaping
# --------------------------------------------------------------------------

def test_mask_profanity_keeps_first_and_last_letter():
    assert captions.mask_profanity("that fucking shit") == "that f*****g s**t"
    assert captions.mask_profanity("FUCK") == "F**K"          # case preserved
    assert captions.mask_profanity("hello world") == "hello world"
    assert captions.mask_profanity("") == ""
    # Substrings of innocent words are never touched (no stem matching).
    assert captions.mask_profanity("the future of Scunthorpe") == \
        "the future of Scunthorpe"


def test_long_chunks_wrap_to_two_lines():
    text = captions._wrap("absolutely ridiculous unbelievable outcome")
    assert text.count("\n") == 1
    assert all(len(line) <= 30 for line in text.split("\n"))
    assert captions._wrap("short one") == "short one"


# --------------------------------------------------------------------------
# Overlay shape
# --------------------------------------------------------------------------

def test_overlays_emit_every_key_build_overlays_ass_reads():
    words = [("hello", 0.0, 0.4), ("there", 0.5, 0.9), ("friend", 1.0, 1.5)]
    plan = captions.build_caption_plan(
        _cand(0.0, 2.0), _transcript(words),
        preset_id="neon_pop", max_words=2, position="bottom", layout={},
    )
    overlays = captions.caption_plan_to_overlays(plan)
    assert overlays
    required = {"text", "start_t", "end_t", "template_id", "style",
                "x_pct", "y_pct", "scale", "rotation"}
    for ovl in overlays:
        assert required <= set(ovl)
        assert isinstance(ovl["style"], dict)
        assert ovl["end_t"] > ovl["start_t"]
        assert 0.0 <= ovl["x_pct"] <= 1.0 and 0.0 <= ovl["y_pct"] <= 1.0
    assert overlays[0]["template_id"] == "neon_pop"


def test_unknown_preset_falls_back_to_the_default():
    plan = captions.build_caption_plan(
        _cand(0.0, 2.0), {"segments": []},
        preset_id="does_not_exist", max_words=3, position="bottom", layout={},
    )
    assert plan["preset_id"] == captions.DEFAULT_PRESET_ID
    assert plan["style"]["font_family"]


# --------------------------------------------------------------------------
# Caption entry pop
# --------------------------------------------------------------------------

def _pop_overlay(**extra):
    ovl = {"text": "HELLO THERE FRIEND", "start_t": 0.0, "end_t": 1.5,
           "template_id": "bold_impact", "x_pct": 0.5, "y_pct": 0.4375}
    ovl.update(extra)
    return ovl


_POP_WORDS = [{"word": "HELLO", "start": 0.0, "end": 0.4},
              {"word": "THERE", "start": 0.5, "end": 0.9},
              {"word": "FRIEND", "start": 1.0, "end": 1.5}]


def _events(tmp_path, overlays):
    out = tmp_path / "pop.ass"
    caption_overlays.build_overlays_ass(overlays, 1080, 1920, str(out))
    return [ln for ln in out.read_text(encoding="utf-8").splitlines()
            if ln.startswith("Dialogue:")]


def test_pop_tags_carry_the_measured_overshoot():
    # 0.91x -> 1.05x at 83ms -> 1.00x by 130ms, straight off `_LQ379ZhspI`.
    assert caption_overlays._pop_tags(100.0) == (
        "\\fscx91\\fscy91"
        "\\t(0,83,\\fscx105\\fscy105)"
        "\\t(83,130,\\fscx100\\fscy100)"
    )


def test_pop_multiplies_the_resting_scale_rather_than_replacing_it():
    # The active word rests at 112%, so its pop has to run 102 -> 118 -> 112,
    # otherwise the highlight would be flattened for the first 130ms.
    assert caption_overlays._pop_tags(112) == (
        "\\fscx102\\fscy102"
        "\\t(0,83,\\fscx118\\fscy118)"
        "\\t(83,130,\\fscx112\\fscy112)"
    )


def test_captions_do_not_animate_unless_asked(tmp_path):
    plain = _events(tmp_path, [_pop_overlay(words=_POP_WORDS)])
    assert plain and not any("\\t(" in line for line in plain)


def test_a_card_without_word_timings_pops_from_the_line_prefix(tmp_path):
    lines = _events(tmp_path, [_pop_overlay(entry_pop=True)])
    assert len(lines) == 1
    assert "\\t(0,83,\\fscx105\\fscy105)" in lines[0]


def test_only_the_card_landing_pops_not_every_word(tmp_path):
    # \t is relative to its own event, so leaving it on the later highlight
    # spans would re-fire the pop on every word instead of once per card.
    lines = _events(tmp_path, [_pop_overlay(entry_pop=True, words=_POP_WORDS)])
    assert len(lines) == 3
    assert sum("\\t(" in line for line in lines) == 1
    assert "\\t(" in lines[0]


def test_the_popping_card_animates_the_highlight_and_the_rest_together(tmp_path):
    # Every token in the first span sits under an inline tag, so both bases
    # have to carry a chain or half the line would sit still.
    first = _events(tmp_path, [_pop_overlay(entry_pop=True, words=_POP_WORDS)])[0]
    assert "\\t(83,130,\\fscx112\\fscy112)" in first   # the active word
    assert "\\t(83,130,\\fscx100\\fscy100)" in first   # everything after it


def test_plan_carries_entry_pop_only_when_asked():
    words = [("hello", 0.0, 0.4), ("there", 0.5, 0.9)]
    off = captions.build_caption_plan(
        _cand(0.0, 2.0), _transcript(words),
        preset_id="bold_impact", max_words=2, position="center", layout={},
    )
    on = captions.build_caption_plan(
        _cand(0.0, 2.0), _transcript(words),
        preset_id="bold_impact", max_words=2, position="center", layout={},
        entry_pop=True,
    )
    assert off["entry_pop"] is False and on["entry_pop"] is True
    assert "entry_pop" not in captions.caption_plan_to_overlays(off)[0]
    assert captions.caption_plan_to_overlays(on)[0]["entry_pop"] is True


# --------------------------------------------------------------------------
# Headline (heuristic path only — the LLM path is opt-in)
# --------------------------------------------------------------------------

def test_heuristic_prefers_the_first_question():
    cand = {"text": "So we set up. Why did that even work? It just did."}
    assert headline.heuristic_headline(cand, "en") == "Why did that even work"


def test_heuristic_trims_to_ten_words_and_capitalises_only_the_first():
    cand = {"text": " ".join(["word"] * 20) + "."}
    out = headline.heuristic_headline(cand, "en")
    assert len(out.split(" ")) == 10
    assert out.startswith("Word ") and out.endswith("word")


def test_heuristic_keeps_romanian_diacritics():
    # Six sentences so the "first third" window is two, and the cue-bearing
    # one is the second — not just the opening line.
    cand = {"text": "Am pregătit totul. Niciodată nu s-a întâmplat așa ceva. "
                    "Apoi am plecat. Restul a fost simplu. "
                    "Am revenit târziu. Gata."}
    out = headline.heuristic_headline(cand, "ro")
    assert out == "Niciodată nu s-a întâmplat așa ceva"


def test_heuristic_on_empty_candidate():
    assert headline.heuristic_headline({}, "en") == ""
    assert headline.heuristic_headline({"text": "   "}, "ro") == ""


@pytest.mark.asyncio
async def test_generate_headline_without_engine_is_heuristic():
    cand = {"text": "This is the part nobody expected to happen."}
    out = await headline.generate_headline(cand, engine=None, language="en")
    assert out["source"] == "heuristic"
    assert out["text"]


@pytest.mark.asyncio
async def test_generate_headline_never_raises_on_a_bad_engine():
    cand = {"text": "Something happened here."}
    out = await headline.generate_headline(cand, engine="not-a-provider",
                                           language="en")
    assert out == {"text": "Something happened here", "source": "heuristic"}
