"""
ClipForge — AI Stream Clipper: WHERE the dynamic edit cuts, on the clock.

Split from `dynamic_edit.py` at the repo's 500-line limit, on the seam that
module's own headings already drew. Two different questions live there: WHERE to
cut, which is about time — sentence ends, peaks, the rhythm the reference edits
keep — and WHAT each shot looks like, which is about framing. This is the first.

Nothing here knows about cameras or rectangles.

Moved verbatim; the only edits are the imports it needs to stand alone.
"""

from __future__ import annotations

import re
from bisect import bisect_left
from typing import Sequence

from services.clipper.dynamic_cameras import DEFAULT_STYLE, _f
from services.clipper.dynamic_edit import (  # noqa: F401  (shared vocabulary)
    _EMPHATIC,
    _SENTENCE_END,
)

__all__ = ["_boundaries", "_cut_times"]


# ---------------------------------------------------------------------------
# where to cut
# ---------------------------------------------------------------------------

def _boundaries(words: Sequence[dict], peaks: Sequence[float],
                scenes: Sequence[float], clip_start: float, duration: float,
                style: dict) -> list[tuple[float, float]]:
    """Candidate cut points as [(t, weight)], clip-relative.

    Weight is "how natural a cut here would sound". Speech pauses win, because
    a cut inside a word is the one artefact a viewer always notices; a source
    scene cut is close behind, since the source already cut there.
    """
    gap_min = _f(style.get("pause_gap_s"), 0.14)
    out: list[tuple[float, float]] = []

    prev_end: float | None = None
    prev_text = ""
    for w in words or []:
        ws = _f(w.get("start")) - clip_start
        we = _f(w.get("end"), _f(w.get("start"))) - clip_start
        if prev_end is not None and 0.0 < ws < duration:
            gap = ws - prev_end
            if gap >= gap_min:
                weight = 1.0 + min(1.0, gap / 0.7)
                if _SENTENCE_END.search(prev_text.strip()):
                    weight += 0.8
                out.append((max(0.0, (prev_end + ws) / 2.0), weight))
            elif _SENTENCE_END.search(prev_text.strip()):
                out.append((max(0.0, prev_end), 1.2))
        prev_end, prev_text = we, str(w.get("word") or "")

    for t in scenes or []:
        rel = _f(t) - clip_start
        if 0.0 < rel < duration:
            out.append((rel, 1.6))
    for t in peaks or []:
        rel = _f(t) - clip_start
        if 0.0 < rel < duration:
            out.append((rel, 0.7))

    out.sort(key=lambda b: b[0])
    return out


def _cut_times(boundaries: Sequence[tuple[float, float]], duration: float,
               style: dict) -> list[float]:
    """Greedily walk the clip, taking the best-sounding boundary in range.

    Falls back to a hard cut at the target length when a stretch offers nothing:
    a six-second unbroken shot breaks the style far more visibly than a cut
    landing mid-phrase.
    """
    lo = max(0.2, _f(style.get("min_shot_s"), DEFAULT_STYLE["min_shot_s"]))
    target = max(lo, _f(style.get("target_shot_s"), DEFAULT_STYLE["target_shot_s"]))
    hi = max(target, _f(style.get("max_shot_s"), DEFAULT_STYLE["max_shot_s"]))

    cuts: list[float] = []
    t = 0.0
    while duration - t > hi:
        window = [(bt, bw) for bt, bw in boundaries if t + lo <= bt <= t + hi]
        if window:
            # Prefer a strong boundary, tie-breaking toward the target length so
            # the cadence stays even instead of clumping at one end.
            nxt = max(window, key=lambda b: (b[1], -abs(b[0] - (t + target))))[0]
        else:
            nxt = t + target
        cuts.append(round(max(nxt, t + lo), 3))
        t = cuts[-1]

    if cuts and duration - cuts[-1] < lo:      # absorb a runt tail
        cuts.pop()
    return cuts
