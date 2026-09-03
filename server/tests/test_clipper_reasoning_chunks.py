"""One bad model response must not erase or repay its good neighbours."""

from __future__ import annotations

import pytest

from services.clipper import reasoning_chunks as chunks


def _plan():
    return [
        {"index": 0, "t_start": 0.0, "t_end": 10.0,
         "chars": 4, "lines": "zero"},
        {"index": 1, "t_start": 9.0, "t_end": 20.0,
         "chars": 3, "lines": "one"},
    ]


def test_a_fresh_plan_names_every_chunk_as_pending():
    prepared = chunks.prepare("anchors", _plan())

    assert prepared.reused is False and prepared.reason == chunks.NEW
    assert chunks.counts(prepared.state) == {
        chunks.PENDING: 2, chunks.USABLE: 0, chunks.UNUSABLE: 0}


def test_an_empty_answer_is_usable_and_reusable():
    plan = _plan()
    state = chunks.prepare("anchors", plan).state
    state = chunks.record(state, plan[0], usable=True, items=[],
                          provenance={"provider": "ollama"})

    assert chunks.reusable(state, plan[0]) == []
    assert chunks.reusable(state, plan[1]) is None


def test_an_unusable_chunk_is_retried_without_losing_the_good_one():
    plan = _plan()
    state = chunks.prepare("anchors", plan).state
    state = chunks.record(state, plan[0], usable=True, items=[{"t": 1}],
                          provenance={"provider": "openai"})
    state = chunks.record(state, plan[1], usable=False, items=[{"t": 11}],
                          provenance={"provider": None})
    resumed = chunks.prepare("anchors", plan, state)

    assert resumed.reused is True
    assert chunks.reusable(resumed.state, plan[0]) == [{"t": 1}]
    assert chunks.reusable(resumed.state, plan[1]) is None
    assert chunks.items(resumed.state) == [{"t": 1}]


def test_a_changed_slice_refuses_the_whole_nested_state():
    plan = _plan()
    state = chunks.prepare("anchors", plan).state
    state = chunks.record(state, plan[0], usable=True, items=[{"t": 1}],
                          provenance={})
    changed = _plan()
    changed[1]["lines"] = "different"

    prepared = chunks.prepare("anchors", changed, state)

    assert prepared.reused is False
    assert prepared.reason == chunks.PLAN_CHANGED
    assert chunks.items(prepared.state) == []


def test_a_malformed_row_is_not_filtered_and_shifted_onto_the_next_chunk():
    plan = _plan()
    state = chunks.prepare("anchors", plan).state
    state["chunks"][0] = "bad"

    prepared = chunks.prepare("anchors", plan, state)

    assert prepared.reused is False and prepared.reason == chunks.MALFORMED
    assert len(prepared.state["chunks"]) == len(plan)


def test_record_does_not_mutate_the_previous_checkpoint():
    plan = _plan()
    before = chunks.prepare("anchors", plan).state
    after = chunks.record(before, plan[0], usable=True, items=[1], provenance={})

    assert chunks.counts(before)[chunks.PENDING] == 2
    assert chunks.counts(after)[chunks.USABLE] == 1


def test_chunk_identity_does_not_assume_the_index_is_a_list_position():
    plan = _plan()
    plan[0]["index"] = 10
    plan[1]["index"] = 20
    state = chunks.prepare("anchors", plan).state
    state = chunks.record(state, plan[1], usable=True, items=[{"t": 11}],
                          provenance={})

    assert chunks.reusable(state, plan[1]) == [{"t": 11}]
    assert chunks.reusable(state, plan[0]) is None


def test_duplicate_chunk_indices_are_refused_before_any_answer_can_shift():
    plan = _plan()
    plan[1]["index"] = plan[0]["index"]

    with pytest.raises(ValueError, match="duplicate indices"):
        chunks.prepare("anchors", plan)


def test_complete_requires_every_chunk_to_be_usable():
    plan = _plan()
    state = chunks.prepare("anchors", plan).state
    assert chunks.complete(state) is False
    for chunk in plan:
        state = chunks.record(state, chunk, usable=True, items=[], provenance={})
    assert chunks.complete(state) is True


@pytest.mark.asyncio
async def test_json_result_names_the_provider_and_both_response_hashes(monkeypatch):
    from services.clipper import llm_engine

    async def fake(engine, _prompt, **_kwargs):
        return "not json" if engine == "ollama" else '[{"t": 1}]'

    monkeypatch.setattr("services.descriptions._call_llm", fake)
    answer = await llm_engine._ask_json_result(
        ("ollama", "openai"), "prompt", keys=("t",))

    assert answer.usable is True and answer.provider == "openai"
    assert answer.prompt_fingerprint and answer.response_fingerprint
    assert [row["engine"] for row in answer.attempts] == ["ollama", "openai"]
    assert all(row["response_fingerprint"] for row in answer.attempts)
    assert [row["parsed"] for row in answer.attempts] == [False, True]
    assert answer.provenance()["nondeterministic"] is True


@pytest.mark.asyncio
async def test_a_non_string_provider_answer_falls_back_instead_of_raising(
        monkeypatch):
    from services import descriptions
    from services.clipper import llm_engine

    async def fake(engine, _prompt, **_kwargs):
        return {"not": "text"} if engine == "ollama" else '[]'

    monkeypatch.setattr("services.descriptions._call_llm", fake)
    answer = await llm_engine._ask_json_result(
        ("ollama", "openai"), "prompt", model="", keys=("t",))

    assert answer.usable is True and answer.provider == "openai"
    assert [row["answered"] for row in answer.attempts] == [False, True]
    assert answer.attempts[1]["model"] == descriptions.DEFAULT_OPENAI_MODEL


@pytest.mark.asyncio
async def test_a_structural_answer_with_no_valid_anchor_stays_retryable(
        monkeypatch):
    from services.clipper import llm_select, reasoning_trace

    segments = [{"start": 0.0, "end": 10.0, "text": "words"}]

    async def fake(_engine, _prompt, **_kwargs):
        return '[{"payoff_t": "not a time"}]'

    monkeypatch.setattr("services.descriptions._call_llm", fake)
    saved = []
    trace = reasoning_trace.RunTrace("p1", mode="story_v2_shadow")
    anchors = await llm_select.detect_anchors(
        segments, 10.0, engines=("ollama",),
        trace=trace, checkpoint=lambda state: saved.append(state))

    assert anchors == []
    assert chunks.counts(saved[-1])[chunks.UNUSABLE] == 1
    assert trace.incomplete_chunks == {"anchors_chunks_unusable": 1}
    assert trace.outcome() == "failed_non_blocking"


@pytest.mark.asyncio
async def test_anchor_rerun_calls_only_the_chunk_that_was_unusable(monkeypatch):
    from services.clipper import chunking, llm_select

    far = chunking.MAX_CHUNK_SECONDS + 600.0
    segments = [
        {"start": 0.0, "end": 10.0, "text": "EARLY_MARKER"},
        {"start": far, "end": far + 10.0, "text": "LATE_MARKER"},
    ]
    phase = {"value": 1}
    calls = []

    async def fake(engine, prompt, **_kwargs):
        marker = "late" if "LATE_MARKER" in prompt else "early"
        calls.append((phase["value"], marker, engine))
        if marker == "late" and phase["value"] == 1:
            return "not json"
        payoff = far + 5.0 if marker == "late" else 5.0
        return (f'[{{"payoff_t": {payoff}, "payoff_strength": 0.8, '
                '"archetypes": ["STORY"], "required_context": [], '
                '"unresolved_context": [], "confidence": 0.8}]')

    monkeypatch.setattr("services.descriptions._call_llm", fake)
    saved = []
    first = await llm_select.detect_anchors(
        segments, far + 20.0, checkpoint=lambda state: saved.append(state))
    first_state = saved[-1]

    assert len(first) == 1
    assert chunks.counts(first_state) == {
        chunks.PENDING: 0, chunks.USABLE: 1, chunks.UNUSABLE: 1}

    phase["value"] = 2
    saved.clear()
    second = await llm_select.detect_anchors(
        segments, far + 20.0, chunk_cache=first_state,
        checkpoint=lambda state: saved.append(state))

    assert len(second) == 2
    assert [(marker, engine) for run, marker, engine in calls if run == 2] == [
        ("late", "ollama")]
    assert chunks.complete(saved[-1]) is True
    assert saved[-1]["chunks"][0]["provenance"]["provider"] == "ollama"


@pytest.mark.asyncio
async def test_promise_rerun_also_calls_only_the_unusable_chunk(monkeypatch):
    from services.clipper import chunking, promises

    far = chunking.MAX_CHUNK_SECONDS + 600.0
    segments = [
        {"start": 0.0, "end": 10.0, "text": "EARLY_PROMISE"},
        {"start": far, "end": far + 10.0, "text": "LATE_PROMISE"},
    ]
    phase = {"value": 1}
    calls = []

    async def fake(engine, prompt, **_kwargs):
        marker = "late" if "LATE_PROMISE" in prompt else "early"
        calls.append((phase["value"], marker, engine))
        if marker == "late" and phase["value"] == 1:
            return '[{"t": "bad", "text": "setup"}]'
        when = far + 5.0 if marker == "late" else 5.0
        return (f'[{{"t": {when}, "kind": "prediction", '
                '"text": "this will happen", "confidence": 0.8}]')

    monkeypatch.setattr("services.descriptions._call_llm", fake)
    saved = []
    first = await promises.detect(
        segments, far + 20.0, engines=("ollama",),
        checkpoint=lambda state: saved.append(state))
    first_state = saved[-1]

    assert len(first) == 1
    assert chunks.counts(first_state)[chunks.UNUSABLE] == 1

    phase["value"] = 2
    saved.clear()
    second = await promises.detect(
        segments, far + 20.0, engines=("ollama",), chunk_cache=first_state,
        checkpoint=lambda state: saved.append(state))

    assert len(second) == 2
    assert [(marker, engine) for run, marker, engine in calls if run == 2] == [
        ("late", "ollama")]
    assert chunks.complete(saved[-1]) is True
