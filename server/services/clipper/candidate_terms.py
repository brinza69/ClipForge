"""
ClipForge — AI Stream Clipper, Pass C: the shared vocabulary of candidate work.

Split out of candidates.py to keep every file under the repo's 500-line limit.
The seam is a real one: nothing here knows what a candidate IS. It is the
word lists, the tuning constants, the frozen feature vector's key order, and
the numeric guards — all of which candidates.py, candidate_boundaries.py and
their tests read, in that one direction only.

Every guard exists because Pass C is handed 3-second sources, silent audio and
transcripts with no word timings, and has to answer for all of them.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Sequence

from services.clipper.segmentation import (
    norm_token, sentences_from_words, word_list,
)

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
    "game_ui_ratio",
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

