"""
ClipForge — AI Stream Clipper: how a long stream is handed to a model.

WHAT THIS REPLACES. `chunk_lines` split the prompt text on line boundaries at
120,000 characters and knew nothing about the clock. Measured on the four-hour
audited source, that produced TWO chunks: one covering 3h21m and one covering
38m. Each got the same fixed quota of anchors, so a stretch six times longer
competed for the same ten slots — and every payoff the run found landed after
hour 2h57m, which is not evidence that the first three hours were empty, only
that nothing could show whether they were.

Two more silent losses sat on top of it. `transcript_lines` truncated at 600,000
characters and said nothing, so the TAIL of a long stream simply did not exist
for the model. And nothing anywhere recorded which parts of the source had been
looked at, so "did we read all of it" was not a question the artefacts could
answer.

THE RULE HERE. A chunk is bounded by BOTH the clock and the character budget,
whichever comes first. Consecutive chunks overlap, so a moment that straddles a
boundary is whole in at least one of them. Every second of the source ends up
either inside a chunk or inside an explicitly recorded skip — never neither.

WHAT IS PURE HERE. Everything. Items in, chunks out; no model, no I/O. The
caller renders its own lines (`atoms.to_line_items`, `llm_select`'s segment
form) and gets back the same lines grouped, with the times kept.
"""

from __future__ import annotations

from typing import Any, Sequence

# A chunk may not span more than this much of the CLOCK. 45 minutes is the
# planned starting point: long enough that a callback inside one chunk is still
# visible to the model, short enough that a four-hour stream becomes six
# chunks rather than two.
MAX_CHUNK_SECONDS = 45.0 * 60.0

# ...nor more than this many characters. Well under the old 120,000: the
# binding constraint should usually be the clock, and a chunk that hits this
# first is a stretch dense enough to deserve its own pass anyway.
MAX_CHUNK_CHARS = 40_000

# Consecutive chunks share this much time. A payoff whose setup sits just before
# a boundary is otherwise split across two prompts and legible in neither.
OVERLAP_SECONDS = 90.0

# A gap shorter than this is not worth reporting: it is the space between two
# utterances, not a hole in the coverage.
_GAP_FLOOR_S = 5.0


def _num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if out == out else default


def plan_chunks(items: Sequence[dict], *,
                max_seconds: float = MAX_CHUNK_SECONDS,
                max_chars: int = MAX_CHUNK_CHARS,
                overlap_seconds: float = OVERLAP_SECONDS) -> list[dict]:
    """Group timed line items into chunks bounded by time AND characters.

    Each chunk carries `index`, `t_start`, `t_end`, `chars`, `lines` (the joined
    text) and `items`. The times are the real span of what is inside it, which
    is what makes coverage checkable afterwards.

    Overlap is applied by REPLAYING trailing items into the next chunk rather
    than by moving the boundary. The same line then appears in two prompts, on
    purpose: a model reading either one sees the whole moment.

    A single item longer than `max_seconds`, or larger than `max_chars`, is
    never split — a line is the smallest thing with a timestamp, and cutting one
    would produce a chunk whose span cannot be stated. It gets a chunk to
    itself, flagged `oversized`, so the bound is either honoured or the breach
    is on the record. Silently returning a chunk twice the stated limit is the
    class of thing this module was written to end.
    """
    ordered = [i for i in (items or []) if isinstance(i, dict) and i.get("line")]
    ordered.sort(key=lambda i: (_num(i.get("start")), _num(i.get("end"))))
    if not ordered:
        return []

    max_seconds = max(1.0, float(max_seconds))
    max_chars = max(1, int(max_chars))
    overlap_seconds = max(0.0, float(overlap_seconds))

    chunks: list[dict] = []
    current: list[dict] = []
    chars = 0

    def flush() -> None:
        nonlocal current, chars
        if not current:
            return
        chunks.append(_chunk(len(chunks), current))
        # Replay the tail of what we just closed into the next chunk.
        if overlap_seconds > 0:
            edge = _num(current[-1].get("end")) - overlap_seconds
            replay = [i for i in current if _num(i.get("end")) > edge]
            # Never replay the whole chunk: that would make no progress and,
            # with a chunk that is one long item, would not terminate.
            current = replay if len(replay) < len(current) else []
        else:
            current = []
        chars = sum(len(i["line"]) + 1 for i in current)

    for item in ordered:
        size = len(item["line"]) + 1
        alone = (_num(item.get("end")) - _num(item.get("start")) > max_seconds
                 or size > max_chars)
        if current:
            span = _num(item.get("end")) - _num(current[0].get("start"))
            if alone or span > max_seconds or chars + size > max_chars:
                flush()
        if alone:
            # An item bigger than a bound cannot be split — a line is the
            # smallest thing with a timestamp. It gets a chunk of its own and
            # that chunk says it is over budget, because the alternative is a
            # chunk that quietly breaks the limit this module exists to state.
            # Dropping it instead would be worse: that is the silent tail loss
            # the planner replaces.
            chunks.append(_chunk(len(chunks), [item], oversized=True))
            current, chars = [], 0
            continue
        current.append(item)
        chars += size
    flush()
    return chunks


def _chunk(index: int, items: Sequence[dict], *, oversized: bool = False) -> dict:
    return {
        "index": index,
        "oversized": oversized,
        "t_start": round(min(_num(i.get("start")) for i in items), 3),
        "t_end": round(max(_num(i.get("end")) for i in items), 3),
        "chars": sum(len(i["line"]) + 1 for i in items),
        "lines": "\n".join(i["line"] for i in items),
        "items": list(items),
    }


def coverage(chunks: Sequence[dict], duration: float = 0.0) -> dict:
    """Which parts of the source were read, and which were not.

    Returns `covered_s`, `share`, and `gaps` — the intervals no chunk contains.
    A gap is a fact about the run and belongs in the trace: the alternative is
    what shipped before, where three hours of a stream could go unread and the
    only trace of it was that no clip came from there.
    """
    spans = sorted((float(c["t_start"]), float(c["t_end"]))
                   for c in (chunks or []) if c.get("t_end") is not None)
    merged: list[list[float]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1] + _GAP_FLOOR_S:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    gaps: list[dict] = []
    if merged:
        if duration > 0 and merged[0][0] > _GAP_FLOOR_S:
            gaps.append({"start": 0.0, "end": round(merged[0][0], 3),
                         "why": "no transcript before the first line"})
        for left, right in zip(merged, merged[1:]):
            gaps.append({"start": round(left[1], 3), "end": round(right[0], 3),
                         "why": "no transcript in this stretch"})
        if duration > 0 and duration - merged[-1][1] > _GAP_FLOOR_S:
            gaps.append({"start": round(merged[-1][1], 3), "end": round(duration, 3),
                         "why": "no transcript after the last line"})

    covered = sum(end - start for start, end in merged)
    return {
        "chunks": len(chunks or []),
        "oversized": sum(1 for c in (chunks or []) if c.get("oversized")),
        "covered_s": round(covered, 1),
        "share": round(covered / duration, 3) if duration > 0 else None,
        "gaps": gaps,
        "widest_chunk_s": round(max((e - s for s, e in spans), default=0.0), 1),
    }


def quota_for(chunk: dict, *, per_chunk: int, floor: int = 3,
              seconds_per_slot: float = 240.0) -> int:
    """How many anchors to ask this chunk for.

    Scaled by the time it covers, not fixed. A fixed quota is what let a chunk
    holding 3h21m and one holding 38m compete for the same ten slots — the long
    one had to be six times more selective to fit, and nothing said so.

    `floor` keeps a short chunk worth asking about at all; `per_chunk` stays the
    ceiling, so this can only ever ask for LESS than the old behaviour on a
    short chunk and never more on a long one.
    """
    span = max(0.0, _num(chunk.get("t_end")) - _num(chunk.get("t_start")))
    want = int(round(span / max(1.0, seconds_per_slot)))
    return max(int(floor), min(int(per_chunk), want or int(floor)))
