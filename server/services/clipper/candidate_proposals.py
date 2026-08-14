"""
ClipForge — AI Stream Clipper: turning outside proposals into candidates.

Pass C generates candidates from Pass B's semantic windows. This module takes
proposals that came from somewhere ELSE — a language model naming a moment, or
an anchor carrying a payoff and the facts it needs — and turns them into the
same shape, so everything downstream cannot tell where a candidate came from.

Split out of candidates.py, which crossed the repo's 500-line limit when the
story engine landed. The seam is real: nothing here proposes a window or
measures a finished one, it only converts and merges.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from services.clipper.candidate_terms import (
    _bounds, _neighbourhood, _num, _text_of,
)
from services.clipper.segmentation import signal_view, word_list

logger = logging.getLogger("clipforge.clipper.candidates")


# the same proposal. Loose on purpose: the point of a second nominator is the
# moments the first one MISSED, so only a near-identical span is redundant.
_NOMINATION_OVERLAP = 0.75


# A silent run-in longer than this carries nothing, so a start inside it is
# not the latest complete start — the first word after it is.
_CONTEXT_SNAP_S = 1.5


def _snap_context_to_speech(anchor: dict, words: Sequence[dict]) -> dict:
    """Move each required-context timestamp onto the word that states it.

    A model names a moment by the segment it sits in, and segment starts are
    coarse. Measured: a context timestamp landed 12.6s before the first word
    of the thing it described, so the window opened on twelve seconds of
    silence and its own hook_latency reported exactly that. Snapping forward
    is the principle the module is built on — a start with no speech after it
    until the payoff carries no information, so it is not the latest complete
    start.
    """
    context = anchor.get("required_context") or []
    if not context or not words:
        return anchor
    starts = [_num(w.get("start"), -1.0) for w in words]
    snapped = []
    for item in context:
        t = _num(item.get("t"), -1.0)
        after = next((s for s in starts if s >= t - 0.05), None)
        if after is not None and after - t > _CONTEXT_SNAP_S:
            item = {**item, "t": round(after, 3)}
        snapped.append(item)
    return {**anchor, "required_context": snapped}


def candidates_from_anchors(anchors: Sequence[dict], transcript: Any,
                            signals: Any, *, min_s: float, max_s: float,
                            duration: float = 0.0,
                            atoms: Sequence[dict] | None = None) -> list[dict]:
    """Anchors -> candidate windows, reasoned backwards from the payoff.

    The forward half is not reimplemented here: `_reaction_end` already knows
    how far past a payoff the reaction runs, and `refine_boundaries` will run
    it again along with the sentence-true in-point and the dangling-tail trim.
    This supplies the LATEST COMPLETE START — which is the half the old
    reasoning had no way to find, because the earliest required fact is a
    semantic question and the audio has no opinion on it.
    """
    from services.clipper import story
    from services.clipper.candidate_boundaries import _reaction_end

    lo, hi = _bounds(min_s, max_s)
    words = word_list(transcript) if isinstance(transcript, dict) else []
    sv = signal_view(signals)
    ceiling = _num(duration) or (_num(words[-1]["end"]) if words else 0.0)

    out: list[dict] = []
    for anchor in anchors or []:
        payoff = _num(anchor.get("payoff_t"), -1.0)
        if payoff < 0:
            continue
        reaction = _reaction_end(payoff, words, sv, ceiling or payoff + hi)
        anchor = _snap_context_to_speech(anchor, words)
        for win in story.variants_from_anchor(anchor, reaction, lo=lo, hi=hi,
                                              ceiling=ceiling):
            inside, _b, _a = _neighbourhood(words, win["start"], win["end"])
            backrefs = story.resolve_backrefs(inside, atoms, win["start"])
            if backrefs:
                win["story"]["backrefs"] = backrefs
                unresolved = [b for b in backrefs if not b["resolved"]]
                if unresolved:
                    win["story"]["unresolved_refs"] = unresolved
            debt = story.context_debt(inside, anchor,
                                      backrefs=backrefs if atoms else None)
            if anchor.get("callback_to"):
                # A callback's setup is minutes or hours away and can never be
                # in the window. The clip owes it, and owes less only when the
                # payoff line restates it — which is when a callback works as
                # a standalone clip at all.
                from services.clipper import promises as promise_mod
                owed = promise_mod.callback_debt(anchor["callback_to"], inside)
                win["story"]["callback_to"] = anchor["callback_to"]
                win["story"]["callback_debt"] = owed
                debt = max(debt, 0.5 * owed)
            win["story"]["context_debt"] = debt
            win["story"]["hook_latency"] = story.hook_latency(
                win["start"], anchor.get("hook_t"), inside)
            out.append({**win, "text": _text_of(inside), "words": list(inside),
                        "window_index": -1})

    out.sort(key=lambda c: (c["start"], c["end"]))
    logger.info("Pass C: %d windows from %d anchors", len(out), len(anchors or []))
    return out


def merge_nominations(cands: list[dict], nominated: Sequence[dict],
                      transcript: Any, *, min_s: float, max_s: float,
                      keep_overlaps: bool = False) -> list[dict]:
    """Add nominated windows that the existing candidates do not already cover.

    Union, not replacement. The two nominators fail differently — the scorer
    finds what is loud and structurally clean, a language model finds what is
    interesting — and keeping both is the whole reason for running the second
    one. Nominations arrive as bare spans, so each gets its words and text
    filled in here, the same shape generate_candidates emits.

    `keep_overlaps` turns the coverage check off, and story_v1 needs it. That
    path does not propose bare timestamps; it proposes a CUT, derived from the
    earliest fact the payoff needs. When it lands on the same moment as a
    heuristic window the two are not duplicates — they are two boundaries for
    one moment, and the better one is a question for `dedupe` AFTER scoring,
    not for a coverage test before it. Measured: with the check on, every one
    of 14 story windows was swallowed by the heuristic window it overlapped
    and none reached the board.
    """
    lo, hi = _bounds(min_s, max_s)
    words = word_list(transcript) if isinstance(transcript, dict) else []
    merged = list(cands)

    def covered(start: float, end: float) -> bool:
        for existing in merged:
            a, b = _num(existing.get("start")), _num(existing.get("end"))
            shortest = min(b - a, end - start)
            if shortest <= 0:
                continue
            overlap = min(b, end) - max(a, start)
            if overlap > 0 and overlap / shortest > _NOMINATION_OVERLAP:
                return True
        return False

    added = 0
    for win in nominated or []:
        start, end = _num(win.get("start")), _num(win.get("end"))
        if not (lo <= end - start <= hi):
            continue
        if not keep_overlaps and covered(start, end):
            continue
        inside, _b, _a = _neighbourhood(words, start, end)
        entry = {
            "start": round(start, 3), "end": round(end, 3),
            "text": _text_of(inside), "words": list(inside),
            "reasons": list(win.get("reasons") or ["llm_nominated"]),
            "llm_tag": win.get("llm_tag", ""),
            "window_index": -1,
        }
        # The story rationale is the whole point of the story path — losing it
        # here would leave a candidate nobody can explain or debug.
        for key in ("story", "variant"):
            if win.get(key) is not None:
                entry[key] = win[key]
        merged.append(entry)
        added += 1

    merged.sort(key=lambda c: (_num(c.get("start")), _num(c.get("end"))))
    logger.info("Pass C: %d of %d nominations were new", added, len(nominated or []))
    return merged

