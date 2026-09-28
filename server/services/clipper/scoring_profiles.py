"""
ClipForge — AI Stream Clipper: the weight rows, and only the weight rows.

Split from scoring.py when that file crossed the repo's 500-line limit. Nothing
was rewritten: the rows, the comments above them and `_normalise` are the same
bytes they were, because the comments here ARE the record of what was measured
on which source. `scripts/score_contribution.py` was run on the gaming profile
before and after the move and reports the same contribution for every
sub-score, which is the only evidence that a split of a weight table is safe.

Each row sums to 100 by hand for readability; `_normalise()` divides by the
actual sum, so an edit can never silently change the meaning of the score.
"""

from __future__ import annotations

SUB_SCORES = (
    "hook", "clarity", "setup_efficiency", "payoff", "emotion", "novelty",
    "audio_energy", "visual_energy", "reaction", "caption_suitability",
    "platform_fit", "context_completeness", "retention", "edit_confidence",
    "technical", "safety",
)

# Ideal duration band per platform, in seconds.
#
# `platform_fit` is still COMPUTED and reported — the breakdown showing "60s,
# TikTok prefers under 45" is worth knowing — but it carries ZERO WEIGHT in
# every profile as of 2026-08-17, and that is a measurement rather than taste.
#
# Measured over 1073 real candidates from four projects, with the gaming
# profile's weights:
#
#   * platform_fit was the LARGEST single contributor to how the board is
#     ordered — 2.10 of the 16.22 points of spread the ranking has to work
#     with, 13% of all discrimination.
#   * and it is almost binary: 798 candidates at 100, 120 at 0, 155 spread
#     between. What it contributed was the answer to "is the duration in band",
#     not a judgement about the clip.
#   * worse, the generator and the band DISAGREE. `clipper_max_clip_s` is 90 s
#     and the TikTok band ends at 45, so 279 of those 1073 candidates — 26% —
#     were produced by the pipeline exactly as asked and then docked 6 points
#     by the scorer for being that long.
#   * the practical effect: a 50 s clip lost 6 points to a 44 s one, while
#     `payoff` — whether the moment has a point at all — contributes 1.14 in
#     total. Duration outweighed content five to one.
#
# Zeroing the weight rather than deleting the sub-score keeps the number in the
# breakdown, and `_normalise()` divides by the real sum so the freed 5-6% is
# redistributed across the content sub-scores proportionally — no new opinion
# about what matters, just one thing that should not have had a vote losing it.
#
# The lower bound is 15 s everywhere: shorter than that and none of these
# surfaces give a clip a second look. That is a filter's job, and
# `clipper_min_clip_s` already enforces it at generation.
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
        "reaction": 11, "caption_suitability": 3, "platform_fit": 0,
        "context_completeness": 4, "retention": 5, "edit_confidence": 3,
        "technical": 2, "safety": 2,
    },
    # Podcast: nothing happens on screen, so an idea has to open well and land.
    "podcast": {
        "hook": 14, "clarity": 13, "setup_efficiency": 8, "payoff": 13,
        "emotion": 7, "novelty": 8, "audio_energy": 3, "visual_energy": 2,
        "reaction": 4, "caption_suitability": 7, "platform_fit": 0,
        "context_completeness": 7, "retention": 4, "edit_confidence": 2,
        "technical": 1, "safety": 1,
    },
    # Interview: an answer clipped away from its question has to still make
    # sense on its own, so context_completeness carries real weight.
    "interview": {
        "hook": 12, "clarity": 12, "setup_efficiency": 7, "payoff": 12,
        "emotion": 8, "novelty": 7, "audio_energy": 3, "visual_energy": 3,
        "reaction": 5, "caption_suitability": 6, "platform_fit": 0,
        "context_completeness": 11, "retention": 4, "edit_confidence": 2,
        "technical": 2, "safety": 1,
    },
    # IRL: unscripted, so what happened in frame beats what was said about it.
    "irl": {
        "hook": 10, "clarity": 5, "setup_efficiency": 6, "payoff": 9,
        "emotion": 12, "novelty": 11, "audio_energy": 8, "visual_energy": 12,
        "reaction": 9, "caption_suitability": 3, "platform_fit": 0,
        "context_completeness": 3, "retention": 3, "edit_confidence": 2,
        "technical": 1, "safety": 1,
    },
    # Commentary: a take is only worth clipping if it is both sharp and new.
    "commentary": {
        "hook": 13, "clarity": 10, "setup_efficiency": 8, "payoff": 12,
        "emotion": 11, "novelty": 11, "audio_energy": 4, "visual_energy": 3,
        "reaction": 5, "caption_suitability": 6, "platform_fit": 0,
        "context_completeness": 5, "retention": 3, "edit_confidence": 2,
        "technical": 1, "safety": 1,
    },
    # Talking head: one static face — the words and the burned-in captions are
    # the entire visual interest.
    "talking_head": {
        "hook": 14, "clarity": 12, "setup_efficiency": 8, "payoff": 12,
        "emotion": 8, "novelty": 7, "audio_energy": 3, "visual_energy": 2,
        "reaction": 3, "caption_suitability": 10, "platform_fit": 0,
        "context_completeness": 6, "retention": 4, "edit_confidence": 2,
        "technical": 2, "safety": 1,
    },
    # Tutorial: a step cut off mid-explanation is worse than useless, so
    # completeness and clarity dominate and excitement barely counts.
    "tutorial": {
        "hook": 8, "clarity": 15, "setup_efficiency": 8, "payoff": 10,
        "emotion": 3, "novelty": 5, "audio_energy": 2, "visual_energy": 4,
        "reaction": 2, "caption_suitability": 9, "platform_fit": 0,
        "context_completeness": 17, "retention": 5, "edit_confidence": 3,
        "technical": 3, "safety": 1,
    },
    # Sports: the play and the crowd. Commentary is often unintelligible and
    # scoring it heavily would throw away the best moments.
    "sports": {
        "hook": 8, "clarity": 3, "setup_efficiency": 6, "payoff": 13,
        "emotion": 9, "novelty": 6, "audio_energy": 12, "visual_energy": 14,
        "reaction": 11, "caption_suitability": 2, "platform_fit": 0,
        "context_completeness": 3, "retention": 3, "edit_confidence": 2,
        "technical": 1, "safety": 1,
    },
    # Low dialogue: there is almost no speech to score, so picture and
    # soundtrack decide and the language sub-scores are near-zero weight.
    "low_dialogue": {
        "hook": 6, "clarity": 1, "setup_efficiency": 4, "payoff": 9,
        "emotion": 12, "novelty": 12, "audio_energy": 13, "visual_energy": 18,
        "reaction": 8, "caption_suitability": 1, "platform_fit": 0,
        "context_completeness": 2, "retention": 4, "edit_confidence": 2,
        "technical": 1, "safety": 1,
    },
    # Unknown: the fallback until content-type detection is confident. Flat
    # enough that a misdetection never costs much.
    "unknown": {
        "hook": 10, "clarity": 8, "setup_efficiency": 6, "payoff": 10,
        "emotion": 8, "novelty": 7, "audio_energy": 7, "visual_energy": 7,
        "reaction": 6, "caption_suitability": 5, "platform_fit": 0,
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
