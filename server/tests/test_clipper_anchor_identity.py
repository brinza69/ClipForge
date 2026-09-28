"""One story finding keeps one source-scoped identity through every edit."""

import pytest

from services.clipper import anchor_identity, candidate_groups, reasoning_trace
from services.clipper.story import variants_from_anchor


def _anchor(**over):
    return {
        "payoff_t": 100.0,
        "payoff_quote": "That was the turn!",
        "why": "the setup pays off",
        "archetypes": ["STORY"],
        "required_context": [{"t": 80.0, "fact": "the setup"}],
        "hook_t": 90.0,
        **over,
    }


def test_identity_is_source_scoped_and_ignores_confidence_and_provenance():
    first = _anchor(confidence=0.2, prompt_version="old")
    second = _anchor(confidence=0.9, prompt_version="new")

    assert anchor_identity.identity(first, "source-a") == \
        anchor_identity.identity(second, "source-a")
    assert anchor_identity.identity(first, "source-a") != \
        anchor_identity.identity(first, "source-b")


def test_two_semantically_different_anchors_at_one_time_do_not_collapse():
    first = anchor_identity.identity(_anchor(why="first turn"), "source")
    second = anchor_identity.identity(_anchor(why="second turn"), "source")

    assert first != second


@pytest.mark.asyncio
async def test_the_detector_assigns_the_id_before_candidates_exist(monkeypatch):
    from services.clipper import llm_select

    async def fake(_engine, _prompt, **_kwargs):
        return ('[{"payoff_t": 5, "payoff_quote": "that was the turn", '
                '"why": "the setup pays off", "archetypes": ["STORY"]}]')

    monkeypatch.setattr("services.descriptions._call_llm", fake)
    segments = [{"start": 0.0, "end": 10.0, "text": "that was the turn"}]

    first = await llm_select.detect_anchors(
        segments, 10.0, engines=("openai",))
    second = await llm_select.detect_anchors(
        segments, 10.0, engines=("openai",))

    assert first[0]["anchor_id"].startswith("a1-")
    assert second[0]["anchor_id"] == first[0]["anchor_id"]


def test_every_boundary_variant_carries_the_same_anchor_id():
    anchor = anchor_identity.assign([_anchor()], "source")[0]
    variants = variants_from_anchor(
        anchor, 108.0, lo=10.0, hi=60.0, ceiling=200.0)

    assert len(variants) >= 2
    assert {variant["anchor_id"] for variant in variants} == {anchor["anchor_id"]}
    assert {variant["story"]["anchor_id"] for variant in variants} == {
        anchor["anchor_id"]}


def test_census_counts_anchors_not_payoff_buckets_when_ids_exist():
    candidates = []
    for index, why in enumerate(("first", "second")):
        anchor = anchor_identity.assign([_anchor(why=why)], "source")[0]
        candidates.append({
            "start": float(index * 70), "end": float(index * 70 + 30),
            "overall": 50.0, "text": f"distinct words {why}",
            "anchor_id": anchor["anchor_id"],
            "story": {"anchor_id": anchor["anchor_id"], "payoff_t": 100.0,
                      "grounding": {"payoff": True}},
        })

    census = candidate_groups.story_census(
        candidate_groups.build_groups(candidates), duration=1000.0)

    assert census["story_payoffs"] == 2
    assert census["story_payoffs_grounded"] == 2


def test_selection_trace_exposes_both_anchor_and_group_identity():
    candidate = {
        "start": 1.0, "end": 10.0, "overall": 50.0,
        "anchor_id": "a1-example", "moment_id": "moment-example",
        "story": {"anchor_id": "a1-example"},
    }

    trace = reasoning_trace.build_selection_trace(
        [candidate], mode="story_v2_shadow")

    assert trace["entries"][0]["anchor_id"] == "a1-example"
    assert trace["entries"][0]["moment_id"] == "moment-example"
