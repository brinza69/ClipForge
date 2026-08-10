"""
ClipForge — AI Stream Clipper, Pass C: candidates, boundary refinement and the
frozen feature vector.

Pass B hands over semantic windows. This module overproduces candidate cuts
from them — a wide open, a tight open and a peak-centred cut per window,
because proposing a single in-point per moment means the good one is never
found and `dedupe.py` collapses the rest anyway. It then refines each cut
against the transcript (sentence-true in-point, guaranteed payoff, the reaction
after it, no dead air) and flattens everything a scorer needs into
FEATURE_KEYS: one flat dict of finite floats, the contract `scoring.py` and
`ranker.py` share.

The transcript arrives WITH punctuation and original case (the clipper
transcribes with keep_punctuation=True), so sentence-final punctuation is the
primary boundary signal; audio energy only refines it.

Pure arithmetic over plain dicts — no DB, no ffmpeg, no cv2, no network. Every
function here will be handed 3-second sources, silent audio and transcripts
with no word timings, and has to answer for all of them.
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from typing import Any, Iterator, Sequence

from services.clipper.segmentation import (
    norm_token, overlap_seconds, points_in, sentences_from_words, series_slice,
    signal_view, word_list,
)
from services.clipper.segmentation import _continues  # noqa: F401  (shared vocabulary)

logger = logging.getLogger("clipforge.clipper.candidates")

# Openers that only make sense once you know what they refer to: a clip opening
# on one of these needs the sentence before it. Romanian is spelled without
# diacritics because norm_token() folds them onto ASCII.
_LEAD_IN = frozenset((
    "he", "she", "they", "them", "it", "that", "this", "these", "those", "so", "and", "but",
    "which", "el", "ea", "ei", "ele", "asta", "aia", "deci", "si", "dar", "care"))

# Short, high-precision cue lists (EN + RO). They only nudge decisions the
# timing signals already lean towards, so missing a word costs far less than
# firing on ordinary speech.
_EMOTION = frozenset((
    "wow", "insane", "crazy", "amazing", "unbelievable", "incredible", "terrible", "awful",
    "shocked", "scared", "angry", "hate", "love", "nebunie", "incredibil", "imposibil",
    "grozav", "oribil", "socat", "uimitor", "doamne", "jur", "serios", "aiurea"))
_HOOK = frozenset((
    "wait", "look", "listen", "watch", "imagine", "secret", "nobody", "everyone", "never",
    "stop", "why", "how", "stai", "uite", "asculta", "atentie", "imagineaza", "nimeni",
    "niciodata", "opreste"))
_LAUGH = frozenset((
    "haha", "hahaha", "hahah", "hehe", "ahah", "lol", "lmao", "rofl", "ras", "rad", "rade"))
_FILLER = frozenset((
    "um", "uh", "uhm", "erm", "hmm", "like", "basically", "literally", "aaa", "pai", "gen",
    "adica", "stii", "cumva", "practic"))
# Polarity is not separated: sentiment_magnitude only ever asks how loaded the
# language is, never in which direction.
_SENTIMENT = frozenset((
    "good", "great", "best", "perfect", "beautiful", "happy", "win", "won", "bad", "worst",
    "wrong", "lost", "fail", "failed", "stupid", "bun", "super", "frumos", "castigat", "tare",
    "rau", "prost", "gresit", "pierdut", "greu", "urat", "nasol"))
# Content-safety lexicon: deliberately tiny, only the unambiguous terms.
_PROFANITY = frozenset((
    "fuck", "fucking", "fucked", "shit", "bitch", "asshole", "cunt", "pula", "muie", "cacat",
    "futut", "dracu", "pizda"))
_CUES = _EMOTION | _LAUGH
_CLOSERS = "\"'”’)]}»"

PAUSE_KEEP_S = 1.2        # gaps shorter than this are rhythm, not dead air
REACTION_MAX_S = 3.0      # how far past the payoff a reaction may run
TAIL_PAD_S = 0.25         # breathing room kept after the last word
LEAD_IN_MAX_S = 5.0       # a longer preceding sentence is context, not a lead-in
SNAP_TOLERANCE_S = 2.5    # furthest a start is moved to reach a sentence
SENTENCE_END_REACH_S = 3.5  # furthest an end is pushed OUT to finish a sentence
DANGLE_PAUSE_S = 1.5      # silence after an orphan word that proves it is one
GRID_S = 0.5              # payoff search resolution
PAYOFF_WINDOW_S = 1.5     # emotion words this close count towards the payoff
_EPS = 1e-6

# The frozen vector. Every key is produced on every call, in this order, as a
# finite float — `scoring.py` reads by name and `ranker.py` by position, so
# adding a key is a versioned change, never an incidental one.
FEATURE_KEYS: tuple[str, ...] = (
    "duration", "position_ratio", "words_per_second", "word_count", "question_count",
    "exclamation_count", "sentence_count", "avg_word_confidence", "silence_ratio",
    "audio_rms_mean", "audio_rms_max", "audio_peak_count", "audio_energy_delta",
    "motion_mean", "motion_max", "scene_cut_count", "face_presence_ratio",
    "first_sentence_len", "emotion_word_ratio", "pronoun_start", "ends_on_sentence",
    "starts_on_sentence", "hook_strength", "question_opening", "first_seconds_energy",
    "starts_mid_sentence", "speech_rate_wpm", "filler_ratio", "speech_ratio", "snr",
    "setup_ratio", "payoff_strength", "payoff_position", "peak_prominence",
    "emotion_intensity", "laughter_score", "sentiment_magnitude", "novelty",
    "repetition_ratio", "audio_peak_ratio", "audio_dynamic_range", "motion_peak",
    "scene_cut_rate", "reaction_score", "post_payoff_energy", "word_confidence",
    "ends_mid_sentence", "self_contained", "pronoun_dependency", "energy_trend",
    "dead_air_ratio", "boundary_confidence", "snap_quality", "loudness_ok",
    "clipping_ratio", "blur_score", "resolution_ok", "profanity_ratio", "flagged_terms",
)


# --------------------------------------------------------------------------
# helpers — every one of these is a NaN or divide-by-zero guard
# --------------------------------------------------------------------------

def _num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default

def _clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else (1.0 if value > 1.0 else value)

def _div(a: float, b: float, default: float = 0.0) -> float:
    return (a / b) if b else default

def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0

def _tokens(words: Sequence[dict]) -> list[str]:
    return [t for t in (norm_token(w.get("word", "")) for w in words) if t]

def _hits(tokens: Sequence[str], vocab: frozenset[str]) -> int:
    return sum(1 for t in tokens if t in vocab)

def _text_of(words: Sequence[dict]) -> str:
    return " ".join(str(w.get("word", "")).strip() for w in words)

def _add(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)

def _bounds(min_s: Any, max_s: Any) -> tuple[float, float]:
    lo = max(1.0, _num(min_s, 15.0) or 15.0)
    return lo, max(lo + 1.0, _num(max_s, 90.0) or 90.0)

def _clean_words(raw: Any) -> list[dict]:
    """Keep only word dicts with a usable time span, in time order."""
    out = []
    for w in raw or ():
        if not isinstance(w, dict) or not str(w.get("word") or "").strip():
            continue
        start, end = _num(w.get("start"), -1.0), _num(w.get("end"), -1.0)
        if start >= 0 and end >= start:
            out.append(w)
    return sorted(out, key=lambda w: (_num(w["start"]), _num(w["end"])))

def _words_for(cand: dict, transcript: Any) -> list[dict]:
    """Source words, falling back to the candidate's own slice.

    The worker keeps `words` on every candidate, so a caller with no transcript
    still gets refinement — just without the surrounding context.
    """
    words = word_list(transcript) if isinstance(transcript, dict) else []
    return words or _clean_words(cand.get("words"))


# Per-source derivations, computed once. Both public functions run once per
# candidate and a stream yields hundreds of them; re-segmenting a 60k-word
# transcript each time is the difference between a second and a minute. Keyed
# by a fingerprint rather than id(), because a recycled id would silently hand
# back another source's sentences.
_SOURCE_CACHE: dict[tuple[int, float, float], dict[str, Any]] = {}


def _source(words: list[dict]) -> dict[str, Any]:
    if not words:
        return {"sentences": [], "counts": Counter()}
    key = (len(words), round(_num(words[0]["start"]), 3), round(_num(words[-1]["end"]), 3))
    cached = _SOURCE_CACHE.get(key)
    if cached is None:
        cached = {"sentences": sentences_from_words(words),
                  # Content words only: "the" turning up everywhere says
                  # nothing about how novel a clip is.
                  "counts": Counter(t for t in _tokens(words) if len(t) > 3)}
        if len(_SOURCE_CACHE) > 2:
            _SOURCE_CACHE.clear()  # one source at a time; never grow unbounded
        _SOURCE_CACHE[key] = cached
    return cached


def _neighbourhood(words: Sequence[dict], start: float,
                   end: float) -> tuple[list[dict], dict | None, dict | None]:
    """One pass: (words fully inside the span, the word before, the word after)."""
    inside: list[dict] = []
    before = after = None
    for w in words:
        w0, w1 = _num(w["start"]), _num(w["end"])
        if w1 <= start + _EPS:
            before = w
        elif w0 >= end - _EPS:
            after = w
            break
        elif w0 >= start - _EPS and w1 <= end + _EPS:
            inside.append(w)
    return inside, before, after


def _snap(words: Sequence[dict], t: float, *, to_end: bool,
          limit: float | None = None) -> float:
    """Move `t` off the middle of a word — a cut may never land mid-syllable.

    Keeps the straddled word whole (pulling a start back, pushing an end out)
    unless that crosses `limit`, in which case the word is dropped instead.
    """
    for w in words:
        w0, w1 = _num(w["start"]), _num(w["end"])
        if w0 < t - _EPS and w1 > t + _EPS:
            if to_end:
                return w1 if (limit is None or w1 <= limit) else w0
            return w0 if (limit is None or w0 >= limit) else w1
        if w0 >= t:
            break
    return t


# --------------------------------------------------------------------------
# candidate generation
# --------------------------------------------------------------------------

def _best_peak(t0: float, t1: float, sv: dict) -> float | None:
    """The loudest audio peak inside the window, or None if there is none."""
    inside = points_in(t0, t1, sv["peaks"])
    if not inside:
        return None
    return max(inside, key=lambda t: _mean(series_slice(sv["rms"], sv["rms_hop"],
                                                        t - 0.5, t + 0.5)))


def _variants(w0: float, w1: float, words: Sequence[dict], sv: dict, lo: float,
              hi: float, target: float) -> Iterator[tuple[float, float, str]]:
    """The two or three cuts worth proposing for one semantic window."""
    span = w1 - w0
    if span >= lo:
        if span <= hi:
            yield w0, w1, "window_whole"
        else:
            # A window overruns only because the thought kept going, so the end
            # is what matters — keep the tail, drop the head.
            yield _snap(words, w1 - hi, to_end=False, limit=w1 - hi), w1, "window_trimmed"

    # A window much longer than the target is usually setup plus payoff; the
    # tight open proposes the payoff on its own.
    if span > target + 4.0:
        start = _snap(words, w1 - target, to_end=False, limit=w0)
        if lo <= w1 - start <= hi:
            yield start, w1, "tight_open"

    peak = _best_peak(w0, w1, sv)
    if peak is not None:
        # Sit the peak ~65% in: where scoring's payoff_position band wants it,
        # and it leaves room for the reaction.
        end = _snap(words, min(w1, max(peak + 0.35 * target, w0 + target)), to_end=True, limit=w1)
        start = _snap(words, max(w0, end - target), to_end=False, limit=w0)
        if lo <= end - start <= hi:
            yield start, end, "peak_centred"


def generate_candidates(windows: list[dict], signals: dict, *, min_s: float,
                        max_s: float, target_s: float) -> list[dict]:
    """Turn Pass B windows into overlapping candidate cuts.

    Deliberately more candidates than clips: `dedupe.py` collapses the ones
    that turn out to be the same moment, and the alternates it keeps are what
    the UI offers as "other cuts of this moment".
    """
    lo, hi = _bounds(min_s, max_s)
    target = min(max(_num(target_s, 35.0) or 35.0, lo), hi)
    sv = signal_view(signals)
    out: list[dict] = []
    seen: set[tuple[float, float]] = set()

    for index, win in enumerate(windows or []):
        if not isinstance(win, dict):
            continue
        words = _clean_words(win.get("words"))
        w0 = _num(win.get("start"), _num(words[0]["start"]) if words else 0.0)
        w1 = _num(win.get("end"), _num(words[-1]["end"]) if words else 0.0)
        if w1 - w0 < lo:
            continue
        base = [str(r) for r in (win.get("reasons") or [])]

        for start, end, why in _variants(w0, w1, words, sv, lo, hi, target):
            key = (round(start, 2), round(end, 2))
            if not (lo <= end - start <= hi) or key in seen:
                continue
            seen.add(key)
            inside, _b, _a = _neighbourhood(words, start, end)
            out.append({"start": round(start, 3), "end": round(end, 3),
                        "text": _text_of(inside), "words": list(inside),
                        "reasons": base + [why], "window_index": index})

    out.sort(key=lambda c: (c["start"], c["end"]))
    logger.info("Pass C: %d candidates from %d windows", len(out), len(windows or []))
    return out


# --------------------------------------------------------------------------
# boundary refinement
# --------------------------------------------------------------------------

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

    start, end = _fit(start, end, words, lo, hi, floor, ceiling)
    inside, _before, _after = _neighbourhood(words, start, end)

    out = dict(cand)  # a NEW dict; the caller's candidate is never touched
    out.update({"start": round(start, 3), "end": round(end, 3),
                "text": _text_of(inside) or str(cand.get("text") or ""),
                "words": list(inside), "reasons": reasons,
                "alternatives": _rank_alternatives(alternatives, words, start, end, lo, hi)})
    return out


# --------------------------------------------------------------------------
# feature extraction
# --------------------------------------------------------------------------

def _lexical(inside: Sequence[dict], counts: Counter) -> dict[str, float]:
    tokens = _tokens(inside)
    total = float(len(tokens))
    content = [t for t in tokens if len(t) > 3]
    probabilities = [p for p in (_num(w.get("probability"), -1.0) for w in inside) if p >= 0]
    confidence = _mean(probabilities)
    flagged = float(_hits(tokens, _PROFANITY))
    # A word the rest of the source barely uses is what makes this clip new.
    rare = float(sum(1 for t in content if counts.get(t, 0) <= 2))
    return {
        "word_count": total,
        "avg_word_confidence": confidence,
        "word_confidence": confidence,
        "emotion_word_ratio": _div(float(_hits(tokens, _EMOTION)), total),
        "laughter_score": _clamp01(_div(float(_hits(tokens, _LAUGH)), total) * 12.0),
        "filler_ratio": _div(float(_hits(tokens, _FILLER)), total),
        "sentiment_magnitude": _clamp01(_div(float(_hits(tokens, _SENTIMENT)), total) * 8.0),
        "profanity_ratio": _div(flagged, total),
        "flagged_terms": flagged,
        "novelty": _div(rare, float(len(content))),
        "repetition_ratio": 1.0 - _div(float(len(set(content))), float(len(content)), 1.0),
    }


def _structure(inside: Sequence[dict], sentences: Sequence[dict], before: dict | None,
               after: dict | None, start: float, end: float) -> dict[str, float]:
    text = _text_of(inside).rstrip(_CLOSERS)
    first = _sentence_from(sentences, start)
    opening = _first_tokens(first)
    clip_tokens = _tokens(inside)

    starts_on = 1.0 if (first is not None and abs(_num(first["start"]) - start) <= 0.35) else 0.0
    ends_on = 1.0 if text.endswith((".", "!", "?", "…")) else 0.0
    pronoun_start = 1.0 if (clip_tokens and clip_tokens[0] in _LEAD_IN) else 0.0
    dangling = 1.0 if text.endswith("?") else 0.0
    # A missing neighbour means the clip touches the edge of the source; treat
    # that as a clean break rather than as a mid-sentence cut.
    lead_gap = (start - _num(before["end"])) if before else PAUSE_KEEP_S
    tail_gap = (_num(after["start"]) - end) if after else PAUSE_KEEP_S
    edge = (abs(start - _num(inside[0]["start"])) + abs(end - _num(inside[-1]["end"]))) if inside else 0.0

    return {
        "sentence_count": float(sum(1 for s in sentences
                                    if start - 0.05 <= _num(s["start"]) < end)),
        "question_count": float(text.count("?")),
        "exclamation_count": float(text.count("!")),
        "first_sentence_len": float(len(opening)),
        "starts_on_sentence": starts_on,
        "starts_mid_sentence": 1.0 - starts_on,
        "ends_on_sentence": ends_on,
        "ends_mid_sentence": 1.0 - ends_on,
        "question_opening": 1.0 if (first and str(first["text"]).rstrip().endswith("?")) else 0.0,
        "pronoun_start": pronoun_start,
        "pronoun_dependency": _clamp01(0.7 * pronoun_start + 0.3 * _div(
            float(_hits(opening, _LEAD_IN)), float(len(opening)))),
        "self_contained": _clamp01(1.0 - 0.35 * (1.0 - starts_on) - 0.25 * (1.0 - ends_on)
                                   - 0.25 * pronoun_start - 0.15 * dangling),
        "boundary_confidence": _clamp01(0.35 * starts_on + 0.35 * ends_on
                                        + 0.15 * _clamp01(max(0.0, lead_gap) / 0.6)
                                        + 0.15 * _clamp01(max(0.0, tail_gap) / 0.6)),
        # 1.0 when both cuts sit exactly on a word edge, 0 by a quarter-second.
        "snap_quality": _clamp01(1.0 - edge / 0.5),
    }


def _audio(start: float, end: float, duration: float, sv: dict, payoff: float) -> dict[str, float]:
    rms = series_slice(sv["rms"], sv["rms_hop"], start, end)
    mean, top = _mean(rms), max(rms, default=0.0)
    half = len(rms) // 2
    delta = (_mean(rms[half:]) - _mean(rms[:half])) if half else 0.0
    ordered = sorted(rms)
    quarter = max(1, len(ordered) // 4)
    loud, quiet = _mean(ordered[-quarter:]), _mean(ordered[:quarter])
    median = ordered[len(ordered) // 2] if ordered else 0.0

    silence = overlap_seconds(start, end, sv["silence"])
    dead = sum(overlap for a, b in sv["silence"]
               if (overlap := min(b, end) - max(a, start)) >= PAUSE_KEEP_S)
    peaks = float(len(points_in(start, end, sv["peaks"])))
    after = _mean(series_slice(sv["rms"], sv["rms_hop"], payoff, end))
    setup = _mean(series_slice(sv["rms"], sv["rms_hop"], start, payoff))

    return {
        "audio_rms_mean": _clamp01(mean),
        "audio_rms_max": _clamp01(top),
        "audio_peak_count": peaks,
        "audio_energy_delta": delta,
        # rms is normalised 0..1 over the whole source, so a 0.2 delta is a
        # large swing inside one clip.
        "energy_trend": max(-1.0, min(1.0, delta / 0.2)),
        "audio_peak_ratio": _clamp01(_div(peaks, max(1.0, duration / 8.0))),
        "audio_dynamic_range": _clamp01(_div(top - min(rms, default=0.0), top)),
        "peak_prominence": _clamp01(_div(top - median, top)),
        "silence_ratio": _clamp01(_div(silence, duration)),
        "dead_air_ratio": _clamp01(_div(dead, duration)),
        "speech_ratio": _clamp01(_div(overlap_seconds(start, end, sv["speech"]), duration))
        if sv["speech"] else _clamp01(1.0 - _div(silence, duration)),
        "snr": _clamp01(_div(loud - quiet, loud)),
        "first_seconds_energy": _clamp01(
            _mean(series_slice(sv["rms"], sv["rms_hop"], start, min(end, start + 3.0)))),
        "post_payoff_energy": _clamp01(_div(after, mean)),
        # The reaction is at least as loud as the run-up, or it is not one.
        "payoff_energy_ratio": _clamp01(_div(after, setup)),
        "loudness_ok": _clamp01(1.0 - max(0.0, 0.20 - mean, mean - 0.85) / 0.20),
        # Post-normalisation saturation is the only clipping evidence Pass A
        # keeps; it under-reports, which is the safe direction for a penalty.
        "clipping_ratio": _div(float(sum(1 for v in rms if v >= 0.999)), float(len(rms))),
    }


def _visual(start: float, end: float, duration: float, sv: dict, signals: Any) -> dict[str, float]:
    motion = series_slice(sv["motion"], sv["motion_hop"], start, end)
    cuts = float(len(points_in(start, end, sv["scenes"])))
    samples = [f for f in sv["faces"] if start <= _num(f.get("t"), -1.0) <= end]
    blob = signals if isinstance(signals, dict) else {}
    # The analysis proxy is always 480p, so its dimensions say nothing about the
    # source; only an explicit source height is evidence either way.
    height = _num(blob.get("source_height") or blob.get("height"), 0.0)
    return {
        "motion_mean": _clamp01(_mean(motion)),
        "motion_max": _clamp01(max(motion, default=0.0)),
        "motion_peak": _clamp01(max(motion, default=0.0)),
        "scene_cut_count": cuts,
        "scene_cut_rate": _div(cuts, duration),
        "face_presence_ratio": _div(float(sum(1 for f in samples if f.get("boxes"))),
                                    float(len(samples))),
        # Not measurable from the Pass A signals; emitted as real zeros/ones so
        # the vector stays fixed-width for the ranker.
        "blur_score": 0.0,
        "resolution_ok": 1.0 if height <= 0 else _clamp01(height / 720.0),
    }


def extract_features(cand: dict, transcript: dict, signals: dict,
                     duration: float) -> dict:
    """The frozen numeric vector: exactly FEATURE_KEYS, every value finite."""
    cand = cand or {}
    start, end = _num(cand.get("start")), _num(cand.get("end"))
    if end < start:
        start, end = end, start
    span = max(0.0, end - start)

    words = _words_for(cand, transcript)
    source = _source(words)
    sv = signal_view(signals)
    inside, before, after = _neighbourhood(words, start, end)
    total = _num(duration) or (_num(words[-1]["end"]) if words else 0.0)

    payoff = _payoff_time(start, end, inside, sv)
    # No evidence: assume the conventional position so the downstream bands stay
    # neutral, and let payoff_strength report the truth.
    payoff_at = payoff if payoff is not None else start + 0.7 * span
    strength = 0.0
    if payoff is not None:
        near = _mean(series_slice(sv["rms"], sv["rms_hop"], payoff_at - 1.0, payoff_at + 1.0))
        cues = sum(1 for w in inside if norm_token(w.get("word", "")) in _CUES
                   and abs((_num(w["start"]) + _num(w["end"])) / 2.0 - payoff_at) <= PAYOFF_WINDOW_S)
        strength = _clamp01(0.5 * near + 0.2 * min(1.0, cues / 2.0)
                            + (0.3 if points_in(payoff_at - 0.75, payoff_at + 0.75,
                                                sv["peaks"]) else 0.0))

    raw: dict[str, float] = {
        "duration": span,
        "position_ratio": _clamp01(_div(start, total)),
        "words_per_second": _div(float(len(inside)), span),
        "speech_rate_wpm": _div(float(len(inside)), span) * 60.0,
        "setup_ratio": _clamp01(_div(payoff_at - start, span)),
        "payoff_position": _clamp01(_div(payoff_at - start, span)),
        "payoff_strength": strength,
    }
    raw.update(_lexical(inside, source["counts"]))
    raw.update(_structure(inside, source["sentences"], before, after, start, end))
    raw.update(_audio(start, end, span, sv, payoff_at))
    raw.update(_visual(start, end, span, sv, signals))

    # A reaction is loud audio AND someone still talking after the payoff.
    spoken_after = float(sum(1 for w in inside if _num(w["start"]) >= payoff_at))
    raw["reaction_score"] = _clamp01(0.6 * raw["payoff_energy_ratio"]
                                     + 0.4 * _clamp01(spoken_after / 8.0))
    raw["emotion_intensity"] = _clamp01(0.45 * _clamp01(raw["emotion_word_ratio"] * 20.0)
                                        + 0.30 * raw["laughter_score"]
                                        + 0.25 * raw["peak_prominence"])
    raw["hook_strength"] = _clamp01(0.30 * raw["question_opening"]
                                    + 0.25 * _clamp01(float(_hits(_tokens(inside)[:12], _HOOK)))
                                    + 0.20 * raw["first_seconds_energy"]
                                    + 0.25 * _clamp01(raw["emotion_word_ratio"] * 20.0)
                                    - 0.30 * raw["starts_mid_sentence"])

    return {key: _num(raw.get(key, 0.0)) for key in FEATURE_KEYS}
