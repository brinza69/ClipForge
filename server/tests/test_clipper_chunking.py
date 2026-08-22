"""How a long stream is handed to a model, and what the run can prove it read.

The measurement behind this file. On the four-hour audited source, `chunk_lines`
split on characters alone and produced TWO chunks: one covering 3h21m and one
covering 38m. Both got the same fixed quota of anchors, so a stretch six times
longer had to be six times more selective to fit — and nothing on disk said so.
Every payoff the run found landed after 2h57m, which is not evidence that the
first three hours were empty, only that nothing could show whether they were.

Two silent losses sat on top: `transcript_lines` truncated at 600,000 characters
without a word, and no artefact recorded which parts of the source were read.
"""

from __future__ import annotations

import pytest

from services.clipper import chunking


def _items(*spans, chars: int = 40):
    """Line items `chars` long each, at the given (start, end) spans."""
    return [{"start": float(s), "end": float(e), "line": f"[{int(s)}] " + "w" * chars}
            for s, e in spans]


def _even(count: int, step: float, *, chars: int = 40):
    return _items(*[(i * step, i * step + step) for i in range(count)], chars=chars)


# ── The clock is a bound, not just the byte count ────────────────────────────


def test_a_long_quiet_stretch_is_split_by_time_not_by_size():
    """The defect, in one test. Four hours of sparse dialogue is a few thousand
    characters — well under any byte budget — and used to arrive as one chunk."""
    items = _even(48, 300.0)          # 4 hours, one short line every 5 minutes
    chunks = chunking.plan_chunks(items)
    assert len(chunks) >= 6, f"four hours became {len(chunks)} chunk(s)"
    for chunk in chunks:
        span = chunk["t_end"] - chunk["t_start"]
        assert span <= chunking.MAX_CHUNK_SECONDS + 1.0


def test_a_dense_stretch_is_still_split_by_size():
    """The clock is not the only bound: a talk-heavy ten minutes can exceed the
    character budget on its own."""
    items = _even(200, 3.0, chars=400)   # 10 minutes, ~80k chars
    chunks = chunking.plan_chunks(items)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert chunk["chars"] <= chunking.MAX_CHUNK_CHARS + 500


def test_a_short_source_is_one_chunk():
    """A source that fits has no boundary problem, and inventing one would cost
    a second call for nothing."""
    assert len(chunking.plan_chunks(_even(10, 20.0))) == 1


def test_no_items_is_no_chunks():
    assert chunking.plan_chunks([]) == []
    assert chunking.plan_chunks(None) == []


def test_items_arrive_in_timeline_order_whatever_order_they_came_in():
    items = _items((100.0, 110.0), (0.0, 10.0), (50.0, 60.0))
    chunk = chunking.plan_chunks(items)[0]
    assert chunk["t_start"] == 0.0 and chunk["t_end"] == 110.0


# ── The overlap ──────────────────────────────────────────────────────────────


def test_consecutive_chunks_share_their_seam():
    """A payoff whose setup sits just before a boundary is otherwise split
    across two prompts and legible in neither."""
    chunks = chunking.plan_chunks(_even(48, 300.0))
    for left, right in zip(chunks, chunks[1:]):
        assert right["t_start"] < left["t_end"], "no overlap at the seam"


def test_an_event_on_a_boundary_is_whole_in_one_chunk():
    """The case the overlap exists for."""
    items = _even(48, 300.0)
    chunks = chunking.plan_chunks(items)
    seam = chunks[0]["t_end"]
    # Every item within the overlap of the seam appears in two chunks.
    both = [i for i in items
            if seam - chunking.OVERLAP_SECONDS < i["end"] <= seam]
    for item in both:
        holding = [c for c in chunks
                   if any(x["line"] == item["line"] for x in c["items"])]
        assert len(holding) >= 2, f"{item['line'][:12]} appears once"


def test_the_overlap_cannot_stall_the_planner():
    """Replaying the whole chunk would make no progress. With one item longer
    than the time budget that is exactly what a naive replay would do."""
    items = [{"start": 0.0, "end": 9999.0, "line": "[0] one very long line"},
             {"start": 9999.0, "end": 10000.0, "line": "[9999] next"}]
    chunks = chunking.plan_chunks(items)
    assert 1 <= len(chunks) <= 3


# ── Coverage is recorded, not assumed ────────────────────────────────────────


def test_a_fully_covered_source_reports_no_gaps():
    chunks = chunking.plan_chunks(_even(48, 300.0))
    cov = chunking.coverage(chunks, 14400.0)
    assert cov["gaps"] == []
    assert cov["share"] > 0.98


def test_a_hole_in_the_middle_is_reported():
    """A stretch with no transcript is a fact about the run. Before this, three
    hours could go unread and the only trace was that no clip came from there."""
    items = _items((0.0, 60.0), (7200.0, 7260.0))
    # Duration ends with the last line, so the only hole is the middle one.
    cov = chunking.coverage(chunking.plan_chunks(items), 7260.0)
    assert len(cov["gaps"]) == 1
    assert cov["gaps"][0]["start"] == 60.0 and cov["gaps"][0]["end"] == 7200.0


def test_a_tail_with_no_transcript_is_reported():
    """The silent truncation this replaces cut exactly here."""
    cov = chunking.coverage(chunking.plan_chunks(_items((0.0, 60.0))), 3600.0)
    assert cov["gaps"][-1]["end"] == 3600.0


def test_a_gap_shorter_than_a_breath_is_not_a_gap():
    """The space between two utterances is not a hole in the coverage."""
    cov = chunking.coverage(
        chunking.plan_chunks(_items((0.0, 10.0), (12.0, 20.0))), 20.0)
    assert cov["gaps"] == []


def test_coverage_of_nothing_is_not_a_crash():
    cov = chunking.coverage([], 100.0)
    assert cov["chunks"] == 0 and cov["gaps"] == []


# ── The quota follows the clock ──────────────────────────────────────────────


def test_a_longer_chunk_may_ask_for_more():
    """A fixed quota is what let 3h21m and 38m compete for the same ten slots.
    The long one had to be six times more selective and nothing said so."""
    long_chunk = {"t_start": 0.0, "t_end": 3600.0}
    short_chunk = {"t_start": 0.0, "t_end": 300.0}
    assert (chunking.quota_for(long_chunk, per_chunk=10)
            > chunking.quota_for(short_chunk, per_chunk=10))


def test_the_quota_never_exceeds_the_ceiling_it_was_given():
    huge = {"t_start": 0.0, "t_end": 100_000.0}
    assert chunking.quota_for(huge, per_chunk=10) == 10


def test_even_a_tiny_chunk_is_worth_asking_about():
    tiny = {"t_start": 0.0, "t_end": 5.0}
    assert chunking.quota_for(tiny, per_chunk=10) >= 3


# ── An answer is not an answer until it parses ───────────────────────────────


@pytest.mark.asyncio
async def test_an_unparseable_answer_falls_through_to_the_next_engine(monkeypatch):
    """`_ask` accepted the first non-empty STRING, and the caller parsed after
    the loop had already committed to that engine — so the fallback existed and
    could not be reached."""
    from services.clipper import llm_engine

    calls: list[str] = []

    async def fake(engine, prompt, **kw):
        calls.append(engine)
        return "I'm sorry, I can't help with that." if engine == "ollama" else '[{"t": 1}]'

    monkeypatch.setattr("services.descriptions._call_llm", fake)
    out = await llm_engine._ask_json(("ollama", "openai"), "p")
    assert out == [{"t": 1}]
    assert calls == ["ollama", "openai"]


@pytest.mark.asyncio
async def test_the_wrong_json_shape_is_not_an_answer_either(monkeypatch):
    from services.clipper import llm_engine

    async def fake(engine, prompt, **kw):
        return '{"not": "a list"}'

    monkeypatch.setattr("services.descriptions._call_llm", fake)
    assert await llm_engine._ask_json(("ollama",), "p") is None


@pytest.mark.asyncio
async def test_a_provider_that_never_answers_does_not_hold_the_run(monkeypatch):
    """The one failure the engine list cannot route around by itself."""
    import asyncio

    from services.clipper import llm_engine

    async def fake(engine, prompt, **kw):
        if engine == "ollama":
            await asyncio.sleep(30)
        return '[{"t": 1}]'

    monkeypatch.setattr("services.descriptions._call_llm", fake)
    out = await llm_engine._ask_json(("ollama", "openai"), "p", timeout=0.05)
    assert out == [{"t": 1}]


@pytest.mark.asyncio
async def test_cancellation_is_checked_between_engines(monkeypatch):
    from services.clipper import llm_engine

    async def fake(engine, prompt, **kw):
        return "not json"

    monkeypatch.setattr("services.descriptions._call_llm", fake)
    out = await llm_engine._ask_json(("ollama", "openai"), "p",
                                     is_cancelled=lambda: True)
    assert out is None


# ── The overlap's duplicates are collapsed ───────────────────────────────────


def test_the_same_moment_reported_from_both_sides_is_one_anchor():
    """The overlap exists so a moment on a seam is whole somewhere; the price
    is that the model reports it twice."""
    from services.clipper.llm_select import _dedupe_anchors

    out = _dedupe_anchors([
        {"payoff_t": 100.0, "confidence": 0.5, "chunk_index": 0},
        {"payoff_t": 101.5, "confidence": 0.9, "chunk_index": 1},
        {"payoff_t": 400.0, "confidence": 0.7, "chunk_index": 1},
    ])
    assert [a["payoff_t"] for a in out] == [101.5, 400.0]


def test_two_close_anchors_from_the_SAME_prompt_are_both_kept():
    """The model saw both and chose to name them separately. Merging them would
    silently discard a judgement — this function undoes an artefact of the
    overlap, it does not second-guess the model."""
    from services.clipper.llm_select import _dedupe_anchors

    out = _dedupe_anchors([{"payoff_t": 100.0, "chunk_index": 0},
                           {"payoff_t": 103.0, "chunk_index": 0}])
    assert len(out) == 2


def test_the_more_confident_report_wins_not_the_first_one():
    """Keeping the first would systematically prefer whichever side of the
    overlap happened to be read first."""
    from services.clipper.llm_select import _dedupe_anchors

    out = _dedupe_anchors([{"payoff_t": 100.0, "confidence": 0.9, "chunk_index": 0},
                           {"payoff_t": 100.5, "confidence": 0.2, "chunk_index": 1}])
    assert out[0]["confidence"] == 0.9


def test_two_genuinely_different_moments_are_not_merged():
    from services.clipper.llm_select import _dedupe_anchors

    out = _dedupe_anchors([{"payoff_t": 100.0, "chunk_index": 0},
                           {"payoff_t": 130.0, "chunk_index": 1}])
    assert len(out) == 2


def test_an_item_bigger_than_a_bound_gets_its_own_chunk_and_says_so():
    """It cannot be split — a line is the smallest thing with a timestamp — so
    the choice is a chunk that quietly breaks the stated limit, or one that
    admits it. Dropping it would be the silent tail loss this replaces."""
    items = _items((0.0, 30.0)) + [
        {"start": 30.0, "end": 30.0 + chunking.MAX_CHUNK_SECONDS * 2,
         "line": "[30] one unsplittable line"}]
    chunks = chunking.plan_chunks(items)
    over = [c for c in chunks if c["oversized"]]
    assert len(over) == 1
    assert over[0]["t_end"] - over[0]["t_start"] > chunking.MAX_CHUNK_SECONDS
    assert all(c["t_end"] - c["t_start"] <= chunking.MAX_CHUNK_SECONDS + 1.0
               for c in chunks if not c["oversized"])
    assert chunking.coverage(chunks, 0.0)["oversized"] == 1


@pytest.mark.asyncio
async def test_a_list_of_junk_does_not_stop_the_fallback(monkeypatch):
    """`[{"garbage": 1}]` is a list, so validating the container alone let it
    block the next provider while producing zero anchors."""
    from services.clipper import llm_engine

    calls: list[str] = []

    async def fake(engine, prompt, **kw):
        calls.append(engine)
        return '[{"garbage": 1}]' if engine == "ollama" else '[{"payoff_t": 9}]'

    monkeypatch.setattr("services.descriptions._call_llm", fake)
    out = await llm_engine._ask_json(("ollama", "openai"), "p",
                                     keys=("payoff_t",))
    assert out == [{"payoff_t": 9}]
    assert calls == ["ollama", "openai"]


@pytest.mark.asyncio
async def test_an_empty_list_is_a_real_answer(monkeypatch):
    """"Nothing here worth clipping" is what a quiet stretch should return.
    Retrying it on another provider buys nothing and costs a call."""
    from services.clipper import llm_engine

    calls: list[str] = []

    async def fake(engine, prompt, **kw):
        calls.append(engine)
        return "[]"

    monkeypatch.setattr("services.descriptions._call_llm", fake)
    assert await llm_engine._ask_json(("ollama", "openai"), "p",
                                      keys=("payoff_t",)) == []
    assert calls == ["ollama"]


def test_the_anchor_cache_notices_a_new_chunk_plan():
    """Answers produced under a different plan are not answers to the same
    question: the plan decides which stretches the model was ever shown."""
    from workers.clipper_build import _anchor_stamp

    stamp = _anchor_stamp({}, 100.0)
    assert "chunking" in stamp
    assert stamp["chunking"][0] == round(chunking.MAX_CHUNK_SECONDS, 1)
