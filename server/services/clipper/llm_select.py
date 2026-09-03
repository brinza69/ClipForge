"""
ClipForge — AI Stream Clipper: LLM judgement over candidate clips.

Why this exists, in one measurement. On a 12-minute IShowSpeed co-stream the
rule-based scorer put "let's cook our food, let's cook our diamonds" at #5 of
its top eight, and put this at #53 of 53:

    "Look to your left — it's a blast furnace." / "What do you mean, look to
    my left? How do you make a blast furnace?" / "You don't have that in the
    game." / "I'm lying, bro." / "Y'all just sat there and lied, bro."

Chat trolled him, he fell for it, he called them out. Setup, trap, punchline.
Dead last. The scorer judges audio energy, pacing and sentence structure, and
none of those can tell "naming my inventory" from "chat lied to me and I
noticed". That gap is what a language model closes.

TWO FILTERS THAT FAIL DIFFERENTLY
---------------------------------
`nominate()` reads the whole transcript on a CHEAP model and proposes moments.
Its picks are UNIONED with the rule-based candidates, never substituted for
them. That is the whole design: a small model nominates roughly the same
obvious moments the scorer already finds, so using it as a filter would throw
away exactly the non-obvious picks the expensive model is being paid to judge.
Two independent recalls, one judgement pass.

`judge()` scores that union on a FRONTIER model and returns 0..100 per clip.

USE A FRONTIER MODEL FOR THE JUDGING. Measured on the same 46 candidates:
gpt-4o-mini answered almost everything 50, 40 or 10 with reasons like
"Excitement about discovery", and moved the good clip from #45 only to #36.
A frontier model spread its scores over eight values, sank pure narration to
the bottom on its own, and moved the same clip to #4. Nomination is bulk
reading and a small model is fine at it; judging is taste and it is not.

The models are not deterministic between runs — the same 46 candidates were
scored twice and the ordering moved. That is a second reason the verdict is
blended with the heuristic rather than replacing it: the heuristic is stable.

COST, measured with tiktoken on real transcripts (90..395 tokens/minute across
19 of them): 3.7 cents for a 12-hour gaming stream, 11.1 for a talk-heavy one.
Frontier OUTPUT is the dominant term — 10 $/1M against 2.50 for input — which
is why `judge` asks for `id: score` and one short reason, never prose.

Everything here degrades to None rather than raising. A clipper run must not
fail because Ollama is down or a key expired; it falls back to the heuristic
ranking, which is what shipped before this module existed.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Sequence

from services.clipper import chunking

logger = logging.getLogger("clipforge.clipper.llm_select")

# Engines tried in order. Local first: pass 1 is bulk reading, which is what a
# small local model is good at and what an API charges most for.
NOMINATE_ENGINES = ("ollama", "openai")

MAX_TRANSCRIPT_CHARS = 600_000    # ~150k tokens; longer sources are chunked
CHUNK_CHARS = 120_000             # ~30k tokens per nomination chunk

# Reaching a model, and recording every attempt, lives in llm_engine.py.
# Re-exported because llm_judge, promises and the tests all import them from
# here and the split is not meant to be visible to them.
from services.clipper.llm_engine import (  # noqa: E402,F401
    JUDGE_ENGINES, MAX_CLIP_CHARS, MAX_JUDGE_CLIPS,
    _ask, _ask_json, _ask_json_result, _note, _note_chunk, _note_result,
    _num, parse_json,
)


# --------------------------------------------------------------------------
# pure helpers — no network, unit-testable
# --------------------------------------------------------------------------


def transcript_line_items(segments: Sequence[dict]) -> list[dict]:
    """`[seconds] text` per segment, each keeping its own start and end.

    The planner bounds a chunk by the clock as well as by characters, and once
    these are joined into one string the clock is gone.
    """
    out: list[dict] = []
    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        text = " ".join(str(seg.get("text") or "").split())
        if not text:
            continue
        out.append({
            "start": _num(seg.get("start")),
            "end": _num(seg.get("end")) or _num(seg.get("start")),
            "line": f"[{int(_num(seg.get('start')))}] {text}",
        })
    return out


def transcript_lines(segments: Sequence[dict], limit: int = MAX_TRANSCRIPT_CHARS) -> str:
    """`[seconds] text` per segment — the model needs a timestamp to point at.

    Truncates at `limit` and says nothing about it, which is the silent tail
    loss the chunk planner replaces.
    """
    out: list[str] = []
    total = 0
    for item in transcript_line_items(segments):
        total += len(item["line"]) + 1
        if total > limit:
            break
        out.append(item["line"])
    return "\n".join(out)


def chunk_lines(lines: str, size: int = CHUNK_CHARS) -> list[str]:
    """Split on line boundaries so no segment is cut in half."""
    if len(lines) <= size:
        return [lines] if lines else []
    chunks, current, total = [], [], 0
    for line in lines.splitlines():
        if total + len(line) + 1 > size and current:
            chunks.append("\n".join(current))
            current, total = [], 0
        current.append(line)
        total += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def moments_to_windows(moments: Any, duration: float, *, pad_s: float = 4.0,
                       min_s: float = 15.0, max_s: float = 90.0) -> list[dict]:
    """Model output -> candidate windows, clamped into the source.

    A model naming a moment gives one timestamp, not a span, so the window is
    built around it. Anything unparseable is dropped rather than guessed at.
    """
    out: list[dict] = []
    if not isinstance(moments, list):
        return out
    for item in moments:
        if not isinstance(item, dict):
            continue
        t = _num(item.get("t", item.get("start", item.get("time"))), -1.0)
        if t < 0:
            continue
        want = _num(item.get("duration"), 30.0)
        want = min(max(want, min_s), max_s)
        start = max(0.0, t - pad_s)
        end = start + want
        if duration > 0:
            end = min(end, duration)
            start = max(0.0, min(start, max(0.0, end - min_s)))
        if end - start < min_s:
            continue
        out.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "reasons": ["llm_nominated"],
            "llm_tag": str(item.get("why") or item.get("tag") or "")[:120],
        })
    return out


def apply_scores(cands: list[dict], verdicts: Any, *, weight: float = 0.5) -> int:
    """Blend `llm` scores into each candidate's `overall`. Returns how many hit.

    Blended, not substituted: the heuristic is transparent and the model has
    no idea what renders well, so neither gets the whole vote. `llm_score` is
    kept alongside so a bad blend can be diagnosed without re-running.
    """
    if not isinstance(verdicts, list):
        return 0
    by_id: dict[int, dict] = {}
    for v in verdicts:
        if isinstance(v, dict) and v.get("id") is not None:
            try:
                by_id[int(v["id"])] = v
            except (TypeError, ValueError):
                continue
    hit = 0
    scored: list[dict] = []
    w = min(1.0, max(0.0, weight))
    for i, cand in enumerate(cands):
        verdict = by_id.get(i)
        if verdict is None:
            continue
        # Check BEFORE clamping: a missing or non-numeric score sentinels as
        # -1, and clamping first would silently turn it into a real 0 and drag
        # the blend down. A model that skipped a clip has said nothing about
        # it, which is not the same as calling it worthless.
        raw = _num(verdict.get("score"), -1.0)
        if raw < 0:
            continue
        score = min(100.0, raw)
        cand["llm_score"] = round(score, 1)
        cand["judge_score"] = round(score, 1)
        if verdict.get("why"):
            cand["llm_reason"] = str(verdict["why"])[:200]
        cand["overall"] = round((1.0 - w) * _num(cand.get("overall")) + w * score, 2)
        cand["selection_score"] = cand["overall"]
        scored.append(cand)
        hit += 1

    # A ranking says WHICH clips make the cut; a scored answer has to say the
    # same thing or the board rule sees a pool in which nothing was selected.
    # `selection.status_of` reads `llm_rank`, and without this every candidate
    # in a scored pool came back `not_selected_in_judged_pool` — the judge had
    # spoken and the board came out empty.
    for position, cand in enumerate(
            sorted(scored, key=lambda c: -_num(c.get("llm_score")))):
        cand["llm_rank"] = position + 1
    return hit


# --------------------------------------------------------------------------
# prompts
# --------------------------------------------------------------------------

# The prompts themselves live in llm_prompts.py — re-exported because the
# tests, `clipper_build._anchor_stamp` and this module's own passes all
# reach for them by their old names.
from services.clipper.llm_prompts import (  # noqa: E402,F401
    ANCHOR_PROMPT_VERSION, anchor_prompt, nominate_prompt,
)


async def nominate(segments: Sequence[dict], duration: float, *,
                   per_chunk: int = 12,
                   engines: Sequence[str] = NOMINATE_ENGINES,
                   trace: Any = None, timeout: float | None = None,
                   is_cancelled=None) -> list[dict]:
    """Moments a cheap model thinks are clip-worthy. [] when no engine answers."""
    items = transcript_line_items(segments)
    if not items:
        return []
    found: list[dict] = []
    chunks = chunking.plan_chunks(items)
    for chunk in chunks:
        if is_cancelled is not None and is_cancelled():
            break
        request = f"nominate#{chunk['index']}"
        want = chunking.quota_for(chunk, per_chunk=per_chunk)
        parsed = await _ask_json(engines, nominate_prompt(chunk["lines"], want),
                                 trace=trace, stage="nominate", request=request,
                                 timeout=timeout, is_cancelled=is_cancelled,
                                 keys=("t", "start", "time"))
        before = len(found)
        found.extend(moments_to_windows(parsed, duration))
        _note_chunk_span(trace, "nominate", chunk, len(found) - before)
    _note_coverage(trace, "nominate", chunks, duration)
    logger.info("llm_select: nominated %d moments from %d chunks",
                len(found), len(chunks))
    return found


def _note_chunk_span(trace: Any, kind: str, chunk: dict, produced: int) -> None:
    """Record the slice a model was given, WITH the clock on it."""
    if trace is None:
        return
    try:
        trace.note_chunk(kind, chunk["index"], chars=chunk["chars"],
                         t_start=chunk["t_start"], t_end=chunk["t_end"],
                         produced=produced)
    except Exception:  # noqa: BLE001
        logger.debug("llm_select: trace rejected a chunk", exc_info=True)


def _note_coverage(trace: Any, kind: str, chunks: Sequence[dict],
                   duration: float) -> None:
    """How much of the source this pass actually read, and what it missed.

    A gap is a fact about the run. Before this, three hours of a stream could
    go unread and the only trace was that no clip came from there.
    """
    if trace is None:
        return
    try:
        cov = chunking.coverage(chunks, duration)
        trace.note_count(f"{kind}_chunks", cov["chunks"])
        trace.note_stage(
            kind, "covered" if not cov["gaps"] else "gaps",
            f"{cov['covered_s']}s of {round(duration, 1)}s"
            + (f", {len(cov['gaps'])} gap(s)" if cov["gaps"] else ""))
        for gap in cov["gaps"][:8]:
            trace.note_error(f"{kind}_gap",
                             f"{gap['start']}-{gap['end']}s: {gap['why']}")
    except Exception:  # noqa: BLE001
        logger.debug("llm_select: trace rejected coverage", exc_info=True)


def _attach_callback(anchor: dict, raw: Any, promises: Sequence[dict]) -> None:
    """Link an anchor to the setup it pays off, if the model named one.

    The setup is minutes or hours away, so it can never be inside the window —
    a callback is context the clip OWES, not context it can carry. Recording
    it is what lets `story.context_debt` charge for that and the headline say
    what the viewer missed.
    """
    from services.clipper.promises import MIN_CALLBACK_GAP_S

    t = _num((raw or {}).get("callback_to"), -1.0)
    if t < 0:
        return
    match = min(
        (p for p in promises
         if anchor["payoff_t"] - _num(p.get("t"), -1e9) >= MIN_CALLBACK_GAP_S),
        key=lambda p: abs(_num(p.get("t")) - t), default=None)
    if match is None or abs(_num(match.get("t")) - t) > 30.0:
        return
    anchor["callback_to"] = dict(match)
    if "CALLBACK" not in anchor["archetypes"]:
        anchor["archetypes"] = (anchor["archetypes"] + ["CALLBACK"])[:3]


async def detect_anchors(segments: Sequence[dict], duration: float, *,
                         per_chunk: int = 10,
                         engines: Sequence[str] = NOMINATE_ENGINES,
                         model: str | None = None,
                         promises: Sequence[dict] | None = None,
                         atoms: Sequence[dict] | None = None,
                         threads: Sequence[dict] | None = None,
                         episodes: Sequence[dict] | None = None,
                         trace: Any = None, timeout: float | None = None,
                         is_cancelled=None, chunk_cache: Any = None,
                         checkpoint: Callable[[dict], None] | None = None,
                         ) -> list[dict]:
    """Anchors: a payoff, what a viewer must know for it to land, an archetype.

    The richer sibling of `nominate`, and the input to the story engine. Same
    failure contract — [] when no engine answers, so the run falls back to the
    heuristic candidates rather than stopping.

    Chunked, never the whole stream in one prompt: a 12-hour transcript is
    ~64k tokens at the measured rate and up to 285k on a talkative source,
    past the context of the models this would otherwise use.
    """
    from services.clipper.story import normalise_anchor

    # Atom lines carry the evidence for their own moment — that the room got
    # loud, the picture cut, the game went into a menu — where a transcript
    # line carries only the words. Features as evidence, at the grain of one
    # utterance, which is what atoms exist for.
    if atoms:
        from services.clipper.atoms import to_line_items
        items = to_line_items(atoms)
    else:
        items = transcript_line_items(segments)
    if not items:
        return []
    from services.clipper import episodes as episode_mod
    from services.clipper import promises as promise_mod
    from services.clipper import reasoning_chunks

    # Built once for the whole stream, sliced per chunk. Free — no model call.
    # Threads give the stretches; the atoms give the words that label them.
    # `None` means nobody supplied the persisted upstream artifact.  An empty
    # list is a real, cacheable answer for a short source and must not trigger a
    # private recomputation that the envelope cannot account for.
    stream_episodes = (episode_mod.build(threads or [], atoms or [])
                       if episodes is None else list(episodes))

    found: list[dict] = []
    chunks = chunking.plan_chunks(items)
    prepared = reasoning_chunks.prepare("anchors", chunks, chunk_cache)
    state = prepared.state
    reused = 0
    for chunk in chunks:
        if is_cancelled is not None and is_cancelled():
            break
        saved = reasoning_chunks.reusable(state, chunk)
        if saved is not None:
            found.extend(saved)
            reused += 1
            _note_chunk_span(trace, "anchors", chunk, len(saved))
            continue
        index, lines = chunk["index"], chunk["lines"]
        # Setups still open anywhere up to the END of this chunk, which
        # includes ones inside it. Filtering to "before the chunk" was wrong
        # at real scale: a chunk holds five hours of this source, and a model
        # will not reliably connect a payoff at hour three to a line at hour
        # one buried in 30k tokens. The recall list is exactly that aid.
        #
        # Still bounded — `open_at` drops anything older than the lifetime or
        # closer than the gap, so a payoff never sees a prediction it cannot
        # possibly resolve.
        first_t, last_t = chunk["t_start"], chunk["t_end"]
        live = promise_mod.open_at(promises or [], last_t, span_from=first_t)
        # Only what CLOSED before this chunk opens: an episode still running is
        # partly in the window, and describing it as background would tell the
        # model the moment it is reading is old news.
        so_far = episode_mod.before(stream_episodes, first_t)
        request = f"anchors#{index}"
        want = chunking.quota_for(chunk, per_chunk=per_chunk)
        answer = await _ask_json_result(
            engines, anchor_prompt(lines, want, live, so_far), model=model,
            trace=trace, stage="anchors", request=request, timeout=timeout,
            is_cancelled=is_cancelled, keys=("payoff_t", "t", "payoff"))
        if answer.cancelled:
            break
        before = len(found)
        produced = []
        for raw in (answer.parsed or []):
            anchor = normalise_anchor(raw, duration)
            if anchor is not None:
                anchor["prompt_version"] = ANCHOR_PROMPT_VERSION
                anchor["chunk_index"] = index
                _attach_callback(anchor, raw, promises or [])
                produced.append(anchor)
        usable = answer.usable and (not answer.parsed or bool(produced))
        found.extend(produced)
        provenance = answer.provenance()
        provenance["normalised_items"] = len(produced)
        state = reasoning_chunks.record(
            state, chunk, usable=usable, items=produced,
            provenance=provenance)
        if checkpoint is not None:
            checkpoint(state)
        _note_chunk_span(trace, "anchors", chunk, len(found) - before)
    _note_coverage(trace, "anchors", chunks, duration)
    if trace is not None:
        tally = reasoning_chunks.counts(state)
        trace.note_count("anchors_chunks_reused", reused)
        trace.note_count("anchors_chunks_usable", tally[reasoning_chunks.USABLE])
        trace.note_count("anchors_chunks_unusable", tally[reasoning_chunks.UNUSABLE])
        trace.note_count("anchors_chunks_pending", tally[reasoning_chunks.PENDING])
        trace.note_stage("anchors_cache", prepared.reason,
                         f"{reused} reused, {tally[reasoning_chunks.UNUSABLE]} unusable, "
                         f"{tally[reasoning_chunks.PENDING]} pending")
    found = _dedupe_anchors(found)
    found.sort(key=lambda a: a["payoff_t"])
    logger.info("llm_select: %d anchors from %d chunks (%s)", len(found),
                len(chunks), ANCHOR_PROMPT_VERSION)
    return found


# Two anchors this close in time, from OVERLAPPING chunks, are the same moment
# seen twice. The overlap exists so a moment on a boundary is whole somewhere;
# the price is that the model reports it from both sides.
_SAME_PAYOFF_S = 4.0


def _dedupe_anchors(anchors: list[dict]) -> list[dict]:
    """Collapse the duplicates the chunk overlap creates, keeping the better.

    "Better" is the more confident one, and on a tie the one with a grounded
    quote — not the first, which would systematically prefer whichever side of
    the overlap happened to be read first.

    This is a TIME-based collapse and it is deliberately narrow. The stable
    moment id that would let two variants of one moment recognise each other
    arrives with the grouping batch; until then, four seconds is the width of
    one utterance and nothing wider is safe to merge.
    """
    out: list[dict] = []
    for anchor in sorted(anchors, key=lambda a: _num(a.get("payoff_t"))):
        # Only ACROSS chunks. Two anchors three seconds apart that the model
        # reported from the SAME prompt are two things it chose to name
        # separately, having seen both — merging them would silently discard a
        # judgement, and this function exists to undo an artefact of the
        # overlap, not to second-guess the model.
        twin = next((o for o in out
                     if o.get("chunk_index") != anchor.get("chunk_index")
                     and abs(_num(o.get("payoff_t")) - _num(anchor.get("payoff_t")))
                     <= _SAME_PAYOFF_S), None)
        if twin is None:
            out.append(anchor)
            continue
        better = (_num(anchor.get("confidence")), bool(anchor.get("payoff_quote")))
        held = (_num(twin.get("confidence")), bool(twin.get("payoff_quote")))
        if better > held:
            out[out.index(twin)] = anchor
    return out




# Judging lives in llm_judge.py — it reads finished candidates and
# ranks them, where this module proposes them. Re-exported so
# `from services.clipper.llm_select import judge` keeps working.
from services.clipper.llm_judge import (  # noqa: E402,F401
    JUDGE_PROMPT_VERSION, REJECT_REASONS, apply_ranking, judge,
    judge_prompt,
)
