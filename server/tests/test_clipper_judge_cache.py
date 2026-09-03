"""The comparative judge is paid once for an identical question."""

from __future__ import annotations

import pytest

from services.clipper import judge_cache


def _candidates(text: str = "moment") -> list[dict]:
    return [{"overall": 50.0, "start": 1.0, "text": text}]


def test_malformed_or_duplicate_rounds_are_not_partially_salvaged():
    state = {
        "judge_cache_version": judge_cache.JUDGE_CACHE_VERSION,
        "rounds": [
            {"round": 0, "prompt_fingerprint": "a", "status": "usable",
             "verdicts": [{"id": 0}], "provenance": {}},
            {"round": 0, "prompt_fingerprint": "b", "status": "usable",
             "verdicts": [{"id": 0}], "provenance": {}},
        ],
    }

    prepared = judge_cache.prepare(state)

    assert prepared.reused is False and prepared.reason == judge_cache.MALFORMED
    assert prepared.state["rounds"] == []


def test_a_claimed_usable_row_needs_an_applicable_verdict():
    prompt = "same question"
    state = judge_cache.record(
        judge_cache.prepare().state, 0, prompt, usable=True,
        verdicts=[{"id": 9}], provenance={})

    assert judge_cache.reusable(
        state, 0, prompt, candidate_count=1) is None
    state["rounds"][0]["verdicts"] = []
    assert judge_cache.prepare(state).reason == judge_cache.MALFORMED


@pytest.mark.asyncio
async def test_an_identical_judge_question_reuses_raw_verdicts(monkeypatch):
    from services.clipper import llm_judge

    calls = []

    async def fake(engine, _prompt, **_kwargs):
        calls.append(engine)
        return '[{"id": 0, "story_editor": "strong", "cold_viewer": "strong"}]'

    monkeypatch.setattr("services.descriptions._call_llm", fake)
    saved = []
    state = judge_cache.prepare().state
    first = _candidates()
    assert await llm_judge.judge(
        first, engines=("openai",), cache_state=state,
        checkpoint=lambda updated: saved.append(updated)) == 1
    checkpoint = saved[-1]

    async def must_not_run(*_args, **_kwargs):
        raise AssertionError("an identical judge prompt must be reused")

    monkeypatch.setattr("services.descriptions._call_llm", must_not_run)
    second = _candidates()
    assert await llm_judge.judge(
        second, engines=("openai",), cache_state=checkpoint) == 1

    assert calls == ["openai"]
    assert second[0]["llm_rank"] == first[0]["llm_rank"] == 1
    row = checkpoint["rounds"][0]
    assert row["status"] == judge_cache.USABLE
    assert row["provenance"]["provider"] == "openai"
    assert row["provenance"]["nondeterministic"] is True


@pytest.mark.asyncio
async def test_an_unusable_judge_answer_is_retried(monkeypatch):
    from services.clipper import llm_judge, reasoning_trace

    phase = {"value": 1}

    async def fake(_engine, _prompt, **_kwargs):
        if phase["value"] == 1:
            return "[]"
        return '[{"id": 0, "story_editor": "medium"}]'

    monkeypatch.setattr("services.descriptions._call_llm", fake)
    saved = []
    trace = reasoning_trace.RunTrace("p", mode="story_v2_shadow")
    assert await llm_judge.judge(
        _candidates(), engines=("openai",), trace=trace,
        cache_state=judge_cache.prepare().state,
        checkpoint=lambda updated: saved.append(updated)) == 0
    first = saved[-1]

    assert first["rounds"][0]["status"] == judge_cache.UNUSABLE
    assert trace.outcome() == "failed_non_blocking"

    phase["value"] = 2
    saved.clear()
    assert await llm_judge.judge(
        _candidates(), engines=("openai",), cache_state=first,
        checkpoint=lambda updated: saved.append(updated)) == 1
    assert saved[-1]["rounds"][0]["status"] == judge_cache.USABLE


@pytest.mark.asyncio
async def test_a_parse_failure_recovered_by_fallback_is_not_a_partial_run(
        monkeypatch):
    from services.clipper import llm_judge, reasoning_trace

    async def fake(engine, _prompt, **_kwargs):
        if engine == "first":
            return "not json"
        return '[{"id": 0, "story_editor": "strong"}]'

    monkeypatch.setattr("services.descriptions._call_llm", fake)
    trace = reasoning_trace.RunTrace("p", mode="story_v2_shadow")

    assert await llm_judge.judge(
        _candidates(), engines=("first", "second"), trace=trace) == 1
    assert [row["parsed"] for row in trace.results] == [False, True]
    assert trace.unusable == []
    assert trace.parse_fallbacks == [{
        "stage": "judge", "request": "judge#0:round0",
        "unusable_answers": 1,
    }]
    assert trace.outcome() == "fallback"


@pytest.mark.asyncio
async def test_a_changed_prompt_is_not_a_cache_hit(monkeypatch):
    from services.clipper import llm_judge

    calls = []

    async def fake(engine, _prompt, **_kwargs):
        calls.append(engine)
        return '[{"id": 0}]'

    monkeypatch.setattr("services.descriptions._call_llm", fake)
    saved = []
    await llm_judge.judge(
        _candidates(), engines=("openai",),
        cache_state=judge_cache.prepare().state,
        checkpoint=lambda updated: saved.append(updated))
    await llm_judge.judge(
        _candidates("different"), engines=("openai",),
        cache_state=saved[-1], checkpoint=lambda _updated: None)

    assert calls == ["openai", "openai"]


@pytest.mark.asyncio
async def test_the_worker_checkpoints_and_reuses_a_judge_round(monkeypatch):
    """The resumable function is wired into the path production calls."""
    from services.clipper import reasoning_cache, reasoning_trace, storage
    from workers import clipper_judging

    project_id = "judge-cache-worker"
    base = reasoning_cache.base_inputs({"size": 1}, {"text": "same"})
    calls = []

    async def fake(engine, _prompt, **_kwargs):
        calls.append(engine)
        return '[{"id": 0, "story_editor": "strong"}]'

    def pool(refined, _duration, _trace, **_kwargs):
        return {
            "pool": [refined[0]],
            "all_groups": [],
            "selected_groups": [{"moment_id": "m1"}],
        }

    class Queue:
        def is_cancelled(self, _job_id):
            return False

        async def update_progress(self, *_args):
            raise AssertionError("one selected moment should finish round one")

    monkeypatch.setattr("services.descriptions._call_llm", fake)
    monkeypatch.setattr(clipper_judging, "_judge_pool", pool)
    monkeypatch.setattr(
        "services.clipper.candidate_groups.propagate_verdicts",
        lambda *_args, **_kwargs: None)

    first = _candidates()
    assert await clipper_judging._judge_rounds(
        first, 10.0, 1, {"llm_judge_model": "judge-test"},
        reasoning_trace.RunTrace(project_id, mode="story_v2_shadow"),
        Queue(), "job", project_id=project_id, cache_base=base)
    assert first[0]["llm_rank"] == 1
    assert storage.read_artifact(project_id, "judge") is not None

    async def must_not_run(*_args, **_kwargs):
        raise AssertionError("the worker must load its stored judge round")

    monkeypatch.setattr("services.descriptions._call_llm", must_not_run)
    second = _candidates()
    trace = reasoning_trace.RunTrace(project_id, mode="story_v2_shadow")
    assert await clipper_judging._judge_rounds(
        second, 10.0, 1, {"llm_judge_model": "judge-test"}, trace,
        Queue(), "job", project_id=project_id, cache_base=base)

    assert len(calls) == 1
    assert second[0]["llm_rank"] == 1
    assert trace.counts["judge_rounds_reused"] == 1


@pytest.mark.asyncio
async def test_a_checkpoint_failure_does_not_recast_a_verdict_as_failure(
        monkeypatch):
    from services.clipper import reasoning_cache, reasoning_trace
    from workers import clipper_cache, clipper_judging

    async def fake(_engine, _prompt, **_kwargs):
        return '[{"id": 0, "story_editor": "strong"}]'

    def pool(refined, _duration, _trace, **_kwargs):
        return {"pool": [refined[0]], "all_groups": [],
                "selected_groups": [{"moment_id": "m1"}]}

    class Queue:
        def is_cancelled(self, _job_id):
            return False

        async def update_progress(self, *_args):
            raise AssertionError("one selected moment should finish round one")

    monkeypatch.setattr("services.descriptions._call_llm", fake)
    monkeypatch.setattr(clipper_judging, "_judge_pool", pool)
    monkeypatch.setattr(
        "services.clipper.candidate_groups.propagate_verdicts",
        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(clipper_cache, "_cached", lambda *_args: None)

    def fail_write(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(clipper_cache, "_cache", fail_write)
    candidate = _candidates()
    trace = reasoning_trace.RunTrace("p", mode="story_v2_shadow")
    base = reasoning_cache.base_inputs({"size": 1}, {"text": "same"})

    assert await clipper_judging._judge_rounds(
        candidate, 10.0, 1, {}, trace, Queue(), "job",
        project_id="p", cache_base=base)
    assert candidate[0]["llm_rank"] == 1
    assert trace.errors == [{"stage": "judge_cache", "error": "disk full"}]
