"""
ClipForge — AI Stream Clipper, Pass C: where a clip actually starts and ends.

Split out of candidates.py to keep every file under the repo's 500-line limit,
and because it is a real seam: everything here takes a candidate that already
exists and moves its two edges. Nothing here proposes a candidate or scores one.

The transcript arrives WITH punctuation and original case (the clipper
transcribes with keep_punctuation=True), so sentence-final punctuation is the
primary boundary signal; audio energy only refines it.
"""

from __future__ import annotations

import logging
from typing import Sequence

from services.clipper.candidate_terms import (
    DANGLE_PAUSE_S, GRID_S, LEAD_IN_MAX_S, PAUSE_KEEP_S, PAYOFF_WINDOW_S,
    REACTION_MAX_S, RELEASE_GAP_S, SENTENCE_END_REACH_S, SNAP_TOLERANCE_S,
    TAIL_PAD_S,
    _CLOSERS, _CUES, _EPS, _LEAD_IN, _add, _bounds, _mean, _neighbourhood,
    _num, _snap, _source, _text_of, _tokens, _words_for,
)
from services.clipper.segmentation import (
    _continues, norm_token, points_in, series_slice, signal_view,
)

logger = logging.getLogger("clipforge.clipper.candidates")

def _sentence_from(sentences: Sequence[dict], t: float) -> dict | None:
    """The clip's first sentence: the one starting at `t`, else the one over it."""
    for s in sentences:
        if _num(s["start"]) >= t - 0.05 or _num(s["end"]) > t + 0.05:
            return s
    return None


def _sentence_before(sentences: Sequence[dict], t: float) -> dict | None:
    previous = None
    for s in sentences:
        if _num(s["start"]) >= t - 0.05:
            break
        previous = s
    return previous


def _nearest_sentence_start(sentences: Sequence[dict], t: float) -> float | None:
    best, distance = None, SNAP_TOLERANCE_S
    for s in sentences:
        start = _num(s["start"])
        if abs(start - t) <= distance:
            best, distance = start, abs(start - t)
        elif start > t + SNAP_TOLERANCE_S:
            break
    return best


def _nearest_sentence_end(sentences: Sequence[dict], t: float) -> float | None:
    """The sentence end to finish on, or None if there is none close enough.

    The start side has had `_nearest_sentence_start` from the beginning; the end
    side never had a mirror, and it showed. Measured on a real clip set, all four
    top candidates ended one word into a phrase — "...of water bro let's" before
    "go", "...cook our food let's" before "cook", "...trail chamber oh" before
    "my god". The payoff, reaction and answer rules that already run on the end
    decide WHICH thought to finish on; none of them makes it land on a boundary.

    Reaching FORWARD is allowed further than pulling back, because the failure
    being fixed is a truncated phrase: completing it is what the viewer wants,
    and trimming earlier only helps when the sentence had already closed.
    """
    best, distance = None, 0.0
    for s in sentences:
        end = _num(s["end"])
        gap = end - t
        reach = SENTENCE_END_REACH_S if gap >= 0 else SNAP_TOLERANCE_S
        if abs(gap) <= reach and (best is None or abs(gap) < distance):
            best, distance = end, abs(gap)
        elif end > t + SENTENCE_END_REACH_S:
            break
    return best


def _first_tokens(sentence: dict | None) -> list[str]:
    if not sentence:
        return []
    return _tokens([{"word": w} for w in str(sentence["text"]).split()])


def _payoff_time(start: float, end: float, words: Sequence[dict], sv: dict) -> float | None:
    """Where audio energy, an audio peak and emotion words combine highest.

    None when nothing in the span carries any evidence — a silent clip has no
    payoff, and inventing one would drag every later decision along with it.
    """
    if end - start <= 1.0:
        return None
    peaks = points_in(start, end, sv["peaks"])
    cues = [(_num(w["start"]) + _num(w["end"])) / 2.0 for w in words
            if start <= _num(w["start"]) < end and norm_token(w.get("word", "")) in _CUES]
    best_t, best_v, t = None, 0.0, start
    while t <= end + _EPS:
        energy = _mean(series_slice(sv["rms"], sv["rms_hop"], t - 1.0, t + 1.0))
        emotion = min(1.0, sum(1 for c in cues if abs(c - t) <= PAYOFF_WINDOW_S) / 3.0)
        value = energy + 0.6 * emotion + (0.5 if any(abs(p - t) <= 0.75 for p in peaks) else 0.0)
        # >= so a flat run resolves to its LAST instant: the payoff of a level
        # delivery is the end of it, not the start.
        if value >= best_v:
            best_t, best_v = t, value
        t += GRID_S
    return best_t if best_v > 0.0 else None


def _reaction_end(end: float, words: Sequence[dict], sv: dict, ceiling: float) -> float:
    """Extend up to REACTION_MAX_S while speech continues or the audio stays hot."""
    limit = min(ceiling, end + REACTION_MAX_S)
    out = cursor = end
    for w in words:
        w0, w1 = _num(w["start"]), _num(w["end"])
        if w1 <= cursor + _EPS:
            continue
        if w0 > limit or w0 - cursor >= PAUSE_KEEP_S:
            break
        out = cursor = min(w1, limit)
    if out <= end + _EPS:
        # Nobody spoke — a laugh or a crowd still counts as the reaction.
        after = _mean(series_slice(sv["rms"], sv["rms_hop"], end, min(limit, end + 1.5)))
        before = _mean(series_slice(sv["rms"], sv["rms_hop"], max(0.0, end - 3.0), end))
        if after > 0 and after >= 0.6 * before:
            out = min(limit, end + 1.5)
    return out


def _answer_end(sentences: Sequence[dict], start: float, end: float, hi: float) -> float:
    """Never cut between a question and the sentence that answers it."""
    last = None
    for s in sentences:
        if _num(s["end"]) <= end + 0.05:
            last = s
            continue
        if last is not None and str(last["text"]).rstrip(_CLOSERS).endswith("?"):
            if _num(s["end"]) - start <= hi:
                return _num(s["end"])
        break
    return end


def _trim_tail(end: float, words: Sequence[dict], floor: float) -> float:
    """Drop trailing dead air, keeping pauses shorter than PAUSE_KEEP_S."""
    last = 0.0
    for w in words:
        if _num(w["end"]) <= end + _EPS:
            last = max(last, _num(w["end"]))
        else:
            break
    if last <= 0 or end - last <= PAUSE_KEEP_S:
        return end
    return max(floor, min(end, last + TAIL_PAD_S))


def _keep_release(end: float, words: Sequence[dict], ceiling: float,
                  limit: float) -> float:
    """Leave the last word room to finish, which its timestamp does not.

    Every rule above decides WHICH word the clip ends on, and each of them
    lands the cut on that word's `end` — the moment Whisper stops hearing it,
    which precedes the moment it stops sounding. Measured on this source the
    gap runs a median 0.16s and a p90 of 0.40s (see TAIL_PAD_S), so cutting on
    the timestamp truncates the release of the final consonant. All three clips
    of the first export anyone watched did exactly that, and all three were
    heard as ending mid-phrase.

    Never crosses into the next word: when speech continues there is no silence
    to borrow, and the alternative would be splicing in someone's next onset.
    """
    inside_end = 0.0
    for w in words:
        we = _num(w["end"])
        if we <= end + _EPS:
            inside_end = max(inside_end, we)
    if inside_end <= 0 or end - inside_end > TAIL_PAD_S:
        return end  # already has room, or the tail rules chose a longer pause

    target = min(inside_end + TAIL_PAD_S, ceiling, limit)
    following = next((w for w in words if _num(w["start"]) >= end - _EPS), None)
    if following is not None:
        target = min(target, _num(following["start"]) - RELEASE_GAP_S)
    return max(end, target)


def _drop_dangling_tail(end: float, words: Sequence[dict], start: float,
                        lo: float) -> float:
    """Cut before a final word the speaker never finished the thought on.

    Measured on a real stream: clips ended "...of water bro let's" with the "go"
    16s later, "...trail chamber oh" with "my god" 5.5s later. The cut is in the
    right PLACE — speech genuinely stops there — but it keeps an orphan word that
    reads as a mistake. Extending across the silence would be worse; dropping the
    orphan is what a human editor does.

    Only fires when a real pause follows, so a clip that simply runs into the
    next sentence is untouched, and never when it would breach the minimum.
    """
    inside = [w for w in words if _num(w["end"]) <= end + _EPS]
    if len(inside) < 2:
        return end
    tail = inside[-1]
    if not _continues(str(tail.get("word", ""))):
        return end
    following = next((w for w in words if _num(w["start"]) > _num(tail["end"]) + _EPS),
                     None)
    if following is None or _num(following["start"]) - _num(tail["end"]) < DANGLE_PAUSE_S:
        return end
    trimmed = _num(inside[-2]["end"])
    return trimmed if trimmed - start >= lo else end


def _fit(start: float, end: float, words: Sequence[dict], lo: float, hi: float,
         floor: float, ceiling: float) -> tuple[float, float]:
    """Force the span inside [lo, hi] and the media bounds, staying off words."""
    start = max(floor, start)
    end = min(ceiling, max(end, start + 0.1))
    if end - start > hi:
        end = _snap(words, start + hi, to_end=True, limit=start + hi)
    if end - start < lo:
        end = min(ceiling, start + lo)
        if end - start < lo:  # the source itself is shorter than min_s
            start = max(floor, end - lo)
    start = _snap(words, start, to_end=False, limit=floor)
    if end - start > hi:  # snapping backwards can reopen the max breach
        start = _snap(words, end - hi, to_end=False, limit=end - hi)
    return start, max(end, start + 0.1)


def _rank_alternatives(raw: list[dict], words: Sequence[dict], start: float,
                       end: float, lo: float, hi: float) -> list[dict]:
    """Keep the two closest calls — an alternate nobody would consider is noise."""
    kept: list[tuple[float, dict]] = []
    for alt in raw:
        a0 = _snap(words, _num(alt["start"]), to_end=False, limit=0.0)
        a1 = _snap(words, _num(alt["end"]), to_end=True)
        drift = abs(a0 - start) + abs(a1 - end)
        if lo <= a1 - a0 <= hi and drift >= 0.4:
            kept.append((drift, {"start": round(a0, 3), "end": round(a1, 3),
                                 "why": str(alt["why"])}))
    kept.sort(key=lambda item: item[0])
    return [alt for _drift, alt in kept[:2]]


def refine_boundaries(cand: dict, transcript: dict, signals: dict, *,
                      min_s: float, max_s: float) -> dict:
    """Return a NEW candidate with human-quality in and out points.

    Order matters: open on a sentence, add a lead-in only if the opening line
    dangles, keep the reaction after the payoff, keep a question with its
    answer, then trim dead air. Every step re-checks [min_s, max_s].
    """
    cand = cand or {}
    lo, hi = _bounds(min_s, max_s)
    words = _words_for(cand, transcript)
    sentences = _source(words)["sentences"]
    sv = signal_view(signals)

    start, end = _num(cand.get("start")), _num(cand.get("end"))
    if end <= start:
        end = start + lo
    floor, ceiling = 0.0, max(end, _num(words[-1]["end"]) if words else end)
    reasons = [str(r) for r in (cand.get("reasons") or [])]
    alternatives: list[dict] = []

    snapped = _nearest_sentence_start(sentences, start)
    if snapped is not None and snapped + lo <= ceiling:
        start = snapped
        _add(reasons, "start_on_sentence")
    start = _snap(words, start, to_end=False, limit=floor)

    if _first_tokens(_sentence_from(sentences, start))[:1] in ([t] for t in _LEAD_IN):
        previous = _sentence_before(sentences, start)
        lead = _num(previous["start"]) if previous else start
        if 0 < start - lead <= LEAD_IN_MAX_S and end - lead <= hi:
            alternatives.append({"start": start, "end": end,
                                 "why": "opens on the action, without the set-up line"})
            start = lead
            _add(reasons, "lead_in_added")

    payoff = _payoff_time(start, end, words, sv)
    reaction = _reaction_end(end, words, sv, ceiling)
    if reaction > end + 0.05 and reaction - start <= hi:
        alternatives.append({"start": start, "end": end,
                             "why": "cuts on the payoff, before the reaction"})
        end = reaction
        _add(reasons, "reaction_kept")
    if payoff is not None and end <= payoff + _EPS:
        end = min(ceiling, max(end, payoff + min(REACTION_MAX_S, hi)))
        _add(reasons, "end_after_payoff")

    answer = _answer_end(sentences, start, end, hi)
    if answer > end + 0.05:
        end = min(ceiling, answer)
        _add(reasons, "answer_kept")

    trimmed = _trim_tail(end, words, floor)
    if trimmed < end - 0.05 and trimmed - start >= lo and (payoff is None or trimmed > payoff + 0.2):
        end = trimmed
        _add(reasons, "tail_trimmed")

    # Last thing before the duration fit: land the end on a finished thought.
    # Runs after payoff/reaction/answer because those choose WHICH thought the
    # clip ends on, and this only moves the cut onto its boundary. Never drops
    # below the minimum, never breaches the maximum, never crosses the payoff.
    # Order matters: snap onto a sentence boundary FIRST, then drop an orphan
    # word if the boundary itself sits on one. Reversed, the snap pulls the end
    # back onto the very word the drop just removed.
    sentence_end = _nearest_sentence_end(sentences, end)
    if sentence_end is not None and abs(sentence_end - end) > 0.05:
        candidate_end = min(ceiling, sentence_end)
        if (lo <= candidate_end - start <= hi
                and (payoff is None or candidate_end > payoff + 0.2)):
            end = candidate_end
            _add(reasons, "end_on_sentence")

    dropped = _drop_dangling_tail(end, words, start, lo)
    if dropped < end - 0.05:
        end = dropped
        _add(reasons, "dangling_tail_dropped")

    # Last of all, and after the orphan drop so it pads whatever word survived:
    # let the final word finish sounding. Bounded by start + hi so the fit below
    # has nothing left to claw back.
    released = _keep_release(end, words, ceiling, start + hi)
    if released > end + 0.01:
        end = released
        _add(reasons, "release_kept")

    start, end = _fit(start, end, words, lo, hi, floor, ceiling)
    inside, _before, _after = _neighbourhood(words, start, end)

    out = dict(cand)  # a NEW dict; the caller's candidate is never touched
    out.update({"start": round(start, 3), "end": round(end, 3),
                "text": _text_of(inside) or str(cand.get("text") or ""),
                "words": list(inside), "reasons": reasons,
                "alternatives": _rank_alternatives(alternatives, words, start, end, lo, hi)})
    return out

