"""
ClipForge — Auto Story Doodle: script_generator helpers

Pure, network-free helpers used by script_generator.py:
  - narration -> scene-sized chunk splitting (deterministic pre-split)
  - LLM JSON validation/normalization (tags, scenes)

Split out of script_generator.py to keep files under the 500-line limit
(CLAUDE.md rule). Internal to the doodle.script_generator module — not
part of the PRP's public contract, but a plain module so it's easy to
unit-test in isolation.
"""

from __future__ import annotations

import math
import re
from typing import Any

# Narration pacing: ~150 words per minute of spoken narration.
WORDS_PER_MINUTE = 150

# Per-scene narration duration bounds (seconds) after normalization.
MIN_SCENE_DURATION = 1.5
MAX_SCENE_DURATION = 6.0

# Subtitle length cap (chars) — kept short for on-screen readability.
SUBTITLE_MAX_CHARS = 42

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def split_narration_into_chunks(narration: str, frame_interval: float) -> list[str]:
    """Greedy split of narration into ~frame_interval-second chunks.

    Rough pre-split (by word count at ~150wpm) so stage 2 always receives a
    bounded, sequential list of narration pieces to assign subtitles/prompts
    to — the LLM does not have to invent scene boundaries from scratch, only
    refine wording per piece and write prompts.
    """
    words = narration.split()
    if not words:
        return []

    words_per_scene = max(3, round(frame_interval / 60 * WORDS_PER_MINUTE))

    # Prefer sentence-aligned chunks where sentences are short; otherwise
    # fall back to fixed word windows so no chunk exceeds ~2x target.
    sentences = [s.strip() for s in _SENT_SPLIT.split(narration) if s.strip()]
    chunks: list[str] = []
    buf: list[str] = []
    buf_words = 0

    for sent in sentences:
        sw = sent.split()
        if len(sw) > words_per_scene * 2:
            # Long sentence — flush current buffer, then window this sentence.
            if buf:
                chunks.append(" ".join(buf))
                buf, buf_words = [], 0
            for i in range(0, len(sw), words_per_scene):
                chunks.append(" ".join(sw[i : i + words_per_scene]))
            continue

        if buf_words + len(sw) > words_per_scene and buf:
            chunks.append(" ".join(buf))
            buf, buf_words = sw, len(sw)
        else:
            buf.extend(sw)
            buf_words += len(sw)

    if buf:
        chunks.append(" ".join(buf))

    return chunks if chunks else [narration]


def normalize_tags(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for t in raw:
        if isinstance(t, str) and t.strip():
            out.append(t.strip())
    return out[:12]


def truncate_subtitle(text: str, max_chars: int = SUBTITLE_MAX_CHARS) -> str:
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    cut = text[: max_chars - 1].rstrip()
    return cut + "…"  # ellipsis


def normalize_scenes(raw_scenes: list[Any]) -> list[dict]:
    """Validate + normalize a list of raw (possibly messy) scene dicts.

    - Drops empty/unusable scenes (no narration text).
    - Re-indexes sequentially from 0 regardless of input indexes/order.
    - Ensures all required keys are present with sane types.
    - Clamps estimated_duration to [MIN_SCENE_DURATION, MAX_SCENE_DURATION].
    - Truncates subtitle to SUBTITLE_MAX_CHARS (with ellipsis).
    - Sets flow_filename = scene_{index:03d}.png.
    - Sets image_path/audio_path/audio_duration to None (not yet produced).
    """
    normalized: list[dict] = []

    for item in raw_scenes:
        if not isinstance(item, dict):
            continue
        narration = str(item.get("narration") or "").strip()
        if not narration:
            continue

        image_prompt = str(item.get("image_prompt") or "").strip()
        if not image_prompt:
            image_prompt = (
                "A simple hand-drawn doodle illustrating: "
                + narration[:80]
                + ". White background, thick black imperfect marker lines, "
                "minimal shapes, no 3D, no realism, no cinematic lighting, "
                "no photorealism."
            )

        subtitle = str(item.get("subtitle") or narration).strip()
        subtitle = truncate_subtitle(subtitle)

        duration = item.get("estimated_duration")
        try:
            duration = float(duration)
        except (TypeError, ValueError):
            duration = 3.0
        if not math.isfinite(duration):
            duration = 3.0
        duration = min(MAX_SCENE_DURATION, max(MIN_SCENE_DURATION, duration))

        index = len(normalized)
        normalized.append(
            {
                "index": index,
                "narration": narration,
                "subtitle": subtitle,
                "estimated_duration": round(duration, 2),
                "image_prompt": image_prompt,
                "flow_filename": f"scene_{index:03d}.png",
                "image_path": None,
                "audio_path": None,
                "audio_duration": None,
            }
        )

    return normalized
