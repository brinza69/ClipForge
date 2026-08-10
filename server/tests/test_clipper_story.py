"""
Unit tests for the story engine — payoff-first candidate reasoning.

Everything here is pure. Anchors are handed in as plain dicts, exactly as
`llm_select.detect_anchors` would produce them, so the geometry is testable
without a network call. That matters because the interesting failures are in
the geometry: a window that opens after the fact that makes it make sense.

The example the whole design is built around:

    42:18  "if I win this with 1 HP..."     <- required context, and the hook
    42:22  fight starts
    42:34  kill
    42:38  wins 1v3                         <- payoff
    42:43  friend reacts                    <- reaction

A spike detector takes 42:34-42:43. The story engine has to take 42:18-42:47,
because the setup is what makes the payoff worth anything.
"""

from __future__ import annotations

import pytest

from services.clipper import candidates as cand_mod
from services.clipper import llm_select, story


def _anchor(**over):
    base = {
        "payoff_t": 2558.0,
        "payoff_strength": 0.9,
        "archetypes": ["CLUTCH"],
        "why": "wins 1v3 after announcing 1 HP",
        "required_context": [
            {"t": 2538.0, "fact": "only has 1 HP"},
            {"t": 2542.0, "fact": "three enemies left"},
        ],
        "hook_t": 2538.0,
        "unresolved_context": [],
        "confidence": 0.9,
        "story_version": story.STORY_VERSION,
    }
    base.update(over)
    return base


# ── the editing principle ────────────────────────────────────────────────────


def test_the_start_comes_from_the_earliest_required_fact():
    """Not from the first audio spike. This is the whole backward
    reconstruction: the kill means nothing without the 1 HP."""
    assert story.latest_complete_start(_anchor()) == 2538.0


def test_with_no_required_context_the_start_falls_back_to_the_hook():
    a = _anchor(required_context=[], hook_t=2550.0)
    assert story.latest_complete_start(a) == 2550.0


def test_the_balanced_variant_spans_setup_to_reaction():
    out = story.variants_from_anchor(_anchor(), reaction_end=2567.0,
                                     lo=15.0, hi=90.0)
    balanced = [v for v in out if v["variant"] == "balanced"][0]
    assert balanced["start"] == 2538.0, "lost the setup"
    assert balanced["end"] == 2567.0, "cut the reaction"


def test_variants_compete_rather_than_one_being_chosen():
    out = story.variants_from_anchor(_anchor(), reaction_end=2567.0,
                                     lo=15.0, hi=90.0)
    assert len(out) >= 2, "one window per anchor is the old behaviour"
    assert len({v["variant"] for v in out}) == len(out)
    for v in out:
        assert v["story"]["edit_reason"], "a cut nobody can explain"


def test_a_hook_before_the_setup_earns_its_own_variant():
    a = _anchor(hook_t=2500.0,
                required_context=[{"t": 2538.0, "fact": "1 HP"}])
    names = {v["variant"] for v in story.variants_from_anchor(
        a, reaction_end=2567.0, lo=15.0, hi=90.0)}
    assert "hook_first" in names


def test_a_window_that_must_shrink_loses_setup_not_payoff():
    """The end is where the story finishes. Anything that has to give is at
    the front."""
    a = _anchor(required_context=[{"t": 2400.0, "fact": "far back"}])
    out = story.variants_from_anchor(a, reaction_end=2567.0, lo=15.0, hi=60.0)
    assert out
    for v in out:
        assert v["end"] == 2567.0
        assert v["end"] - v["start"] <= 60.0 + 1e-6


def test_variants_stay_inside_the_source():
    out = story.variants_from_anchor(_anchor(), reaction_end=2567.0,
                                     lo=15.0, hi=90.0, ceiling=2560.0)
    for v in out:
        assert v["end"] <= 2560.0 and v["start"] >= 0.0


def test_an_anchor_too_close_to_the_start_still_yields_something_usable():
    a = _anchor(payoff_t=8.0, hook_t=1.0,
                required_context=[{"t": 1.0, "fact": "x"}])
    out = story.variants_from_anchor(a, reaction_end=12.0, lo=15.0, hi=90.0)
    for v in out:
        assert v["start"] >= 0.0 and v["end"] - v["start"] >= 15.0 - 1e-6


# ── validating what a model sent ─────────────────────────────────────────────


def test_an_anchor_with_no_payoff_time_is_dropped_not_guessed_at():
    for raw in ({}, {"why": "something"}, {"payoff_t": -3}, "nope", None):
        assert story.normalise_anchor(raw, 3600.0) is None


def test_context_after_the_payoff_is_not_context():
    a = story.normalise_anchor({
        "payoff_t": 100.0,
        "required_context": [{"t": 90.0, "fact": "before"},
                             {"t": 120.0, "fact": "after"}],
    }, 3600.0)
    assert [c["fact"] for c in a["required_context"]] == ["before"]


def test_context_from_far_earlier_is_a_different_clip():
    a = story.normalise_anchor({
        "payoff_t": 500.0,
        "required_context": [{"t": 100.0, "fact": "ancient"},
                             {"t": 470.0, "fact": "recent"}],
    }, 3600.0)
    assert [c["fact"] for c in a["required_context"]] == ["recent"]


def test_an_invented_archetype_is_discarded():
    a = story.normalise_anchor(
        {"payoff_t": 10.0, "archetypes": ["CLUTCH", "VIBES", "fail"]}, 100.0)
    assert a["archetypes"] == ["CLUTCH", "FAIL"]


def test_every_archetype_has_a_shape_to_judge_it_by():
    """A FUNNY clip must not be marked down for lacking stakes, and a CLUTCH
    one must not be excused for lacking them."""
    assert set(story.ARCHETYPE_SHAPE) == set(story.ARCHETYPES)
    assert all(story.ARCHETYPE_SHAPE[k] for k in story.ARCHETYPES)


# ── context debt ─────────────────────────────────────────────────────────────


def _words(text, t0=0.0):
    return [{"word": w, "start": t0 + i * 0.4, "end": t0 + i * 0.4 + 0.3}
            for i, w in enumerate(text.split())]


def test_opening_on_an_unresolved_reference_is_expensive():
    debt = story.context_debt(
        _words("and then he told me exactly what you said yesterday"))
    assert debt > 0.5, f"a cold viewer cannot follow this, debt={debt}"


def test_a_self_contained_opening_is_cheap():
    debt = story.context_debt(
        _words("the mechanic asked me for five thousand euros to fix the car"))
    assert debt < 0.25, f"nothing here needs outside knowledge, debt={debt}"


def test_what_the_model_says_is_unresolved_outweighs_the_word_list():
    """A word list cannot know that "Daniel" was never introduced."""
    words = _words("the mechanic asked me for five thousand euros")
    plain = story.context_debt(words)
    flagged = story.context_debt(words, {"unresolved_context": ["who Daniel is",
                                                                "what happened"]})
    assert flagged > plain >= 0.0


def test_context_debt_is_always_a_usable_number():
    for words in ([], None, _words("a"), _words("he it that they")):
        d = story.context_debt(words)
        assert 0.0 <= d <= 1.0


# ── hook latency ─────────────────────────────────────────────────────────────


def test_a_clip_that_opens_on_its_hook_has_no_latency():
    assert story.hook_latency(100.0, 100.0, _words("I thought I killed him", 100.0)) == 0.0


def test_a_late_hook_is_measured_not_ignored():
    lat = story.hook_latency(100.0, 111.0, _words("talking", 100.0))
    assert lat == pytest.approx(11.0)


def test_with_no_hook_the_first_word_is_the_latency():
    """A clip that opens on silence has already spent it."""
    assert story.hook_latency(100.0, None, _words("hello", 104.0)) == pytest.approx(4.0)


# ── the pipeline seam ────────────────────────────────────────────────────────


def _transcript(n=400):
    words = [{"word": f"w{i}", "start": i * 1.0, "end": i * 1.0 + 0.6,
              "probability": 0.9} for i in range(n)]
    return {"segments": [{"start": 0.0, "end": float(n), "words": words,
                          "text": " ".join(w["word"] for w in words)}]}


def test_anchors_become_candidates_carrying_their_rationale():
    cands = cand_mod.candidates_from_anchors(
        [_anchor(payoff_t=200.0, hook_t=180.0,
                 required_context=[{"t": 180.0, "fact": "1 HP"}])],
        _transcript(), {}, min_s=15.0, max_s=90.0, duration=400.0)
    assert cands
    for c in cands:
        assert c["text"] and c["words"], "a candidate with no words cannot refine"
        s = c["story"]
        assert s["archetypes"] == ["CLUTCH"]
        assert s["required_context"]
        assert 0.0 <= s["context_debt"] <= 1.0
        assert s["hook_latency"] >= 0.0
        assert s["story_version"] == story.STORY_VERSION


def test_a_story_candidate_survives_the_merge_with_its_story_intact():
    """merge_nominations rebuilds the dict; losing `story` there would leave a
    candidate nobody can explain."""
    anchors = [_anchor(payoff_t=200.0, required_context=[{"t": 180.0, "fact": "x"}])]
    proposed = cand_mod.candidates_from_anchors(
        anchors, _transcript(), {}, min_s=15.0, max_s=90.0, duration=400.0)
    merged = cand_mod.merge_nominations(
        [{"start": 0.0, "end": 40.0, "text": "a", "words": [], "reasons": ["rule"]}],
        proposed, _transcript(), min_s=15.0, max_s=90.0)
    carried = [c for c in merged if c.get("story")]
    assert carried, "the story rationale was dropped in the merge"
    assert carried[0]["story"]["archetypes"] == ["CLUTCH"]


def test_a_context_timestamp_in_a_silence_is_snapped_to_the_speech():
    """A model names a moment by the segment it sits in, and segment starts
    are coarse. Measured: a context timestamp landed 12.6s before the first
    word of the thing it described, so the window opened on twelve seconds of
    silence and reported a 12.6s hook latency for a 15s clip."""
    words = ([{"word": "a", "start": 10.0, "end": 10.4}]
             + [{"word": f"w{i}", "start": 30.0 + i * 0.4,
                 "end": 30.3 + i * 0.4} for i in range(40)])
    anchor = _anchor(payoff_t=44.0, hook_t=None,
                     required_context=[{"t": 18.0, "fact": "in a gap"}])
    snapped = cand_mod._snap_context_to_speech(anchor, words)
    assert snapped["required_context"][0]["t"] == pytest.approx(30.0)


def test_a_context_timestamp_already_on_speech_is_left_alone():
    words = [{"word": f"w{i}", "start": 30.0 + i * 0.4, "end": 30.3 + i * 0.4}
             for i in range(40)]
    anchor = _anchor(payoff_t=44.0,
                     required_context=[{"t": 30.2, "fact": "on speech"}])
    snapped = cand_mod._snap_context_to_speech(anchor, words)
    assert snapped["required_context"][0]["t"] == pytest.approx(30.2)


def test_a_story_cut_is_not_swallowed_by_the_window_it_overlaps():
    """A story window on the same moment as a heuristic one is not a
    duplicate — it is a different BOUNDARY for that moment, derived from the
    earliest fact the payoff needs. Which one wins is a question for dedupe
    after scoring. Measured with the coverage check on: all 14 story windows
    were swallowed and none reached the board."""
    heuristic = [{"start": 170.0, "end": 240.0, "text": "a", "words": [],
                  "reasons": ["rule"]}]
    story_cut = [{"start": 179.0, "end": 233.0, "reasons": ["story_anchor"],
                  "variant": "balanced", "story": {"archetypes": ["FUNNY"]}}]
    swallowed = cand_mod.merge_nominations(
        heuristic, story_cut, _transcript(), min_s=15.0, max_s=90.0)
    assert len(swallowed) == 1, "this is the legacy behaviour, still wanted there"

    kept = cand_mod.merge_nominations(
        heuristic, story_cut, _transcript(), min_s=15.0, max_s=90.0,
        keep_overlaps=True)
    assert len(kept) == 2, "the story cut has to survive to be scored"
    assert any(c.get("story") for c in kept)


def test_a_dud_anchor_does_not_take_the_batch_down():
    good = _anchor(payoff_t=200.0, hook_t=180.0,
                   required_context=[{"t": 180.0, "fact": "1 HP"}])
    cands = cand_mod.candidates_from_anchors(
        [{"nonsense": True}, good], _transcript(), {},
        min_s=15.0, max_s=90.0, duration=400.0)
    assert cands, "one bad anchor discarded the good one"


def test_context_on_the_wrong_side_of_the_payoff_is_ignored():
    """story.py is reached from the heuristic path and from tests too, not
    only through normalise_anchor. A context timestamp after the payoff would
    otherwise produce a window with a negative length."""
    broken = _anchor(payoff_t=200.0, hook_t=None,
                     required_context=[{"t": 2538.0, "fact": "impossible"}])
    assert story.latest_complete_start(broken) == 200.0
    for v in story.variants_from_anchor(broken, reaction_end=215.0,
                                        lo=15.0, hi=90.0):
        assert v["end"] > v["start"]


# ── the story properties have to REACH the score ─────────────────────────────


def test_context_debt_costs_the_clip_completeness():
    """A measurement nobody reads is not a feature. This was inert until
    context_completeness started reading it."""
    from services.clipper import scoring

    cand = {"start": 0.0, "end": 35.0}
    base = {"duration": 35.0, "self_contained": 1.0}
    clean = scoring.score_candidate(cand, dict(base, context_debt=0.0),
                                    profile="interview", platform="tiktok")
    owing = scoring.score_candidate(cand, dict(base, context_debt=0.9),
                                    profile="interview", platform="tiktok")
    assert (owing["sub_scores"]["context_completeness"]
            < clean["sub_scores"]["context_completeness"] - 20)
    assert owing["overall"] < clean["overall"]


def test_a_late_hook_costs_the_clip_its_hook_score():
    """A hook is not energy. hook_latency is the only thing here that measures
    how long a cold viewer waits to learn why to stay."""
    from services.clipper import scoring

    cand = {"start": 0.0, "end": 35.0}
    base = {"duration": 35.0, "hook_strength": 0.6}
    prompt = scoring.score_candidate(cand, dict(base, hook_latency=0.0),
                                     profile="podcast", platform="tiktok")
    late = scoring.score_candidate(cand, dict(base, hook_latency=11.0),
                                   profile="podcast", platform="tiktok")
    assert late["sub_scores"]["hook"] < prompt["sub_scores"]["hook"] - 15
    assert late["overall"] < prompt["overall"]


def test_the_legacy_path_is_unaffected_by_either():
    """Absent must mean 'nothing owed, opens on the hook' — every pre-existing
    caller omits both keys."""
    from services.clipper import scoring

    cand = {"start": 0.0, "end": 35.0}
    base = {"duration": 35.0, "self_contained": 1.0, "hook_strength": 0.6}
    absent = scoring.score_candidate(cand, base, profile="gaming",
                                     platform="tiktok")
    zero = scoring.score_candidate(cand, dict(base, context_debt=0.0,
                                              hook_latency=0.0),
                                   profile="gaming", platform="tiktok")
    assert absent["overall"] == zero["overall"]


def test_a_story_candidate_emits_both_through_extract_features():
    feats = cand_mod.extract_features(
        {"start": 100.0, "end": 140.0, "words": [],
         "story": {"context_debt": 0.42, "hook_latency": 2.5}},
        _transcript(), {}, 400.0)
    assert feats["context_debt"] == pytest.approx(0.42)
    assert feats["hook_latency"] == pytest.approx(2.5)


# ── the LLM pass, without an LLM ─────────────────────────────────────────────


async def test_detect_anchors_returns_nothing_when_no_engine_answers(monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("nothing running")

    monkeypatch.setattr("services.descriptions._call_llm", boom)
    assert await llm_select.detect_anchors(
        [{"start": 0.0, "text": "hello"}], 100.0) == []


async def test_detect_anchors_validates_what_it_gets(monkeypatch):
    async def fake(engine, prompt, **kw):
        assert "PAYOFF" in prompt and "REQUIRED CONTEXT" in prompt
        return ('[{"payoff_t": 50, "archetypes": ["FAIL", "MADE_UP"], '
                '"required_context": [{"t": 30, "fact": "he predicted it"}], '
                '"hook": {"t": 30}, "why": "fails exactly as predicted"},'
                ' {"why": "no timestamp at all"}]')

    monkeypatch.setattr("services.descriptions._call_llm", fake)
    out = await llm_select.detect_anchors([{"start": 0.0, "text": "x"}], 100.0)
    assert len(out) == 1, "the anchor with no payoff time should be dropped"
    assert out[0]["archetypes"] == ["FAIL"]
    assert out[0]["prompt_version"] == llm_select.ANCHOR_PROMPT_VERSION
