"""
ClipForge — AI Stream Clipper: candidate scoring.

Turns the frozen feature vector from `candidates.extract_features` into 16
sub-scores on 0..100, a single weighted `overall`, and a plain-English reason.

Three deliberate constraints:

  * Pure arithmetic over a plain dict. No DB, no network, no model files — so a
    scoring change can be regression-tested in milliseconds.
  * Every feature is read with `features.get(key, 0.0)`. A feature extractor
    that has not learned to emit a signal yet degrades that sub-score instead
    of raising, which matters because Pass A/B/C evolve independently of this
    file.
  * Nothing here can produce NaN or a value outside [0, 100]. The ranker and
    the UI both assume that, and a single NaN would poison the weighted mean.

FEATURE KEYS this file reads (all 0..1 unless marked raw). Anything absent is
treated as 0.0:

    hook_strength, question_opening, first_seconds_energy, starts_mid_sentence
    speech_rate_wpm (raw wpm), filler_ratio, speech_ratio, snr
    setup_ratio, payoff_strength, payoff_position, peak_prominence
    emotion_intensity, laughter_score, sentiment_magnitude
    novelty, repetition_ratio
    audio_rms_mean, audio_peak_ratio, audio_dynamic_range
    motion_mean, motion_peak, scene_cut_rate (raw cuts/second), game_ui_ratio
    reaction_score, post_payoff_energy
    words_per_second (raw), word_confidence
    ends_mid_sentence, self_contained, pronoun_dependency
    energy_trend (-1..1), dead_air_ratio
    boundary_confidence, snap_quality
    loudness_ok, clipping_ratio, blur_score, resolution_ok
    profanity_ratio, flagged_terms (raw count)

CAVEAT for whoever wires this up: features whose polarity is inverted
(starts_mid_sentence, filler_ratio, clipping_ratio, …) score *well* when the
key is missing, because "absent" and "zero" are the same thing to `.get`. If an
extractor cannot compute one of those yet, the honest fix is to emit it as a
real number, not to leave it out.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Callable

logger = logging.getLogger("clipforge.clipper.scoring")

SUB_SCORES = (
    "hook", "clarity", "setup_efficiency", "payoff", "emotion", "novelty",
    "audio_energy", "visual_energy", "reaction", "caption_suitability",
    "platform_fit", "context_completeness", "retention", "edit_confidence",
    "technical", "safety",
)

# Ideal duration band per platform, in seconds. platform_fit is the ONLY
# sub-score the platform touches — everything else is about the content.
# The lower bound is 15 s everywhere: shorter than that and none of these
# surfaces give a clip a second look.
PLATFORM_BANDS: dict[str, tuple[float, float]] = {
    "tiktok": (15.0, 45.0),
    "youtube_shorts": (15.0, 60.0),
    "instagram_reels": (15.0, 90.0),
    "facebook_reels": (15.0, 60.0),
}
_DEFAULT_BAND = (15.0, 60.0)

# Raw per-profile weights. Each row sums to 100 by hand for readability;
# _normalise() divides by the actual sum so an edit can never silently change
# the meaning of `overall`.
_RAW_PROFILES: dict[str, dict[str, float]] = {
    # Gaming: stakes read through sound and motion long before they read in
    # words, and the streamer's reaction IS the clip.
    "gaming": {
        "hook": 8, "clarity": 3, "setup_efficiency": 5, "payoff": 10,
        "emotion": 8, "novelty": 6, "audio_energy": 12, "visual_energy": 12,
        "reaction": 11, "caption_suitability": 3, "platform_fit": 6,
        "context_completeness": 4, "retention": 5, "edit_confidence": 3,
        "technical": 2, "safety": 2,
    },
    # Podcast: nothing happens on screen, so an idea has to open well and land.
    "podcast": {
        "hook": 14, "clarity": 13, "setup_efficiency": 8, "payoff": 13,
        "emotion": 7, "novelty": 8, "audio_energy": 3, "visual_energy": 2,
        "reaction": 4, "caption_suitability": 7, "platform_fit": 6,
        "context_completeness": 7, "retention": 4, "edit_confidence": 2,
        "technical": 1, "safety": 1,
    },
    # Interview: an answer clipped away from its question has to still make
    # sense on its own, so context_completeness carries real weight.
    "interview": {
        "hook": 12, "clarity": 12, "setup_efficiency": 7, "payoff": 12,
        "emotion": 8, "novelty": 7, "audio_energy": 3, "visual_energy": 3,
        "reaction": 5, "caption_suitability": 6, "platform_fit": 5,
        "context_completeness": 11, "retention": 4, "edit_confidence": 2,
        "technical": 2, "safety": 1,
    },
    # IRL: unscripted, so what happened in frame beats what was said about it.
    "irl": {
        "hook": 10, "clarity": 5, "setup_efficiency": 6, "payoff": 9,
        "emotion": 12, "novelty": 11, "audio_energy": 8, "visual_energy": 12,
        "reaction": 9, "caption_suitability": 3, "platform_fit": 5,
        "context_completeness": 3, "retention": 3, "edit_confidence": 2,
        "technical": 1, "safety": 1,
    },
    # Commentary: a take is only worth clipping if it is both sharp and new.
    "commentary": {
        "hook": 13, "clarity": 10, "setup_efficiency": 8, "payoff": 12,
        "emotion": 11, "novelty": 11, "audio_energy": 4, "visual_energy": 3,
        "reaction": 5, "caption_suitability": 6, "platform_fit": 5,
        "context_completeness": 5, "retention": 3, "edit_confidence": 2,
        "technical": 1, "safety": 1,
    },
    # Talking head: one static face — the words and the burned-in captions are
    # the entire visual interest.
    "talking_head": {
        "hook": 14, "clarity": 12, "setup_efficiency": 8, "payoff": 12,
        "emotion": 8, "novelty": 7, "audio_energy": 3, "visual_energy": 2,
        "reaction": 3, "caption_suitability": 10, "platform_fit": 6,
        "context_completeness": 6, "retention": 4, "edit_confidence": 2,
        "technical": 2, "safety": 1,
    },
    # Tutorial: a step cut off mid-explanation is worse than useless, so
    # completeness and clarity dominate and excitement barely counts.
    "tutorial": {
        "hook": 8, "clarity": 15, "setup_efficiency": 8, "payoff": 10,
        "emotion": 3, "novelty": 5, "audio_energy": 2, "visual_energy": 4,
        "reaction": 2, "caption_suitability": 9, "platform_fit": 5,
        "context_completeness": 17, "retention": 5, "edit_confidence": 3,
        "technical": 3, "safety": 1,
    },
    # Sports: the play and the crowd. Commentary is often unintelligible and
    # scoring it heavily would throw away the best moments.
    "sports": {
        "hook": 8, "clarity": 3, "setup_efficiency": 6, "payoff": 13,
        "emotion": 9, "novelty": 6, "audio_energy": 12, "visual_energy": 14,
        "reaction": 11, "caption_suitability": 2, "platform_fit": 6,
        "context_completeness": 3, "retention": 3, "edit_confidence": 2,
        "technical": 1, "safety": 1,
    },
    # Low dialogue: there is almost no speech to score, so picture and
    # soundtrack decide and the language sub-scores are near-zero weight.
    "low_dialogue": {
        "hook": 6, "clarity": 1, "setup_efficiency": 4, "payoff": 9,
        "emotion": 12, "novelty": 12, "audio_energy": 13, "visual_energy": 18,
        "reaction": 8, "caption_suitability": 1, "platform_fit": 6,
        "context_completeness": 2, "retention": 4, "edit_confidence": 2,
        "technical": 1, "safety": 1,
    },
    # Unknown: the fallback until content-type detection is confident. Flat
    # enough that a misdetection never costs much.
    "unknown": {
        "hook": 10, "clarity": 8, "setup_efficiency": 6, "payoff": 10,
        "emotion": 8, "novelty": 7, "audio_energy": 7, "visual_energy": 7,
        "reaction": 6, "caption_suitability": 5, "platform_fit": 6,
        "context_completeness": 6, "retention": 5, "edit_confidence": 4,
        "technical": 3, "safety": 2,
    },
}


def _normalise(raw: dict[str, float]) -> dict[str, float]:
    """Scale a weight row so it sums to exactly 1.0 over every sub-score."""
    filled = {name: float(raw.get(name, 0.0)) for name in SUB_SCORES}
    total = sum(filled.values())
    if total <= 0:  # a row of zeros would make `overall` meaningless
        return {name: 1.0 / len(SUB_SCORES) for name in SUB_SCORES}
    return {name: value / total for name, value in filled.items()}


PROFILES: dict[str, dict[str, float]] = {
    name: _normalise(row) for name, row in _RAW_PROFILES.items()
}


# --------------------------------------------------------------------------
# numeric helpers — every one of these is a NaN / divide-by-zero guard


def _num(value: Any, default: float = 0.0) -> float:
    """Coerce anything to a finite float. Strings, None and NaN become default."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return lo if value < lo else hi if value > hi else value


def _unit(value: float) -> float:
    return _clamp(value, 0.0, 1.0)


def _ramp01(value: float, lo: float, hi: float) -> float:
    """0.0 at or below `lo`, 1.0 at or above `hi`, linear in between."""
    if hi <= lo:
        return 0.0 if value <= lo else 1.0
    return _clamp((value - lo) / (hi - lo), 0.0, 1.0)


def _band_score(value: float, lo: float, hi: float,
                lo_fall: float, hi_fall: float) -> float:
    """100 inside [lo, hi], decaying linearly to 0 over the falloff distance."""
    if lo <= value <= hi:
        return 100.0
    distance = (lo - value) if value < lo else (value - hi)
    falloff = lo_fall if value < lo else hi_fall
    if falloff <= 0:
        return 0.0
    return _clamp(100.0 * (1.0 - distance / falloff))


def _getter(features: dict[str, Any]) -> Callable[[str], float]:
    def get(key: str) -> float:
        return _num(features.get(key, 0.0))
    return get


def _duration_of(cand: dict[str, Any], features: dict[str, Any]) -> float:
    """Clip length in seconds, from the candidate span first, features second."""
    start = _num(cand.get("start", cand.get("start_time", 0.0)))
    end = _num(cand.get("end", cand.get("end_time", 0.0)))
    span = end - start
    if span > 0:
        return span
    for source in (cand.get("duration"), features.get("duration")):
        value = _num(source)
        if value > 0:
            return value
    return 0.0


# --------------------------------------------------------------------------
# sub-scores


def platform_fit_score(duration: float, platform: str) -> float:
    """Distance from the platform's ideal duration band, on 0..100.

    Short-side falloff is tighter than the long side on purpose: a clip that
    ends before the viewer has understood it is unrecoverable, whereas one that
    runs a little long merely loses some watch-through.
    """
    lo, hi = PLATFORM_BANDS.get(str(platform or "").strip().lower(), _DEFAULT_BAND)
    width = max(1.0, hi - lo)
    return _band_score(duration, lo, hi,
                       lo_fall=max(6.0, 0.35 * width),
                       hi_fall=max(10.0, 0.75 * width))


def _compute_sub_scores(cand: dict[str, Any], features: dict[str, Any],
                        platform: str) -> dict[str, float]:
    g = _getter(features)
    duration = _duration_of(cand, features)

    wpm = g("speech_rate_wpm")
    # A conversational 130-190 wpm is what reads cleanly at speed; 0 wpm means
    # no speech was measured at all, which is not the same as slow speech.
    rate = _band_score(wpm, 130.0, 190.0, lo_fall=70.0, hi_fall=90.0) if wpm > 0 else 0.0

    wps = g("words_per_second")
    pace = _band_score(wps, 1.8, 3.4, lo_fall=1.5, hi_fall=1.6) if wps > 0 else 0.0

    # Cuts per second: some cutting is energy, constant cutting is noise.
    cuts = _band_score(g("scene_cut_rate"), 0.05, 0.50, lo_fall=0.05, hi_fall=1.00)

    # The payoff should sit in the back half but not on the very last frame.
    payoff_pos = _band_score(_unit(g("payoff_position")), 0.45, 0.85,
                             lo_fall=0.45, hi_fall=0.20)

    trend = _clamp(g("energy_trend"), -1.0, 1.0)
    filler = _unit(g("filler_ratio"))
    dead_air = _unit(g("dead_air_ratio"))

    # A window spent in an inventory screen scores full marks on motion and
    # detail — the panel is high-contrast and the cursor keeps moving — so
    # nothing else in this file would mark it down. The shot planner already
    # refuses to CUT to a menu; without this the window still gets ranked as a
    # top candidate and then renders as almost pure facecam, which throws away
    # the cross-cutting the gaming style exists for. Scaled, not gated: a clip
    # with one glance at the map should lose a little, not be disqualified.
    on_screen = 1.0 - 0.75 * _unit(g("game_ui_ratio"))

    scores = {
        "hook": (55.0 * _unit(g("hook_strength"))
                 + 25.0 * _unit(g("first_seconds_energy"))
                 + 20.0 * _unit(g("question_opening"))
                 - 20.0 * _unit(g("starts_mid_sentence"))),
        "clarity": (0.45 * rate
                    + 30.0 * _unit(g("speech_ratio"))
                    + 15.0 * (1.0 - filler)
                    + 10.0 * _unit(g("snr"))),
        "setup_efficiency": 100.0 * (1.0 - _ramp01(_unit(g("setup_ratio")), 0.20, 0.75)),
        "payoff": (60.0 * _unit(g("payoff_strength"))
                   + 40.0 * _unit(g("peak_prominence"))),
        "emotion": (45.0 * _unit(g("emotion_intensity"))
                    + 30.0 * _unit(g("laughter_score"))
                    + 25.0 * _unit(g("sentiment_magnitude"))),
        "novelty": (80.0 * _unit(g("novelty"))
                    + 20.0 * (1.0 - _unit(g("repetition_ratio")))),
        "audio_energy": (50.0 * _unit(g("audio_rms_mean"))
                         + 30.0 * _unit(g("audio_peak_ratio"))
                         + 20.0 * _unit(g("audio_dynamic_range"))),
        "visual_energy": on_screen * (45.0 * _unit(g("motion_mean"))
                                      + 30.0 * _unit(g("motion_peak"))
                                      + 0.25 * cuts),
        "reaction": (55.0 * _unit(g("reaction_score"))
                     + 25.0 * _unit(g("post_payoff_energy"))
                     + 20.0 * _unit(g("laughter_score"))),
        "caption_suitability": (0.60 * pace
                                + 25.0 * _unit(g("word_confidence"))
                                + 15.0 * (1.0 - filler)),
        "platform_fit": platform_fit_score(duration, platform),
        "context_completeness": (45.0 * _unit(g("self_contained"))
                                 + 25.0 * (1.0 - _unit(g("starts_mid_sentence")))
                                 + 20.0 * (1.0 - _unit(g("ends_mid_sentence")))
                                 + 10.0 * (1.0 - _unit(g("pronoun_dependency")))),
        "retention": (45.0 * ((trend + 1.0) / 2.0)
                      + 30.0 * (1.0 - dead_air)
                      + 0.25 * payoff_pos),
        "edit_confidence": (55.0 * _unit(g("boundary_confidence"))
                            + 30.0 * _unit(g("snap_quality"))
                            + 15.0 * (1.0 - dead_air)),
        "technical": (35.0 * _unit(g("loudness_ok"))
                      + 25.0 * (1.0 - _unit(g("clipping_ratio")))
                      + 20.0 * (1.0 - _unit(g("blur_score")))
                      + 20.0 * _unit(g("resolution_ok"))),
        # Starts clean and is only ever debited — an absent signal must not
        # read as "this clip is unsafe".
        "safety": 100.0 - (55.0 * _unit(g("profanity_ratio") * 5.0)
                           + 45.0 * _unit(g("flagged_terms") / 3.0)),
    }
    return {name: round(_clamp(scores[name]), 1) for name in SUB_SCORES}


# --------------------------------------------------------------------------
# explanation

# Clauses are written to slot into "<Clause>; <clause>." — each is an
# independent statement of fact, not a claim about how the clip will perform.
_STRONG = {
    "hook": "the opening line is strong",
    "clarity": "the speech is clear and easy to follow",
    "setup_efficiency": "it reaches the point quickly",
    "payoff": "it lands a clear payoff",
    "emotion": "the delivery carries visible emotion",
    "novelty": "it covers ground the rest of the source does not",
    "audio_energy": "audio energy is high throughout",
    "visual_energy": "there is steady on-screen motion",
    "reaction": "a reaction follows the peak",
    "caption_suitability": "the pacing suits burned-in captions",
    "platform_fit": "the length sits inside the target platform's band",
    "context_completeness": "it stands on its own without the surrounding video",
    "retention": "the energy holds to the end",
    "edit_confidence": "both cut points sit on natural boundaries",
    "technical": "the audio and picture are technically clean",
    "safety": "no flagged language was detected",
}

_WEAK = {
    "hook": "The opening is flat",
    "clarity": "The speech is hard to follow",
    "setup_efficiency": "Setup is long",
    "payoff": "There is no clear payoff",
    "emotion": "The delivery is flat",
    "novelty": "It repeats material found elsewhere in the source",
    "audio_energy": "Audio stays quiet",
    "visual_energy": "There is little on-screen motion",
    "reaction": "Nothing follows the peak",
    "caption_suitability": "The pacing is awkward for captions",
    "platform_fit": "The length is outside the target platform's band",
    "context_completeness": "It leans on context from outside the clip",
    "retention": "The energy drops before the end",
    "edit_confidence": "The cut points are uncertain",
    "technical": "The audio or picture has technical problems",
    "safety": "It contains flagged language",
}

# Hygiene sub-scores: they sit at 100 most of the time, so quoting them as a
# clip's *strength* is noise. They are still worth naming when they are bad.
_HYGIENE = ("platform_fit", "technical", "safety", "edit_confidence")


def _fmt_duration(seconds: float) -> str:
    total = int(round(max(0.0, seconds)))
    if total < 60:
        return f"{total}s"
    return f"{total // 60}m{total % 60:02d}s"


def explain(sub_scores: dict, cand: dict, features: dict | None = None) -> str:
    """One or two factual sentences naming the strongest and weakest signals.

    No superlatives and no performance claims — the user is deciding whether to
    watch the preview, and an inflated reason costs them that trust once.
    """
    values = {name: _num((sub_scores or {}).get(name, 0.0)) for name in SUB_SCORES}
    weak = dict(_WEAK)
    # visual_energy is the one sub-score with two different ways to be low, and
    # the default clause is a lie about the other one: an inventory screen has
    # plenty of motion. Say what is actually wrong with the window.
    if _unit(_num((features or {}).get("game_ui_ratio", 0.0))) >= 0.5:
        weak["visual_energy"] = "The game spends much of this window in a menu"
    duration = _fmt_duration(_duration_of(cand or {}, {}))

    # Ties break on SUB_SCORES order so the same clip always reads the same.
    order = sorted(SUB_SCORES, key=lambda n: (-values[n], SUB_SCORES.index(n)))
    content = [n for n in order if n not in _HYGIENE] or list(order)
    top = [n for n in content[:2] if values[n] > 0.0]

    if not top:
        return f"No signal scored above zero on this {duration} clip."

    if len(top) == 1:
        first = f"{_STRONG[top[0]].capitalize()}."
    else:
        first = f"{_STRONG[top[0]].capitalize()}; {_STRONG[top[1]]}."

    weakest = order[-1]
    best = values[top[0]]
    # Only call out a weakness that is both low in absolute terms and clearly
    # out of line with the rest of the clip.
    if values[weakest] < 45.0 and (best - values[weakest]) >= 25.0:
        return f"{first} {weak[weakest]} for a {duration} clip."
    return f"{first} Runs {duration}."


# --------------------------------------------------------------------------


def score_candidate(cand: dict, features: dict, *, profile: str,
                    platform: str) -> dict:
    """Score one candidate against a content-type profile and a platform.

    Returns {'overall': 0..100, 'sub_scores': {name: 0..100}, 'reason': str}.
    `profile` selects the weight row; `platform` only moves platform_fit.
    """
    cand = cand or {}
    features = features or {}

    key = str(profile or "").strip().lower()
    weights = PROFILES.get(key)
    if weights is None:
        logger.debug("unknown scoring profile %r, falling back to 'unknown'", profile)
        weights = PROFILES["unknown"]

    sub_scores = _compute_sub_scores(cand, features, platform)
    overall = sum(sub_scores[name] * weights[name] for name in SUB_SCORES)

    return {
        "overall": round(_clamp(overall), 1),
        "sub_scores": sub_scores,
        "reason": explain(sub_scores, cand, features),
    }
