"""What each STRETCH of a stream is, rather than what the whole file is.

`content_type` is a column on the project, so one value picks one weight row in
`scoring.PROFILES` for every clip in a source. Every long live source labelled
by hand runs two or three types in a row — an hour of gym then three of
Minecraft, a browser stretch then a game, a talk then a match. On those, a
PERFECT whole-file classifier is still wrong for a third of the clips. The
granularity is the defect; no threshold reaches it.

This slices the same signals per stretch and runs the SAME classifier over each
one, so nothing about how a type is decided changes — only how often it is
asked. The stretches come from `episodes.build`, which already cuts the stream
on the clock and labels each piece.

Deliberately not a new classifier. A second way of deciding content type would
have to be kept in step with the first, and the first is the one with the
measurements behind it.
"""

from __future__ import annotations

from typing import Any, Sequence

from services.clipper.candidate_terms import _num

SEGMENT_TYPE_VERSION = "segment_type_v1"

# Below this a stretch has too little in it to classify — the answer would be
# whatever the handful of frames happened to show.
MIN_SEGMENT_S = 120.0


def _clip_spans(spans: Any, start: float, end: float) -> list[list[float]]:
    out: list[list[float]] = []
    for span in spans or []:
        try:
            a, b = float(span[0]), float(span[1])
        except (TypeError, ValueError, IndexError):
            continue
        a, b = max(a, start), min(b, end)
        if b > a:
            out.append([a - start, b - start])
    return out


def _clip_series(block: Any, key: str, start: float, end: float) -> Any:
    """A hop-indexed series cut to a time range, keeping its hop."""
    if not isinstance(block, dict):
        return block
    hop = _num(block.get("hop_s"))
    values = block.get(key)
    if hop <= 0 or not isinstance(values, list):
        return block
    lo = max(0, int(start / hop))
    hi = min(len(values), int(end / hop) + 1)
    return {**block, key: values[lo:hi]}


def slice_signals(signals: dict, start: float, end: float) -> dict:
    """The Pass A signals as they would look if the source were just this range.

    Times are re-based to zero, because every consumer treats them as offsets
    from the start of what it was handed.
    """
    signals = signals or {}
    out = dict(signals)
    out["duration"] = max(0.0, end - start)
    out["motion"] = _clip_series(signals.get("motion"), "motion", start, end)
    out["speech"] = _clip_spans(signals.get("speech"), start, end)
    out["silence"] = _clip_spans(signals.get("silence"), start, end)
    out["faces"] = [
        {**f, "t": _num(f.get("t")) - start}
        for f in (signals.get("faces") or [])
        if isinstance(f, dict) and start <= _num(f.get("t")) <= end
    ]
    return out


def _segments_in(transcript: dict, start: float, end: float) -> dict:
    rows = [s for s in ((transcript or {}).get("segments") or [])
            if _num(s.get("end")) > start and _num(s.get("start")) < end]
    return {"segments": rows}


def clock_ranges(duration: float, step: float = 20 * 60.0
                 ) -> list[tuple[float, float]]:
    """The stream cut into equal stretches.

    On the clock rather than on where the arcs fall, for the reason
    `episodes.build` records: threads on a dense source overlap constantly, so
    there is no quiet moment to cut at and everything merges into one blob.
    Taking the ranges straight from the clock also means this works on a
    project that never ran the story path and has no threads at all.
    """
    if duration <= step:
        return [(0.0, duration)] if duration > 0 else []
    out: list[tuple[float, float]] = []
    edge = 0.0
    while edge < duration:
        out.append((edge, min(edge + step, duration)))
        edge += step
    return out


def classify_ranges(frames: Sequence[str], frame_times: Sequence[float],
                    signals: dict, transcript: dict,
                    ranges: Sequence[tuple[float, float]]) -> list[dict]:
    """One verdict per range: `{start, end, content_type, confidence}`.

    `frame_times` is what each sampled frame's timestamp is, in the same order
    as `frames` — the analyse stage already computes them to sample with, and
    without them a stretch would be classified on the whole source's frames.
    """
    from services.clipper.content_type import detect_content_type

    out: list[dict] = []
    for start, end in ranges or []:
        if end - start < MIN_SEGMENT_S:
            continue
        mine = [f for f, t in zip(frames, frame_times) if start <= t <= end]
        if not mine:
            continue
        verdict = detect_content_type(
            mine, slice_signals(signals, start, end),
            _segments_in(transcript, start, end))
        out.append({
            "start": round(start, 2),
            "end": round(end, 2),
            "content_type": verdict.get("content_type"),
            "confidence": verdict.get("confidence"),
            "version": SEGMENT_TYPE_VERSION,
        })
    return out


def type_at(segments: Sequence[dict], t: float, fallback: str) -> str:
    """The type of the stretch `t` falls in, or the whole-file answer.

    The fallback is not a formality: a stretch can be too short to classify,
    and a candidate in one of those is better scored by the source's overall
    profile than by nothing.
    """
    for seg in segments or []:
        if _num(seg.get("start")) <= t <= _num(seg.get("end")):
            return str(seg.get("content_type") or fallback)
    return fallback
