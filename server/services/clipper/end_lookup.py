"""
ClipForge — AI Stream Clipper, Pass C: small lookups over a clip's words and
VAD spans, for end_acoustics.

Moved whole and unchanged out of end_acoustics.py when EN2 (R3, the reader's
cooperative stop) took that file past 500 lines; end_acoustics imports them
back under their old names.
"""

from __future__ import annotations

from typing import Sequence

from services.clipper.candidate_terms import _EPS, _num


def _span_at(spans: Sequence[Sequence[float]], t: float) -> list[float] | None:
    for s0, s1 in spans:
        if s0 <= t + _EPS and t < s1 - _EPS:
            return [s0, s1]
    return None


def _last_word(words: Sequence[dict], t: float) -> dict | None:
    last = None
    for w in words:
        if _num(w["end"]) <= t + _EPS:
            if last is None or _num(w["end"]) >= _num(last["end"]):
                last = w
        elif _num(w["start"]) > t:
            break
    return last


def _brief(w: dict | None) -> dict | None:
    if w is None:
        return None
    return {"word": str(w.get("word", "")).strip(), "start": _num(w["start"]),
            "end": _num(w["end"])}
