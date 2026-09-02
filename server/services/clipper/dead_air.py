"""Seconds inside a chosen window that carry nothing, and the arithmetic of
removing them.

§15 of the brief. `LATEST COMPLETE START + EARLIEST SATISFYING END` is already
implemented at the variant level — `story.variants_from_anchor` competes cuts
that open and close in different places. What was missing is the inside: a
window can be correctly bounded and still spend four seconds in the middle
waiting for someone to finish walking across a room.

WHAT COUNTS AS DEAD, and why each rule is here:

  - It has to be SILENT. The silence spans in signals.json are already measured
    against the source's own median level, so a loud stream and a quiet one are
    judged the same way.
  - It has to contain NO WORDS. The RMS floor is an audio measure and will mark
    a quietly-spoken word as silence; Whisper's word timings are the better
    evidence of "someone is talking", and they veto.
  - It has to be LONGER than a beat. `PAUSE_KEEP_S` already encodes this for the
    boundary rules — "gaps shorter than this are rhythm, not dead air" — and
    re-deciding it here with a second number would let the two drift apart.
  - The EDGES are not ours. The start and end are owned by the boundary rules,
    which have their own reasons for where they landed (the reaction, the tail
    release). Trimming inward from the edge would silently undo them.
  - A beat SURVIVES. Removing a pause completely makes the two sides sound
    spliced together, because a speaker does not actually stop dead between
    sentences. Half of PAUSE_KEEP_S is kept, split either side of the cut.

Times in and out are CLIP-RELATIVE seconds, because that is what both the
renderer and the caption plan work in.
"""

from __future__ import annotations

from typing import Sequence

from services.clipper.candidate_terms import PAUSE_KEEP_S, _num

# Kept from every pause that is trimmed, split evenly either side of the cut,
# so what remains still reads as a breath rather than a splice.
KEEP_BEAT_S = PAUSE_KEEP_S / 2.0
# Below this a removal is not worth the discontinuity it creates.
MIN_DROP_S = 0.5
# The boundary rules own this much at each end. See the module docstring.
EDGE_KEEP_S = 1.0


def _spans_in_window(spans: Sequence[Sequence[float]], start: float,
                     end: float) -> list[tuple[float, float]]:
    """Absolute source spans clipped into clip-relative coordinates."""
    out: list[tuple[float, float]] = []
    for span in spans or []:
        try:
            a, b = float(span[0]), float(span[1])
        except (TypeError, ValueError, IndexError):
            continue
        a, b = max(a, start), min(b, end)
        if b > a:
            out.append((a - start, b - start))
    return out


def _holds_a_word(a: float, b: float, words: Sequence[dict], start: float) -> bool:
    for w in words or []:
        ws = _num(w.get("start")) - start
        we = _num(w.get("end")) - start
        if we > a and ws < b:
            return True
    return False


def dead_spans(cand: dict, signals: dict, words: Sequence[dict] | None = None
               ) -> list[tuple[float, float]]:
    """Clip-relative spans worth removing, earliest first and never overlapping."""
    cand = cand if isinstance(cand, dict) else {}
    start, end = _num(cand.get("start")), _num(cand.get("end"))
    duration = end - start
    if duration <= 2 * EDGE_KEEP_S:
        return []

    silence = (signals or {}).get("silence") or []
    keep = KEEP_BEAT_S / 2.0
    out: list[tuple[float, float]] = []
    for a, b in _spans_in_window(silence, start, end):
        if b - a < PAUSE_KEEP_S:
            continue                      # a beat, not dead air
        if a < EDGE_KEEP_S or b > duration - EDGE_KEEP_S:
            continue                      # the boundary rules own the edges
        if _holds_a_word(a, b, words or [], start):
            continue                      # someone is talking, quietly
        lo, hi = a + keep, b - keep
        if hi - lo >= MIN_DROP_S:
            out.append((round(lo, 3), round(hi, 3)))
    return sorted(out)


def removed_seconds(spans: Sequence[tuple[float, float]]) -> float:
    return round(sum(max(0.0, b - a) for a, b in spans or []), 3)


def remap_time(t: float, spans: Sequence[tuple[float, float]]) -> float:
    """Where `t` lands once `spans` have been removed.

    A time INSIDE a removed span collapses onto its start — that is the only
    answer that keeps the sequence non-decreasing, and it is the right one for a
    caption: nothing should be spoken there, and if something is, it belongs
    with what follows rather than drifting later.
    """
    shift = 0.0
    for a, b in spans or []:
        if t <= a:
            break
        shift += min(t, b) - a
    return round(max(0.0, t - shift), 3)


def _kept_ranges(start: float, end: float,
                 spans: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """Parts of one source interval left after sorted, disjoint removals."""
    cursor = start
    out: list[tuple[float, float]] = []
    for lo, hi in spans:
        if hi <= cursor:
            continue
        if lo >= end:
            break
        if lo > cursor:
            out.append((cursor, min(lo, end)))
        cursor = max(cursor, hi)
        if cursor >= end:
            break
    if cursor < end:
        out.append((cursor, end))
    return out


def _interval_was_removed(start: float, end: float,
                          spans: Sequence[tuple[float, float]]) -> bool:
    """Whether every source instant between two surviving pieces was cut."""
    if end <= start:
        return False
    covered = sum(max(0.0, min(end, hi) - max(start, lo)) for lo, hi in spans)
    return abs(covered - (end - start)) <= 0.001


def delivered_shots(shots: Sequence[dict], spans: Sequence[tuple[float, float]]
                    ) -> tuple[list[dict], set[int]]:
    """Reconstruct the shot pieces the trimmed file actually contains.

    The returned indices name boundaries whose two sides are separated by
    removed source time. They are trim jumps, not ordinary planner cuts: even
    two `fit` pieces from the same original shot do not deliver one continuous
    image after the source skips forward.

    Inputs are already validated by the sidecar reader. The persisted shot
    dicts are copied, never changed by an audit.
    """
    if not spans:
        return [dict(shot) for shot in shots], set()

    pieces: list[dict] = []
    source_ranges: list[tuple[float, float]] = []
    for shot in shots:
        start, end = float(shot["t0"]), float(shot["t1"])
        for kept_start, kept_end in _kept_ranges(start, end, spans):
            mapped_start = remap_time(kept_start, spans)
            mapped_end = remap_time(kept_end, spans)
            if mapped_end <= mapped_start:
                continue
            pieces.append({**shot, "t0": mapped_start, "t1": mapped_end})
            source_ranges.append((kept_start, kept_end))

    jumps = {
        index for index in range(1, len(source_ranges))
        if _interval_was_removed(source_ranges[index - 1][1],
                                 source_ranges[index][0], spans)
    }
    return pieces, jumps


def remap_overlays(overlays: Sequence[dict],
                   spans: Sequence[tuple[float, float]]) -> list[dict]:
    """Caption overlays on the trimmed timeline.

    Called before the .ass is written, never after: libass positions against
    absolute times, so an overlay left on the untrimmed clock drifts further
    out of sync with every second removed.

    THE KEYS ARE `start_t`/`end_t` AND THAT IS NOT COSMETIC. This function read
    `start`/`end` until 2026-08-17 — keys that `caption_plan_to_overlays` has
    never produced and `build_overlays_ass` has never consumed. Every overlay
    therefore remapped to (0, 0), was dropped by the check below as "wholly
    inside removed time", and `_write_ass` returned None for an empty list. So
    turning `trim_silence` on did not drift the captions, it removed ALL of
    them: the clip rendered with no subtitles at all, on both the static and
    the multi-shot path, and the stale .ass from a previous export stayed on
    disk looking like proof that one had been written.

    Its three unit tests passed throughout, because they were written against
    `start`/`end` as well — the function and its tests agreed with each other
    and with nothing else. `test_the_overlay_shape_is_the_one_the_captioner_emits`
    exists so that cannot happen again.
    """
    if not spans:
        return list(overlays or [])
    out: list[dict] = []
    for ov in overlays or []:
        start = remap_time(_num(ov.get("start_t")), spans)
        end = remap_time(_num(ov.get("end_t")), spans)
        if end <= start:
            continue                      # wholly inside removed time
        out.append({**ov, "start_t": start, "end_t": end})
    return out


def spans_within(spans: Sequence[tuple[float, float]],
                 limit: float) -> list[tuple[float, float]]:
    """The spans that fall inside the first `limit` seconds, clipped to it.

    For previews, which show the opening of a clip rather than all of it. Both
    renderers shorten their `-t` by the TOTAL removed seconds, so handing a
    12-second preview a span that sits at t=20 makes it 2.4s short of a window
    that span was never in.
    """
    out: list[tuple[float, float]] = []
    for start, end in spans or []:
        start, end = _num(start), _num(end)
        if start >= limit:
            continue
        end = min(end, limit)
        if end > start:
            out.append((start, end))
    return out


def select_expr(spans: Sequence[tuple[float, float]]) -> str:
    """The ffmpeg `select` expression that keeps everything except `spans`.

    Single-quoted by the caller: commas separate filters in a filtergraph, and
    `between(t,a,b)` is full of them.
    """
    terms = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in spans or [])
    return f"not({terms})" if terms else "1"
