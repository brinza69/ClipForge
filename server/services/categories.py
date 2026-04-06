"""
ClipForge — Content Category Specialization Service

Defines presets and strategies for different content categories:
  - podcast / interview
  - livestream / streaming clips
  - conference / keynote / presentation
  - creator storytelling / talking-head
  - reaction / commentary
  - general

Each category adjusts:
  - hook generation strategy
  - subtitle pacing
  - clip boundary selection preferences
  - speaker/screen framing balance
  - transition usage
  - caption style
  - upload strategy recommendations
"""

import logging
import re
from typing import Dict, Any, List, Optional

logger = logging.getLogger("clipforge.categories")


# ---------------------------------------------------------------------------
# Category Presets — each key adjusts the pipeline behavior
# ---------------------------------------------------------------------------

CATEGORY_PRESETS: Dict[str, Dict[str, Any]] = {
    "podcast": {
        "name": "Podcast / Interview",
        "description": "Long-form conversation content with 1-2 speakers",
        "account_type": "podcast_clips",

        # Clip extraction preferences
        "preferred_duration": (60, 90),
        "min_duration": 45,
        "max_duration": 120,
        "prefer_question_starts": True,      # Start at Q&A moments
        "prefer_revelation_moments": True,    # Start at surprise/reveal
        "avoid_filler_openings": True,        # Skip "so basically..."
        "allow_cross_speaker": True,          # Include both speakers

        # Hook strategy
        "hook_style": "curiosity_question",   # Questions work best
        "hook_max_words": 12,
        "hook_patterns_boost": [
            r"\bwhat (?:nobody|no one) tells you\b",
            r"\bhere'?s (?:the|what'?s) (?:thing|crazy)\b",
            r"\bmost people (?:don'?t|miss|ignore)\b",
        ],

        # Caption style
        "preferred_caption_preset": "bold_impact",
        "caption_words_per_line": 3,
        "caption_uppercase": True,

        # Framing
        "reframe_mode": "auto",
        "framing_bias": "speaker_focus",      # Keep speaker centered
        "presentation_balance": 0.0,          # No presentation content

        # Transitions
        "fade_in_duration": 0.4,
        "fade_out_duration": 0.3,

        # Upload
        "default_hashtags": ["#podcast", "#podcastclips", "#fyp", "#viral"],
        "default_cta": "Full episode in bio!",
        "best_posting_times": ["7am", "12pm", "7pm"],
    },

    "interview": {
        "name": "Interview",
        "description": "Structured interview with host and guest",
        "account_type": "interview_clips",

        "preferred_duration": (60, 90),
        "min_duration": 45,
        "max_duration": 120,
        "prefer_question_starts": True,
        "prefer_revelation_moments": True,
        "avoid_filler_openings": True,
        "allow_cross_speaker": True,

        "hook_style": "bold_claim",
        "hook_max_words": 10,
        "hook_patterns_boost": [
            r"\bthe truth (?:is|about)\b",
            r"\beveryone is wrong about\b",
            r"\bnobody is talking about\b",
        ],

        "preferred_caption_preset": "clean_minimal",
        "caption_words_per_line": 3,
        "caption_uppercase": False,

        "reframe_mode": "auto",
        "framing_bias": "speaker_focus",
        "presentation_balance": 0.0,

        "fade_in_duration": 0.4,
        "fade_out_duration": 0.3,

        "default_hashtags": ["#interview", "#conversation", "#fyp"],
        "default_cta": "Watch the full interview - link in bio",
        "best_posting_times": ["8am", "1pm", "8pm"],
    },

    "livestream": {
        "name": "Livestream / Streaming",
        "description": "Live streaming content, reactions, gaming moments",
        "account_type": "stream_clips",

        "preferred_duration": (30, 75),
        "min_duration": 20,
        "max_duration": 90,
        "prefer_question_starts": False,
        "prefer_revelation_moments": True,
        "avoid_filler_openings": True,
        "allow_cross_speaker": False,

        "hook_style": "reaction_moment",
        "hook_max_words": 8,
        "hook_patterns_boost": [
            r"\bwait (?:for it|till you)\b",
            r"\bit gets (?:worse|better|crazier)\b",
            r"\bwatch (?:this|what happens)\b",
        ],

        "preferred_caption_preset": "neon_pop",
        "caption_words_per_line": 2,
        "caption_uppercase": True,

        "reframe_mode": "auto",
        "framing_bias": "screen_balance",
        "presentation_balance": 0.3,

        "fade_in_duration": 0.2,
        "fade_out_duration": 0.2,

        "default_hashtags": ["#streaming", "#clips", "#viral", "#fyp"],
        "default_cta": "",
        "best_posting_times": ["3pm", "7pm", "10pm"],
    },

    "conference": {
        "name": "Conference / Keynote / Presentation",
        "description": "Professional talks, keynotes, panel discussions",
        "account_type": "knowledge_clips",

        "preferred_duration": (60, 90),
        "min_duration": 45,
        "max_duration": 120,
        "prefer_question_starts": False,
        "prefer_revelation_moments": True,
        "avoid_filler_openings": True,
        "allow_cross_speaker": False,

        "hook_style": "insight_reveal",
        "hook_max_words": 12,
        "hook_patterns_boost": [
            r"\bthis changes everything\b",
            r"\bthe (?:biggest|worst|best|most important)\b",
            r"\bthink about (?:this|that)\b",
        ],

        "preferred_caption_preset": "boxed_white",
        "caption_words_per_line": 4,
        "caption_uppercase": False,

        "reframe_mode": "auto",
        "framing_bias": "presentation_balance",
        "presentation_balance": 0.35,

        "fade_in_duration": 0.5,
        "fade_out_duration": 0.4,

        "default_hashtags": ["#keynote", "#conference", "#knowledge", "#fyp"],
        "default_cta": "Full talk linked in bio",
        "best_posting_times": ["8am", "12pm", "6pm"],
    },

    "storytelling": {
        "name": "Creator Storytelling / Talking-Head",
        "description": "Single creator telling stories, sharing experiences",
        "account_type": "creator_clips",

        "preferred_duration": (60, 90),
        "min_duration": 45,
        "max_duration": 100,
        "prefer_question_starts": False,
        "prefer_revelation_moments": True,
        "avoid_filler_openings": True,
        "allow_cross_speaker": False,

        "hook_style": "curiosity_gap",
        "hook_max_words": 10,
        "hook_patterns_boost": [
            r"\bso (?:basically|essentially|here'?s what happened)\b",
            r"\blet me (?:tell|explain|show) you\b",
            r"\bi (?:just )?found out\b",
        ],

        "preferred_caption_preset": "viral_gradient",
        "caption_words_per_line": 2,
        "caption_uppercase": True,

        "reframe_mode": "auto",
        "framing_bias": "speaker_focus",
        "presentation_balance": 0.0,

        "fade_in_duration": 0.3,
        "fade_out_duration": 0.3,

        "default_hashtags": ["#storytime", "#creator", "#fyp", "#viral"],
        "default_cta": "Follow for more stories!",
        "best_posting_times": ["7am", "12pm", "9pm"],
    },

    "reaction": {
        "name": "Reaction / Commentary",
        "description": "Reaction videos, commentary on events/media",
        "account_type": "reaction_clips",

        "preferred_duration": (30, 75),
        "min_duration": 20,
        "max_duration": 90,
        "prefer_question_starts": False,
        "prefer_revelation_moments": True,
        "avoid_filler_openings": True,
        "allow_cross_speaker": False,

        "hook_style": "reaction_moment",
        "hook_max_words": 8,
        "hook_patterns_boost": [
            r"\bwait\b",
            r"\bno way\b",
            r"\bwhat\b",
            r"\bare you (?:serious|kidding)\b",
        ],

        "preferred_caption_preset": "karaoke_yellow",
        "caption_words_per_line": 3,
        "caption_uppercase": True,

        "reframe_mode": "auto",
        "framing_bias": "speaker_focus",
        "presentation_balance": 0.2,

        "fade_in_duration": 0.2,
        "fade_out_duration": 0.2,

        "default_hashtags": ["#reaction", "#commentary", "#fyp", "#viral"],
        "default_cta": "",
        "best_posting_times": ["12pm", "5pm", "9pm"],
    },

    "general": {
        "name": "General",
        "description": "Generic content — uses balanced defaults",
        "account_type": "general",

        "preferred_duration": (60, 90),
        "min_duration": 45,
        "max_duration": 100,
        "prefer_question_starts": False,
        "prefer_revelation_moments": True,
        "avoid_filler_openings": True,
        "allow_cross_speaker": True,

        "hook_style": "curiosity_gap",
        "hook_max_words": 10,
        "hook_patterns_boost": [],

        "preferred_caption_preset": "bold_impact",
        "caption_words_per_line": 3,
        "caption_uppercase": True,

        "reframe_mode": "auto",
        "framing_bias": "speaker_focus",
        "presentation_balance": 0.15,

        "fade_in_duration": 0.4,
        "fade_out_duration": 0.3,

        "default_hashtags": ["#fyp", "#viral", "#foryou"],
        "default_cta": "",
        "best_posting_times": ["7am", "12pm", "7pm"],
    },
}


# ---------------------------------------------------------------------------
# Category detection — auto-classify content from transcript/metadata
# ---------------------------------------------------------------------------

def detect_category(
    transcript_text: str = "",
    title: str = "",
    description: str = "",
    duration: float = 0,
    channel_name: str = "",
) -> str:
    """
    Auto-detect the content category from available metadata.

    Uses keyword matching and heuristics. Returns a category key.
    """
    combined = f"{title} {description} {channel_name}".lower()
    text_lower = transcript_text.lower()[:2000]  # Only check first 2000 chars

    scores: Dict[str, float] = {k: 0.0 for k in CATEGORY_PRESETS}

    # --- Title/description keyword matching ---
    podcast_kw = ["podcast", "episode", "ep.", "ep ", "conversation with", "interview with", "talks to", "sits down"]
    interview_kw = ["interview", "q&a", "asks", "answers", "guest", "host"]
    livestream_kw = ["stream", "live", "gaming", "twitch", "kick", "gameplay"]
    conference_kw = ["keynote", "conference", "talk", "presentation", "summit", "ted", "panel"]
    storytelling_kw = ["story", "storytime", "experience", "happened to me", "telling you"]
    reaction_kw = ["reaction", "reacts", "responding", "commentary", "watching"]

    for kw in podcast_kw:
        if kw in combined:
            scores["podcast"] += 15
    for kw in interview_kw:
        if kw in combined:
            scores["interview"] += 15
    for kw in livestream_kw:
        if kw in combined:
            scores["livestream"] += 15
    for kw in conference_kw:
        if kw in combined:
            scores["conference"] += 15
    for kw in storytelling_kw:
        if kw in combined:
            scores["storytelling"] += 10
    for kw in reaction_kw:
        if kw in combined:
            scores["reaction"] += 15

    # --- Transcript patterns ---
    # Multi-speaker indicators suggest podcast/interview
    speaker_markers = len(re.findall(r'\b(?:speaker \d|host|guest|interviewer)\b', text_lower))
    if speaker_markers > 2:
        scores["podcast"] += 10
        scores["interview"] += 10

    # Question density suggests Q&A/interview
    questions = len(re.findall(r'\?', text_lower))
    if questions > 5:
        scores["interview"] += 5
        scores["podcast"] += 3

    # Duration heuristics
    if duration > 3600:  # > 1 hour: likely podcast/interview
        scores["podcast"] += 10
        scores["interview"] += 5
    elif duration > 1800:  # > 30 min
        scores["podcast"] += 5
        scores["conference"] += 5
    elif duration < 600:  # < 10 min
        scores["reaction"] += 5
        scores["storytelling"] += 5

    # Find the best match
    best = max(scores, key=scores.get)

    # If nothing scored significantly, default to general
    if scores[best] < 10:
        return "general"

    return best


def get_category_config(category: str) -> Dict[str, Any]:
    """Get the full configuration preset for a category."""
    return CATEGORY_PRESETS.get(category, CATEGORY_PRESETS["general"])


def get_category_list() -> List[Dict[str, str]]:
    """Get a list of available categories for UI display."""
    return [
        {"id": k, "name": v["name"], "description": v["description"]}
        for k, v in CATEGORY_PRESETS.items()
    ]
