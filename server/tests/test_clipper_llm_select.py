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
        return '[{"id": 0, "score": 88}]'

    monkeypatch.setattr("services.descriptions._call_llm", fake)
    cands = [{"overall": 50.0, "text": "x", "start": 0.0}]
    assert await llm_select.judge(cands, engines=("ollama", "openai")) == 1
    assert calls == ["ollama", "openai"]
    assert cands[0]["overall"] == 69.0
