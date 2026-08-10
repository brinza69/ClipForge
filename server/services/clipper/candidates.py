"""
ClipForge — AI Stream Clipper, Pass C: candidate cuts and the frozen feature
vector.

Pass B hands over semantic windows. This module overproduces candidate cuts
from them — a wide open, a tight open and a peak-centred cut per window,
because proposing a single in-point per moment means the good one is never
found and `dedupe.py` collapses the rest anyway. It then flattens everything a
scorer needs into FEATURE_KEYS: one flat dict of finite floats, the contract
`scoring.py` and `ranker.py` share.

Pass C is three files, split at real seams to stay under the repo's 500-line
limit. `candidate_terms.py` is the bottom layer — vocabularies, tuning
constants, FEATURE_KEYS and the numeric guards. `candidate_boundaries.py`
moves a candidate's two edges onto sentence boundaries. This file proposes the
candidates and measures the finished ones. `refine_boundaries` is re-exported
here, so `from services.clipper.candidates import refine_boundaries` keeps
working.

Pure arithmetic over plain dicts — no DB, no ffmpeg, no cv2, no network. Every
function here will be handed 3-second sources, silent audio and transcripts
with no word timings, and has to answer for all of them.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Iterator, Sequence

from services.clipper.candidate_boundaries import (
    _first_tokens, _payoff_time, _sentence_from, refine_boundaries,
)
from services.clipper.candidate_terms import (
    FEATURE_KEYS, PAUSE_KEEP_S, PAYOFF_WINDOW_S, _CLOSERS, _CUES, _EMOTION, _FILLER, _HOOK,
    _LAUGH, _LEAD_IN, _PROFANITY, _SENTIMENT, _bounds, _clamp01, _clean_words,
    _div, _hits, _mean, _neighbourhood, _num, _snap, _source, _text_of,
    _tokens, _words_for,
)
from services.clipper.segmentation import (
    norm_token, overlap_seconds, points_in, series_slice, signal_view,
)

logger = logging.getLogger("clipforge.clipper.candidates")

__all__ = ["FEATURE_KEYS", "generate_candidates", "refine_boundaries",
           "extract_features"]


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


# Below this absolute spread between a source's quiet floor and its busiest
# frames, the source simply has no menu behaviour and the whole measure is
# noise. Measured on the gameplay slice the spread is 0.14 (p25 0.020 -> p95
# 0.161); on any source without panels it is a rounding error.
_UI_MIN_SPREAD = 0.03


def _ui_ratio(sv: dict, start: float, end: float) -> float:
    """How menu-heavy this window is, 0..1, RELATIVE TO ITS OWN SOURCE.

    An absolute threshold cannot work here: the share of flat mid-grey depends
    on how much permanent chrome the streamer's layout carries, and calibrating
    it per stream is exactly the kind of constant that rots. Measuring against
    the source's own p25/p95 asks the question that actually matters — is THIS
    window menu-heavy for THIS stream — and needs no calibration.

    Returns 0.0 when the source has no menu behaviour to speak of, which is the
    benign default: scoring treats absent and zero alike.
    """
    series = sv.get("ui") or []
    window = series_slice(series, sv["motion_hop"], start, end)
    if not window or len(series) < 8:
        return 0.0
    ordered = sorted(series)
    floor = ordered[max(0, int(0.25 * (len(ordered) - 1)))]
    ceiling = ordered[min(len(ordered) - 1, int(0.95 * (len(ordered) - 1)))]
    if ceiling - floor < _UI_MIN_SPREAD:
        return 0.0
    return _clamp01((_mean(window) - floor) / (ceiling - floor))


def _spoken_ratio(inside: Sequence[dict], start: float, end: float,
                  span: float) -> float | None:
    """Share of the window someone is actually saying a word.

    The audio version of this answers a different question — "is the track
    above the silence floor" — and on a stream with constant game audio the
    answer is always yes. Measured on the 12-minute co-stream: the audio spans
    call 94% of the VOD speech, the median window 0.985 and 24 of 56 windows
    exactly 1.000, a stdev of 0.069 across the whole field. That is a constant,
    not a feature, and `clarity` hands it 30 points while `low_dialogue` gates
    on it. The transcript's own word timings put the real figure at 34%.

    None when there are no word timings to measure — then the audio estimate
    is the only one available and stands.
    """
    timed = [(_num(w.get("start"), -1.0), _num(w.get("end"), -1.0)) for w in inside]
    timed = [(a, b) for a, b in timed if a >= 0 and b > a]
    if not timed or span <= 0:
        return None
    covered, cursor = 0.0, start
    for a, b in sorted(timed):
        a, b = max(a, cursor), min(b, end)
        if b > a:
            covered += b - a
            cursor = b
    return _clamp01(covered / span)


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
        "game_ui_ratio": _ui_ratio(sv, start, end),
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
    if (spoken := _spoken_ratio(inside, start, end, span)) is not None:
        raw["speech_ratio"] = spoken

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
