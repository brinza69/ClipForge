"""
Unit tests for LLM-assisted clip selection.

No network: every test drives the pure half — parsing what a model said,
turning moments into windows, unioning them with the rule-based candidates,
and blending the verdict. The two async passes are covered only for their
failure behaviour, which is the part that must never take a run down with it.

The measurement this whole module exists for: on a 12-minute IShowSpeed
co-stream the rule-based scorer put "let's cook our food" at #5 of its top
eight and put a chat-trolls-him-and-he-notices bit at #53 of 53.
"""

from __future__ import annotations

import pytest

from services.clipper import candidates as cand_mod
from services.clipper import llm_select


# ── parsing what a model actually sends back ─────────────────────────────────


def test_plain_json_parses():
    assert llm_select.parse_json('[{"t": 12, "why": "he falls over"}]') == [
        {"t": 12, "why": "he falls over"}
    ]


def test_a_code_fence_is_not_fatal():
    """Models fence their JSON however firmly you ask them not to, and a whole
    analysis pass must not be lost to a stray backtick."""
    raw = '```json\n[{"t": 5, "why": "x"}]\n```'
    assert llm_select.parse_json(raw) == [{"t": 5, "why": "x"}]


def test_a_preface_is_not_fatal():
    raw = 'Sure! Here are the moments:\n[{"t": 5, "why": "x"}]\nHope that helps.'
    assert llm_select.parse_json(raw) == [{"t": 5, "why": "x"}]


@pytest.mark.parametrize("raw", ["", "   ", None, "no json at all", "{oops"])
def test_junk_returns_none_rather_than_raising(raw):
    assert llm_select.parse_json(raw) is None


# ── moments -> windows ───────────────────────────────────────────────────────


def test_a_moment_becomes_a_window_inside_the_source():
    out = llm_select.moments_to_windows(
        [{"t": 100.0, "duration": 30.0, "why": "he gets trolled"}], duration=200.0)
    assert len(out) == 1
    win = out[0]
    assert win["start"] >= 0.0 and win["end"] <= 200.0
    assert 15.0 <= win["end"] - win["start"] <= 90.0
    assert win["llm_tag"] == "he gets trolled"
    assert "llm_nominated" in win["reasons"]


def test_a_moment_at_the_very_end_is_clamped_not_dropped():
    out = llm_select.moments_to_windows(
        [{"t": 195.0, "duration": 60.0}], duration=200.0)
    assert out and out[0]["end"] <= 200.0
    assert out[0]["end"] - out[0]["start"] >= 15.0


def test_a_duration_outside_the_bounds_is_clamped():
    for want, lo, hi in ((3.0, 15.0, 90.0), (500.0, 15.0, 90.0)):
        out = llm_select.moments_to_windows(
            [{"t": 100.0, "duration": want}], duration=1000.0)
        assert out and lo <= out[0]["end"] - out[0]["start"] <= hi


@pytest.mark.parametrize("moments", [None, "nope", [1, 2], [{"why": "no time"}]])
def test_unusable_moments_are_dropped_not_guessed_at(moments):
    assert llm_select.moments_to_windows(moments, duration=100.0) == []


# ── blending the verdict ─────────────────────────────────────────────────────


def test_the_verdict_is_blended_not_substituted():
    """The heuristic is transparent and the model cannot see the picture, so
    neither gets the whole vote."""
    cands = [{"overall": 40.0}, {"overall": 80.0}]
    hit = llm_select.apply_scores(
        cands, [{"id": 0, "score": 100.0}, {"id": 1, "score": 0.0}], weight=0.5)
    assert hit == 2
    assert cands[0]["overall"] == 70.0
    assert cands[1]["overall"] == 40.0
    assert cands[0]["llm_score"] == 100.0


def test_a_candidate_the_model_skipped_keeps_its_score():
    cands = [{"overall": 40.0}, {"overall": 80.0}]
    assert llm_select.apply_scores(cands, [{"id": 0, "score": 90.0}]) == 1
    assert cands[1]["overall"] == 80.0
    assert "llm_score" not in cands[1]


def test_a_nonsense_verdict_changes_nothing():
    cands = [{"overall": 55.0}]
    for verdicts in (None, "junk", [{"id": "x"}], [{"score": 50}],
                     [{"id": 0, "score": "high"}]):
        llm_select.apply_scores(cands, verdicts)
    assert cands[0]["overall"] == 55.0


def test_an_over_range_score_is_clamped_but_a_negative_one_is_no_verdict():
    """Asymmetric on purpose. 900 is a model being enthusiastic about a real
    judgement, so clamp it. A negative score is nonsense, and 'said nothing'
    is a safer reading of nonsense than 'called it worthless' — the latter
    drags the blend down on a clip the model never actually rejected."""
    cands = [{"overall": 50.0}, {"overall": 50.0}]
    llm_select.apply_scores(cands, [{"id": 0, "score": 900.0},
                                    {"id": 1, "score": -900.0}], weight=1.0)
    assert cands[0]["overall"] == 100.0
    assert cands[1]["overall"] == 50.0 and "llm_score" not in cands[1]


# ── union with the rule-based candidates ─────────────────────────────────────


def _tr():
    words = [{"word": f"w{i}", "start": i * 1.0, "end": i * 1.0 + 0.5,
              "probability": 0.9} for i in range(300)]
    return {"segments": [{"start": 0.0, "end": 300.0, "text": " ".join(
        w["word"] for w in words), "words": words}]}


def test_a_nomination_the_scorer_missed_is_added():
    """The whole point of a second nominator is the moments the first missed."""
    existing = [{"start": 0.0, "end": 40.0, "text": "a", "words": [],
                 "reasons": ["rule"]}]
    merged = cand_mod.merge_nominations(
        existing, [{"start": 200.0, "end": 235.0, "reasons": ["llm_nominated"]}],
        _tr(), min_s=15.0, max_s=90.0)
    assert len(merged) == 2
    added = [c for c in merged if "llm_nominated" in c["reasons"]][0]
    assert added["start"] == 200.0
    assert added["text"], "a merged nomination must carry its transcript"
    assert added["words"], "...and its words, or refinement has nothing to work with"


def test_a_nomination_of_a_moment_already_covered_is_not_duplicated():
    existing = [{"start": 100.0, "end": 140.0, "text": "a", "words": [],
                 "reasons": ["rule"]}]
    merged = cand_mod.merge_nominations(
        existing, [{"start": 101.0, "end": 139.0}], _tr(), min_s=15.0, max_s=90.0)
    assert len(merged) == 1


def test_nominations_are_never_substituted_for_the_rule_based_set():
    """Union, not replacement: a small model nominates roughly the same
    obvious moments the scorer already finds, so filtering with it would throw
    away the non-obvious picks the judging pass is paid to find."""
    existing = [{"start": float(i) * 50, "end": float(i) * 50 + 40, "text": "a",
                 "words": [], "reasons": ["rule"]} for i in range(4)]
    merged = cand_mod.merge_nominations(existing, [], _tr(), min_s=15.0, max_s=90.0)
    assert len(merged) == len(existing)


def test_an_out_of_bounds_nomination_is_rejected():
    existing = [{"start": 0.0, "end": 40.0, "text": "a", "words": [], "reasons": []}]
    merged = cand_mod.merge_nominations(
        existing, [{"start": 100.0, "end": 103.0}], _tr(), min_s=15.0, max_s=90.0)
    assert len(merged) == 1


# ── failure must never take the run down ─────────────────────────────────────


async def test_no_engine_available_means_no_nominations_not_an_exception(monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("Ollama not reachable")

    monkeypatch.setattr("services.descriptions._call_llm", boom)
    out = await llm_select.nominate([{"start": 0.0, "text": "hello there"}], 100.0)
    assert out == []


async def test_no_engine_available_leaves_the_heuristic_ranking_alone(monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("no key")

    monkeypatch.setattr("services.descriptions._call_llm", boom)
    cands = [{"overall": 61.3, "text": "x", "start": 0.0}]
    assert await llm_select.judge(cands) == 0
    assert cands[0]["overall"] == 61.3


async def test_the_first_engine_that_answers_wins(monkeypatch):
    calls = []

    async def fake(engine, prompt, **kw):
        calls.append(engine)
        if engine == "ollama":
            raise RuntimeError("not running")
        return '[{"id": 0, "story_editor": "strong", "cold_viewer": "strong"}]'

    monkeypatch.setattr("services.descriptions._call_llm", fake)
    cands = [{"overall": 50.0, "text": "x", "start": 0.0}]
    assert await llm_select.judge(cands, engines=("ollama", "openai")) == 1
    assert calls == ["ollama", "openai"]
    assert cands[0]["llm_rank"] == 1


# ── comparative ranking ──────────────────────────────────────────────────────


def test_ranking_uses_the_whole_scale_where_scoring_compressed_it():
    """Measured on 46 real candidates, absolute scoring answered with eight
    distinct values and, on a quiet source, everything between 5 and 30. A
    forced ordering cannot compress."""
    cands = [{"overall": 50.0} for _ in range(10)]
    verdicts = [{"id": i} for i in range(10)]
    assert llm_select.apply_ranking(cands, verdicts, weight=1.0) == 10
    scores = [c["llm_score"] for c in cands]
    assert scores[0] == 100.0 and scores[-1] == 0.0
    assert max(scores) - min(scores) == 100.0


def test_rank_order_survives_into_the_score():
    cands = [{"overall": 50.0} for _ in range(4)]
    # The model ranks candidate 3 best and candidate 0 worst.
    llm_select.apply_ranking(cands, [{"id": 3}, {"id": 1}, {"id": 2}, {"id": 0}],
                             weight=1.0)
    assert cands[3]["overall"] > cands[1]["overall"] > cands[0]["overall"]
    assert cands[3]["llm_rank"] == 1 and cands[0]["llm_rank"] == 4


def test_a_cold_viewer_verdict_moves_a_clip_inside_its_neighbourhood():
    """A short lives or dies on the cold viewer, so that perspective carries
    more than the story editor's craft judgement."""
    warm = [{"overall": 50.0}, {"overall": 50.0}]
    cold = [{"overall": 50.0}, {"overall": 50.0}]
    llm_select.apply_ranking(warm, [{"id": 0, "cold_viewer": "strong"},
                                    {"id": 1}], weight=1.0)
    llm_select.apply_ranking(cold, [{"id": 0, "cold_viewer": "weak"},
                                    {"id": 1}], weight=1.0)
    assert warm[0]["llm_score"] > cold[0]["llm_score"]


def test_the_brutal_editor_costs_a_clip_but_cannot_veto_it():
    """The ranking already saw the same clip; a reject reason is a second
    opinion, not an override."""
    cands = [{"overall": 50.0} for _ in range(3)]
    llm_select.apply_ranking(cands, [
        {"id": 0, "critic": ["no_payoff", "late_hook"]},
        {"id": 1, "critic": []},
        {"id": 2, "critic": ["made_up_reason"]},
    ], weight=1.0)
    assert cands[0]["llm_score"] < 100.0, "two reject reasons cost nothing"
    assert cands[0]["llm_score"] > 0.0, "a reject reason must not be a veto"
    assert cands[0]["llm_verdict"]["reject_reasons"] == ["no_payoff", "late_hook"]
    assert cands[2]["llm_verdict"]["reject_reasons"] == [], "invented reason kept"


def test_the_verdict_is_recorded_for_debugging():
    cands = [{"overall": 50.0}]
    llm_select.apply_ranking(cands, [{"id": 0, "story_editor": "strong",
                                      "cold_viewer": "medium",
                                      "critic": ["fans_only"],
                                      "why": "needs the stream"}])
    v = cands[0]["llm_verdict"]
    assert v["story_editor"] == "strong" and v["cold_viewer"] == "medium"
    assert v["prompt_version"] == llm_select.JUDGE_PROMPT_VERSION
    assert cands[0]["llm_reason"] == "needs the stream"


def test_a_candidate_the_judge_left_out_does_not_win_by_default():
    """Measured: the model shortlisted 9 of 42 and the other 33 kept an
    unblended heuristic around 58, so a candidate it declined to rank beat one
    it had ranked fourth. Leaving one out IS a verdict."""
    cands = [{"overall": 58.0} for _ in range(5)]
    llm_select.apply_ranking(cands, [{"id": 0}, {"id": 1}], weight=0.7)
    ranked_last = cands[1]["overall"]
    for i in (2, 3, 4):
        assert cands[i]["llm_score"] == 0.0
        assert cands[i]["overall"] <= ranked_last


def test_nothing_is_touched_when_the_judge_said_nothing_usable():
    cands = [{"overall": 58.0} for _ in range(3)]
    assert llm_select.apply_ranking(cands, [{"no_id": 1}]) == 0
    assert all(c["overall"] == 58.0 for c in cands)
    assert all("llm_score" not in c for c in cands)


def test_an_out_of_range_id_is_ignored_not_crashed_on():
    cands = [{"overall": 50.0}]
    assert llm_select.apply_ranking(cands, [{"id": 7}, {"id": 0}]) == 1


async def test_a_model_that_scores_instead_of_ranking_still_counts(monkeypatch):
    """Falling back beats discarding the call."""
    async def fake(engine, prompt, **kw):
        return '[{"id": 0, "score": 88}]'

    monkeypatch.setattr("services.descriptions._call_llm", fake)
    cands = [{"overall": 50.0, "text": "x", "start": 0.0}]
    assert await llm_select.judge(cands, engines=("openai",)) == 1
    assert cands[0]["llm_score"] == 88.0


async def test_the_judge_sees_the_best_candidates_not_the_earliest(monkeypatch):
    """`cands` is in timeline order. Slicing it handed the judge the opening
    minutes and nothing else: measured on a 4-hour stream with 925 candidates
    it saw about the first twenty minutes, while everything later kept an
    unjudged heuristic score and won on it."""
    seen = {}

    async def fake(engine, prompt, **kw):
        seen["prompt"] = prompt
        return "[]"

    monkeypatch.setattr("services.descriptions._call_llm", fake)
    from services.clipper import llm_judge

    # Timeline order, with the good ones at the end.
    cands = [{"start": float(i), "end": float(i) + 30.0, "text": f"clip {i}",
              "overall": float(i)} for i in range(llm_judge.MAX_JUDGE_CLIPS + 40)]
    await llm_judge.judge(cands, engines=("openai",))
    assert f"clip {len(cands) - 1}" in seen["prompt"], "the best clip was not judged"
    assert "clip 0" not in seen["prompt"], "the worst clip took a slot"


def test_the_prompt_carries_only_the_rubrics_in_play():
    """A FUNNY clip must not be marked down for lacking stakes, and a CLUTCH
    one must not be excused for lacking them."""
    prompt = llm_select.judge_prompt(
        [{"text": "a", "start": 0.0, "story": {"archetypes": ["FUNNY"]}}], want=3)
    assert "FUNNY: setup" in prompt
    assert "CLUTCH" not in prompt
    # Comparative, not absolute: the question is which of these deserve the
    # slots, and leaving one out has to read as a verdict.
    assert "only 3 of them could be published" in prompt
    assert "IN RANK ORDER" in prompt
    assert "leave out" in prompt
    for word in ("story_editor", "cold_viewer", "critic"):
        assert word in prompt
