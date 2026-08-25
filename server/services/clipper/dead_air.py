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


def remap_overlays(overlays: Sequence[dict],
                   spans: Sequence[tuple[float, float]]) -> list[dict]:
    """Caption overlays on the trimmed timeline.

    Called before the .ass is written, never after: libass positions against
    absolute times, so an overlay left on the untrimmed clock drifts further
    out of sync with every second removed.
    """
    if not spans:
        return list(overlays or [])
    out: list[dict] = []
    for ov in overlays or []:
        start = remap_time(_num(ov.get("start")), spans)
        end = remap_time(_num(ov.get("end")), spans)
        if end <= start:
            continue                      # wholly inside removed time
        out.append({**ov, "start": start, "end": end})
    return out


def select_expr(spans: Sequence[tuple[float, float]]) -> str:
    """The ffmpeg `select` expression that keeps everything except `spans`.

    Single-quoted by the caller: commas separate filters in a filtergraph, and
    `between(t,a,b)` is full of them.
    """
    terms = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in spans or [])
    return f"not({terms})" if terms else "1"
