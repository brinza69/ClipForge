"""
ClipForge — Auto Story Doodle: subtitle generation.

Builds a plain SRT file from per-scene REAL audio durations (cumulative
timeline) and returns ffmpeg `subtitles=...:force_style=...` style strings
for the supported subtitle modes.

Modes: "none" (default — SRT is still written, nothing is burned),
"minimal_bottom", "youtube_clean", "tiktok_big".

CRITICAL libass gotcha (the source of the original huge-caption bug):
force_style Fontsize/Margin values are interpreted on libass's default
PlayRes grid (384x288) for external SRT files, NOT in output pixels. A
"Fontsize=45" burned into 1080p is scaled up 1080/288 = 3.75x -> ~170px
text that covers the whole frame. All px targets below are converted to
PlayRes units before being emitted.

Second gotcha: with BorderStyle=3 (opaque box) libass draws the box using
OutlineColour, not BackColour — so the translucent box colour must go into
OutlineColour (the old code left OutlineColour opaque black => giant solid
black box).
"""

from __future__ import annotations

import math
import re
from typing import Dict, List, Tuple

# libass default script grid used for external SRT files.
_PLAYRES_X = 384.0
_PLAYRES_Y = 288.0

SUBTITLE_MODES = ("none", "minimal_bottom", "youtube_clean", "tiktok_big")

# Legacy names from earlier storyboards.
_MODE_ALIASES = {
    "minimal": "minimal_bottom",
    "tiktok_bold": "tiktok_big",
}


def normalize_subtitle_mode(mode) -> str:
    """Map any stored/legacy/unknown value onto a supported mode."""
    m = str(mode or "").strip().lower()
    m = _MODE_ALIASES.get(m, m)
    return m if m in SUBTITLE_MODES else "none"


# Default export filename per mode, so each render style has its own output
# and re-rendering one style never clobbers another.
OUTPUT_NAMES = {
    "none": "final_video_no_subtitles.mp4",
    "minimal_bottom": "final_video_minimal_subtitles.mp4",
    "youtube_clean": "final_video_youtube_clean.mp4",
    "tiktok_big": "final_video_tiktok_big.mp4",
}


# ── SRT building ─────────────────────────────────────────────────────────────

def _srt_timestamp(seconds: float) -> str:
    """Format seconds as SRT timestamp: HH:MM:SS,mmm"""
    if seconds < 0:
        seconds = 0.0
    total_ms = round(seconds * 1000)
    hours, rem_ms = divmod(total_ms, 3_600_000)
    minutes, rem_ms = divmod(rem_ms, 60_000)
    secs, millis = divmod(rem_ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


_PHRASE_SPLIT_RE = re.compile(r"(?<=[.!?;:,…—–])\s+")

MAX_CAPTION_WORDS = 8
MIN_CAPTION_WORDS = 3


def split_phrases(text: str, max_words: int = MAX_CAPTION_WORDS,
                  min_words: int = MIN_CAPTION_WORDS) -> List[str]:
    """Split narration into short readable caption phrases (~4-8 words).

    1. Split at punctuation boundaries (keeping the punctuation).
    2. Window any long fragment into near-equal <=max_words chunks.
    3. Merge tiny fragments into a neighbour so captions don't flicker.
    """
    frags = [f.strip() for f in _PHRASE_SPLIT_RE.split((text or "").strip()) if f.strip()]
    if not frags:
        return []

    windowed: List[List[str]] = []
    for frag in frags:
        words = frag.split()
        if len(words) <= max_words:
            windowed.append(words)
        else:
            n_chunks = math.ceil(len(words) / max_words)
            size = math.ceil(len(words) / n_chunks)
            for i in range(0, len(words), size):
                windowed.append(words[i:i + size])

    merged: List[List[str]] = []
    for words in windowed:
        if (
            merged
            and (len(words) < min_words or len(merged[-1]) < min_words)
            and len(merged[-1]) + len(words) <= max_words
        ):
            merged[-1] = merged[-1] + words
        else:
            merged.append(words)

    return [" ".join(w) for w in merged]


def build_srt(scenes: List[Dict]) -> str:
    """
    Build an SRT string using cumulative REAL audio_duration timings.

    Each scene's FULL narration is split into short phrases (~4-8 words) and
    the scene's audio_duration is distributed across them proportionally to
    word count — so captions track speech instead of parking one huge block
    of text on screen for the whole scene.
    """
    lines: List[str] = []
    cursor = 0.0
    idx = 1
    for scene in scenes:
        duration = scene.get("audio_duration")
        if duration is None:
            duration = scene.get("estimated_duration") or 0.0
        duration = float(duration or 0.0)
        if duration <= 0:
            continue

        scene_start = cursor
        cursor += duration

        text = (scene.get("narration") or scene.get("subtitle") or "").strip()
        if not text:
            continue

        chunks = split_phrases(text)
        if not chunks:
            continue
        weights = [len(c.split()) for c in chunks]
        total_weight = sum(weights) or 1

        t = scene_start
        for chunk, w in zip(chunks, weights):
            chunk_dur = duration * (w / total_weight)
            start, end = t, t + chunk_dur
            t = end
            lines.append(str(idx))
            lines.append(f"{_srt_timestamp(start)} --> {_srt_timestamp(min(end, cursor))}")
            lines.append(chunk)
            lines.append("")
            idx += 1

    return "\n".join(lines) + ("\n" if lines else "")


# ── force_style presets ──────────────────────────────────────────────────────

def _ass_alpha(opacity: float) -> str:
    """ASS alpha byte from an opacity fraction (0=transparent, 1=opaque)."""
    a = max(0, min(255, round((1.0 - opacity) * 255)))
    return f"{a:02X}"


def _orientation(width: int, height: int) -> str:
    if height > width:
        return "portrait"
    if height == width:
        return "square"
    return "landscape"


# Pixel targets per (mode, orientation) at the canonical output sizes
# (1920x1080 / 1080x1920 / 1080x1080). Converted to PlayRes units below.
#   font_px, margin_bottom_px, side_margin_frac (of width), box_opacity
_PRESETS = {
    "minimal_bottom": {
        # Small, subtle, very bottom — max ~70% width, never near the center.
        "landscape": (38, 65, 0.15, 0.40),
        "portrait": (48, 150, 0.10, 0.40),
        "square": (40, 80, 0.125, 0.40),
    },
    "youtube_clean": {
        "landscape": (44, 80, 0.12, 0.55),
        "portrait": (54, 150, 0.10, 0.55),
        "square": (46, 90, 0.12, 0.55),
    },
    "tiktok_big": {
        # Big bold lower-third captions — opt-in only, never the default.
        "landscape": (62, 170, 0.10, 0.0),
        "portrait": (80, 300, 0.08, 0.0),
        "square": (66, 200, 0.10, 0.0),
    },
}

_CANONICAL_HEIGHT = {"landscape": 1080, "portrait": 1920, "square": 1080}


def subtitle_style_args(style: str, resolution: Tuple[int, int]) -> str:
    """
    Return a force_style=... body (WITHOUT the leading "force_style=") for
    ffmpeg's `subtitles` filter. All values are emitted in libass PlayRes
    units (384x288 grid) so the on-screen size matches the px targets.
    """
    mode = normalize_subtitle_mode(style)
    if mode == "none":
        return ""

    width, height = resolution
    orient = _orientation(width, height)
    font_px, margin_v_px, side_frac, box_opacity = _PRESETS[mode][orient]

    # Scale px targets if the output deviates from the canonical size.
    scale = height / _CANONICAL_HEIGHT[orient]
    font_px *= scale
    margin_v_px *= scale

    # px -> PlayRes units.
    fontsize = round(font_px * _PLAYRES_Y / height, 1)
    margin_v = round(margin_v_px * _PLAYRES_Y / height)
    margin_lr = round(side_frac * width * _PLAYRES_X / width)  # = side_frac * 384

    if mode == "tiktok_big":
        return (
            "FontName=Arial Black,"
            f"Fontsize={fontsize},"
            "PrimaryColour=&H00FFFFFF,"
            "OutlineColour=&H00000000,"
            "BackColour=&H80000000,"
            "Bold=1,"
            "BorderStyle=1,"
            "Outline=2.5,"
            "Shadow=1,"
            "Alignment=2,"
            f"MarginV={margin_v},"
            f"MarginL={margin_lr},MarginR={margin_lr},"
            "WrapStyle=1"
        )

    # Box styles (minimal_bottom / youtube_clean): translucent black box
    # snug behind each line. NOTE: with BorderStyle=3 the box colour comes
    # from OutlineColour; Outline acts as box padding.
    box_colour = f"&H{_ass_alpha(box_opacity)}000000"
    padding = 1.5 if mode == "minimal_bottom" else 2.5
    return (
        "FontName=Arial,"
        f"Fontsize={fontsize},"
        "PrimaryColour=&H00FFFFFF,"
        f"OutlineColour={box_colour},"
        f"BackColour={box_colour},"
        "Bold=0,"
        "BorderStyle=3,"
        f"Outline={padding},"
        "Shadow=0,"
        "Alignment=2,"
        f"MarginV={margin_v},"
        f"MarginL={margin_lr},MarginR={margin_lr},"
        "WrapStyle=1"
    )
