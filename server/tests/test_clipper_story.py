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


def test_what_the_model_says_is_unresolved_raises_the_word_list():
    """A word list cannot know that "Daniel" was never introduced."""
    words = _words("the mechanic asked me for five thousand euros")
    plain = story.context_debt(words)
    flagged = story.context_debt(words, {"unresolved_context": ["who Daniel is",
                                                                "what happened"]})
    assert flagged > plain >= 0.0


def test_one_listed_item_cannot_condemn_a_self_contained_clip():
    """The regression: one unverified string used to pin any clip to 0.500.

    Both clips of the first export anyone watched scored 0.000 on the words and
    0.500 from a single listed item; watched cold, both were understood.
    """
    words = _words("that's all facts or not chat facts or not that's all i ask")
    assert story.context_debt(words) == 0.0
    one = story.context_debt(words, {"unresolved_context": ["who Kyle is"]})
    assert 0.0 < one <= 0.2, f"a single unverified item should nudge, not condemn: {one}"


def test_a_long_listed_context_still_reads_as_expensive():
    """Demoting the floor must not make the model's list toothless."""
    words = _words("the mechanic asked me for five thousand euros")
    many = story.context_debt(words, {"unresolved_context": ["a", "b", "c", "d", "e"]})
    assert many >= 0.3, f"five unresolved facts is a real debt, got {many}"


def test_the_listed_contribution_is_capped():
    words = _words("the mechanic asked me for five thousand euros")
    three = story.context_debt(words, {"unresolved_context": ["a", "b", "c"]})
    ten = story.context_debt(words, {"unresolved_context": list("abcdefghij")})
    assert three == ten


def test_a_back_reference_whose_referent_is_in_the_clip_costs_nothing():
    """The word list charges for "remember what he said" either way. It costs
    nothing when he said it eight seconds ago and the viewer just heard it."""
    from services.clipper import atoms as atoms_mod

    said = _words("Mike said the warden spawns in the deep dark", 100.0)
    recalled = _words("remember what Mike said about the warden", 112.0)
    inside = said + recalled
    stream = [{"i": 0, "start": 100.0, "end": 108.0,
               "text": " ".join(w["word"] for w in said),
               "audio": {"energy": 0.3, "peaks": 0, "laughter": 0.0},
               "visual": {"motion": 0.3, "scene_change": False, "ui": 0.0},
               "semantic": {"kind": "speech", "cues": 0, "words": len(said),
                            "importance": 0.2}}]

    refs = story.resolve_backrefs(inside, stream, start=100.0)
    assert refs and refs[0]["resolved"], "the referent is inside the clip"
    paid = story.context_debt(inside, None, backrefs=refs)

    # The same words, but the referent is an hour back and outside the clip.
    far = [dict(stream[0], start=10.0, end=18.0)]
    refs_far = story.resolve_backrefs(recalled, far, start=112.0)
    owed = story.context_debt(recalled, None, backrefs=refs_far)
    assert owed > paid


def test_with_no_atoms_the_word_list_still_answers():
    """Every legacy path passes no atoms, and must behave exactly as before."""
    words = _words("and then he told me exactly what you said yesterday")
    assert story.resolve_backrefs(words, None, 0.0) == []
    assert story.context_debt(words) > 0.5


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
    from services.clipper.candidate_proposals import _snap_context_to_speech

    snapped = _snap_context_to_speech(anchor, words)
    assert snapped["required_context"][0]["t"] == pytest.approx(30.0)


def test_a_context_timestamp_already_on_speech_is_left_alone():
    words = [{"word": f"w{i}", "start": 30.0 + i * 0.4, "end": 30.3 + i * 0.4}
             for i in range(40)]
    anchor = _anchor(payoff_t=44.0,
                     required_context=[{"t": 30.2, "fact": "on speech"}])
    from services.clipper.candidate_proposals import _snap_context_to_speech

    snapped = _snap_context_to_speech(anchor, words)
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


# ── promises and callbacks ───────────────────────────────────────────────────
#
# The one kind of clip a per-chunk pass cannot see: "he predicted he would
# choke" at hour two and "he chokes exactly as predicted" at hour three are
# two unrelated events unless something remembers the first.


def _promise(t, text="he predicted he would choke the final round"):
    return {"t": t, "kind": "prediction", "text": text, "confidence": 0.8}


def test_only_setups_still_live_are_offered_to_a_later_chunk():
    from services.clipper import promises

    pool = [_promise(100.0), _promise(4000.0), _promise(5000.0)]
    live = promises.open_at(pool, 5200.0)
    assert [p["t"] for p in live] == [5000.0, 4000.0], "wrong ones, or wrong order"


def test_a_chunk_spanning_hours_offers_setups_from_its_whole_span():
    """A prompt covers a chunk, and a chunk of a real stream is hours long.
    Asking only about its end left the first chunk of a 4-hour run with zero
    setups offered — every promise in it was more than a lifetime older than
    minute 201. A payoff at minute 60 must be able to see one from minute 20."""
    from services.clipper import promises

    pool = [_promise(240.0), _promise(1200.0)]      # 4 and 20 minutes in
    at_end_only = promises.open_at(pool, 12060.0)   # the chunk ends at 201m
    assert at_end_only == [], "this is the old behaviour, kept for point queries"

    across = promises.open_at(pool, 12060.0, span_from=0.0)
    assert [p["t"] for p in across] == [1200.0, 240.0]


def test_a_setup_the_payoff_is_sitting_on_is_not_a_callback():
    """Closer than the gap and the two are one moment, which the ordinary
    required-context path already handles better."""
    from services.clipper import promises

    assert promises.open_at([_promise(1000.0)], 1030.0) == []


def test_a_setup_goes_stale():
    """Otherwise every loud moment for the rest of the stream inherits one."""
    from services.clipper import promises

    assert promises.open_at([_promise(100.0)], 100.0 + 10 * 3600) == []


def test_a_callback_that_restates_its_setup_owes_less():
    """This is the case where a callback works as a standalone clip at all."""
    from services.clipper import promises

    p = _promise(100.0, "he will choke the final round")
    restated = [{"word": w} for w in
                "I said I would choke the final round and I choked".split()]
    silent = [{"word": w} for w in "well that happened I guess".split()]
    assert promises.callback_debt(p, restated) < 0.35
    assert promises.callback_debt(p, silent) > 0.8


def test_no_callback_means_nothing_owed():
    from services.clipper import promises

    assert promises.callback_debt(None, [{"word": "a"}]) == 0.0


def test_an_unplaceable_setup_is_dropped():
    from services.clipper import promises

    for raw in ({}, {"text": "no time"}, {"t": 5}, {"t": -1, "text": "x"}, "nope"):
        assert promises.normalise_promise(raw, 1000.0) is None


def test_a_goal_with_no_stake_is_not_a_setup():
    """Measured on the 4-hour run: 8 of 12 setups were goals like "I'm hitting
    the cave" — announcements of the next action, with no payoff to wait for."""
    from services.clipper import promises

    bare = {"t": 100.0, "kind": "goal", "text": "I'm going to go to the gym"}
    assert promises.normalise_promise(bare, 1000.0) is None
    staked = {**bare, "stake": "or he shaves his head"}
    kept = promises.normalise_promise(staked, 1000.0)
    assert kept and kept["kind"] == "goal" and kept["stake"]


def test_the_falsifiable_kinds_still_need_no_stake():
    """"there is no way he does that" carries no stake and is still a setup."""
    from services.clipper import promises

    for kind in ("prediction", "bet", "challenge", "promise"):
        raw = {"t": 100.0, "kind": kind, "text": "there is no way he does that"}
        out = promises.normalise_promise(raw, 1000.0)
        assert out is not None and out["kind"] == kind, kind
        assert out["stake"] == ""


def test_every_setup_carries_the_prompt_version_that_made_it():
    from services.clipper import promises

    out = promises.normalise_promise(
        {"t": 10.0, "kind": "bet", "text": "x"}, 1000.0)
    assert out["prompt_version"] == promises.PROMISE_PROMPT_VERSION == "promises_v2"


def test_the_prompt_names_the_intentions_that_were_mistaken_for_setups():
    """The two false positives the 4-hour run produced, quoted back at it."""
    from services.clipper import promises

    text = promises.prompt("0.0 hello", 6)
    assert "gym" in text and "cave" in text
    assert "stake" in text


def test_a_callback_raises_the_debt_of_the_window_it_lands_in():
    """The setup is an hour away and can never be in the window."""
    anchor = _anchor(payoff_t=200.0, hook_t=180.0,
                     required_context=[{"t": 180.0, "fact": "1 HP"}])
    plain = cand_mod.candidates_from_anchors(
        [anchor], _transcript(), {}, min_s=15.0, max_s=90.0, duration=400.0)
    with_cb = cand_mod.candidates_from_anchors(
        [{**anchor, "callback_to": _promise(20.0, "totally unrelated wording")}],
        _transcript(), {}, min_s=15.0, max_s=90.0, duration=400.0)
    assert with_cb[0]["story"]["callback_to"]["t"] == 20.0
    assert with_cb[0]["story"]["context_debt"] > plain[0]["story"]["context_debt"]


async def test_a_named_callback_is_linked_and_tagged(monkeypatch):
    async def fake(engine, prompt, **kw):
        assert "still unresolved" in prompt, "the open setups never reached the model"
        return ('[{"payoff_t": 5000, "archetypes": ["FAIL"], '
                '"why": "fails exactly as predicted", "callback_to": 3502}]')

    monkeypatch.setattr("services.descriptions._call_llm", fake)
    # 23 minutes before the end of the chunk — inside the lifetime a setup
    # stays live for, and past the gap a payoff must clear.
    out = await llm_select.detect_anchors(
        [{"start": 4900.0, "text": "x"}], 6000.0, promises=[_promise(3500.0)])
    assert out and out[0]["callback_to"]["t"] == 3500.0
    assert "CALLBACK" in out[0]["archetypes"]


async def test_a_setup_inside_the_same_chunk_is_still_recalled(monkeypatch):
    """A chunk holds five hours of this source. A model will not reliably
    connect a payoff at hour three to a line at hour one buried in 30k tokens,
    so in-chunk setups belong in the recall list too."""
    seen = {}

    async def fake(engine, prompt, **kw):
        seen["prompt"] = prompt
        return "[]"

    monkeypatch.setattr("services.descriptions._call_llm", fake)
    # One chunk spanning the setup AND a much later moment.
    segments = [{"start": 500.0, "text": "he makes a prediction here"},
                {"start": 2900.0, "text": "and much later it happens"}]
    await llm_select.detect_anchors(segments, 4000.0,
                                    promises=[_promise(500.0)])
    assert "still unresolved" in seen["prompt"], (
        "a setup inside the chunk was hidden from the model"
    )


async def test_a_callback_to_a_setup_that_does_not_exist_is_ignored(monkeypatch):
    async def fake(engine, prompt, **kw):
        return '[{"payoff_t": 5000, "archetypes": ["FAIL"], "callback_to": 4400}]'

    monkeypatch.setattr("services.descriptions._call_llm", fake)
    # 23 minutes before the end of the chunk — inside the lifetime a setup
    # stays live for, and past the gap a payoff must clear.
    out = await llm_select.detect_anchors(
        [{"start": 4900.0, "text": "x"}], 6000.0, promises=[_promise(3500.0)])
    assert out and "callback_to" not in out[0]
    assert "CALLBACK" not in out[0]["archetypes"]


# ── semantic dedupe and archetype diversity ──────────────────────────────────


def _cut(start, end, score, payoff=None, kinds=()):
    c = {"start": start, "end": end, "overall": score,
         "text": f"words at {start:.0f} and nothing else in common"}
    if payoff is not None:
        c["story"] = {"payoff_t": payoff, "archetypes": list(kinds)}
    return c


def test_two_cuts_of_one_moment_collapse_even_with_different_words():
    """A tight cut and a story-rich cut of one joke share little text and can
    fall under the overlap threshold, while being the same clip. What makes
    them the same is the payoff."""
    from services.clipper import dedupe

    a = _cut(100.0, 130.0, 80.0, payoff=128.0, kinds=["FUNNY"])
    b = _cut(124.0, 140.0, 70.0, payoff=129.0, kinds=["FUNNY"])
    assert dedupe.same_story(a, b)
    out = dedupe.deduplicate([a, b], overlap_threshold=0.99,
                             text_threshold=0.99, target_count=8)
    assert sum(1 for c in out if not c["is_alternative"]) == 1


def test_two_different_moments_are_not_collapsed():
    from services.clipper import dedupe

    a = _cut(100.0, 130.0, 80.0, payoff=128.0, kinds=["FUNNY"])
    b = _cut(400.0, 430.0, 70.0, payoff=428.0, kinds=["FUNNY"])
    assert not dedupe.same_story(a, b)


def test_a_legacy_candidate_is_never_grouped_by_story():
    """This only ever adds groupings — a candidate with no story must behave
    exactly as before."""
    from services.clipper import dedupe

    plain = _cut(100.0, 130.0, 80.0)
    told = _cut(500.0, 530.0, 70.0, payoff=528.0, kinds=["FAIL"])
    assert not dedupe.same_story(plain, told)
    assert not dedupe.same_story(plain, plain)


def test_the_board_does_not_fill_with_one_archetype():
    """Eight rage clips when the same stream also has a clutch and a story is
    the failure this exists to stop."""
    from services.clipper import dedupe

    cands = [_cut(i * 100.0, i * 100.0 + 30.0, 90.0 - i,
                  payoff=i * 100.0 + 28.0, kinds=["RAGE"]) for i in range(5)]
    # Slightly worse, but the only clip of its kind.
    cands.append(_cut(600.0, 630.0, 82.0, payoff=628.0, kinds=["CLUTCH"]))
    out = dedupe.deduplicate(cands, overlap_threshold=0.5, text_threshold=0.9,
                             target_count=3)
    top3 = sorted((c for c in out if not c["is_alternative"]),
                  key=lambda c: c["rank_position"])[:3]
    kinds = {k for c in top3 for k in (c["story"]["archetypes"])}
    assert "CLUTCH" in kinds, f"the only clutch never made the board: {kinds}"


def test_diversity_does_not_rescue_a_weak_clip():
    """It exists to stop redundancy, not to promote something bad."""
    from services.clipper import dedupe

    strong = [_cut(i * 100.0, i * 100.0 + 30.0, 90.0,
                   payoff=i * 100.0 + 28.0, kinds=["RAGE"]) for i in range(3)]
    weak = _cut(900.0, 930.0, 5.0, payoff=928.0, kinds=["WHOLESOME"])
    out = dedupe.deduplicate(strong + [weak], overlap_threshold=0.5,
                             text_threshold=0.9, target_count=2)
    top2 = sorted((c for c in out if not c["is_alternative"]),
                  key=lambda c: c["rank_position"])[:2]
    assert all(c["overall"] > 50 for c in top2), "a weak clip was promoted"


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
