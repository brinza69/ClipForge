"""
ClipForge — Caption Service
Generates animated ASS subtitles for word-by-word highlight captions
in TikTok/Reels style, then burns them into video via FFmpeg.
"""

import logging
import math
from pathlib import Path
from typing import List, Dict, Any, Optional

import pysubs2

from config import settings

logger = logging.getLogger("clipforge.captioner")


# Default caption presets
DEFAULT_PRESETS = {
    "bold_impact": {
        "name": "Bold Impact",
        "font_family": "Montserrat",
        "font_size": 68,
        "font_weight": "Bold",
        "text_color": "#FFFFFF",
        "highlight_color": "#FFD700",
        "outline_color": "#000000",
        "outline_width": 4,
        "shadow_color": "#00000080",
        "position": "bottom",
        "uppercase": True,
        "animation": "word",
        "max_words_per_line": 3,
    },
    "clean_minimal": {
        "name": "Clean Minimal",
        "font_family": "Inter",
        "font_size": 58,
        "font_weight": "SemiBold",
        "text_color": "#FFFFFF",
        "highlight_color": "#00D4FF",
        "outline_color": "#000000",
        "outline_width": 3,
        "shadow_color": "#00000060",
        "position": "bottom",
        "uppercase": False,
        "animation": "phrase",
        "max_words_per_line": 4,
    },
    "neon_pop": {
        "name": "Neon Pop",
        "font_family": "Outfit",
        "font_size": 72,
        "font_weight": "Black",
        "text_color": "#FFFFFF",
        "highlight_color": "#FF3366",
        "outline_color": "#1A0033",
        "outline_width": 5,
        "shadow_color": "#FF336640",
        "position": "center",
        "uppercase": True,
        "animation": "word",
        "max_words_per_line": 2,
    },
}


def hex_to_ass_color(hex_color: str) -> str:
    """Convert hex color (#RRGGBB or #RRGGBBAA) to ASS color (&HAABBGGRR)."""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 8:
        r, g, b, a = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16), int(hex_color[6:8], 16)
    elif len(hex_color) == 6:
        r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
        a = 0
    else:
        return "&H00FFFFFF"
    return f"&H{a:02X}{b:02X}{g:02X}{r:02X}"


def generate_captions(
    segments: List[Dict],
    clip_start: float,
    clip_end: float,
    preset: Optional[Dict] = None,
    output_path: Optional[str] = None,
) -> str:
    """
    Generate an ASS subtitle file with animated word-by-word captions.

    Args:
        segments: Transcript segments with word-level timestamps
        clip_start: Start time of the clip in the source video
        clip_end: End time of the clip
        preset: Caption style preset dict
        output_path: Where to save the ASS file

    Returns:
        Path to the generated ASS file
    """
    preset = preset or DEFAULT_PRESETS["bold_impact"]
    logger.info(f"Generating captions [{clip_start:.1f}s-{clip_end:.1f}s] preset={preset.get('name', 'custom')}")

    # Create ASS file
    subs = pysubs2.SSAFile()
    subs.info["PlayResX"] = str(settings.export_width)
    subs.info["PlayResY"] = str(settings.export_height)

    # Create styles
    normal_style = pysubs2.SSAStyle()
    normal_style.fontname = preset.get("font_family", "Montserrat")
    normal_style.fontsize = preset.get("font_size", 68)
    normal_style.bold = preset.get("font_weight", "Bold") in ("Bold", "Black", "ExtraBold")
    normal_style.primarycolor = hex_to_ass_color(preset.get("text_color", "#FFFFFF"))
    normal_style.outlinecolor = hex_to_ass_color(preset.get("outline_color", "#000000"))
    normal_style.backcolor = hex_to_ass_color(preset.get("shadow_color", "#00000080"))
    normal_style.outline = preset.get("outline_width", 4)
    normal_style.shadow = 2
    normal_style.alignment = _get_alignment(preset.get("position", "bottom"))
    normal_style.marginv = 120 if preset.get("position") == "bottom" else 50

    subs.styles["Default"] = normal_style

    # Highlighted word style
    highlight_style = normal_style.copy()
    highlight_style.primarycolor = hex_to_ass_color(preset.get("highlight_color", "#FFD700"))
    subs.styles["Highlight"] = highlight_style

    # Collect all words within clip range
    clip_words = []
    for seg in segments:
        if seg["end"] <= clip_start or seg["start"] >= clip_end:
            continue

        words = seg.get("words", [])
        if words:
            for w in words:
                if w["start"] >= clip_start and w["end"] <= clip_end:
                    clip_words.append({
                        "word": w["word"],
                        "start": w["start"] - clip_start,
                        "end": w["end"] - clip_start,
                    })
        else:
            # No word-level timestamps — split segment text
            seg_start = max(seg["start"], clip_start) - clip_start
            seg_end = min(seg["end"], clip_end) - clip_start
            words_in_seg = seg["text"].split()
            if words_in_seg:
                word_dur = (seg_end - seg_start) / len(words_in_seg)
                for i, word in enumerate(words_in_seg):
                    clip_words.append({
                        "word": word,
                        "start": seg_start + i * word_dur,
                        "end": seg_start + (i + 1) * word_dur,
                    })

    if not clip_words:
        logger.warning("No words found for caption generation")
        return ""

    # Apply uppercase if preset says so
    if preset.get("uppercase", True):
        for w in clip_words:
            w["word"] = w["word"].upper()

    # Group words into lines
    max_words = preset.get("max_words_per_line", 3)
    animation = preset.get("animation", "word")

    if animation == "word":
        _generate_word_animation(subs, clip_words, max_words)
    elif animation == "phrase":
        _generate_phrase_animation(subs, clip_words, max_words + 1)
    else:
        _generate_line_animation(subs, clip_words, max_words + 2)

    # Save
    if not output_path:
        output_path = str(settings.temp_dir / "captions.ass")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    subs.save(output_path)
    logger.info(f"Captions saved: {output_path} ({len(subs.events)} events)")
    return output_path


def _generate_word_animation(
    subs: pysubs2.SSAFile,
    words: List[Dict],
    max_per_line: int,
):
    """Word-by-word highlight: show group, highlight current word."""
    groups = _group_words(words, max_per_line)

    for group in groups:
        group_start = group[0]["start"]
        group_end = group[-1]["end"]

        for i, word_info in enumerate(group):
            # Build text with current word highlighted
            parts = []
            for j, w in enumerate(group):
                if j == i:
                    parts.append(f'{{\\rHighlight}}{w["word"]}{{\\rDefault}}')
                else:
                    parts.append(w["word"])

            text = " ".join(parts)

            event = pysubs2.SSAEvent(
                start=int(word_info["start"] * 1000),
                end=int(word_info["end"] * 1000),
                text=text,
                style="Default",
            )
            subs.events.append(event)


def _generate_phrase_animation(
    subs: pysubs2.SSAFile,
    words: List[Dict],
    max_per_line: int,
):
    """Phrase-level animation: show entire phrase, highlight as group."""
    groups = _group_words(words, max_per_line)

    for group in groups:
        text = " ".join(w["word"] for w in group)
        event = pysubs2.SSAEvent(
            start=int(group[0]["start"] * 1000),
            end=int(group[-1]["end"] * 1000),
            text=text,
            style="Highlight",
        )
        subs.events.append(event)


def _generate_line_animation(
    subs: pysubs2.SSAFile,
    words: List[Dict],
    max_per_line: int,
):
    """Line-level animation: show and fade lines."""
    groups = _group_words(words, max_per_line)

    for group in groups:
        text = " ".join(w["word"] for w in group)
        event = pysubs2.SSAEvent(
            start=int(group[0]["start"] * 1000),
            end=int(group[-1]["end"] * 1000),
            text=f"{{\\fad(100,100)}}{text}",
            style="Default",
        )
        subs.events.append(event)


def _group_words(words: List[Dict], max_per_group: int) -> List[List[Dict]]:
    """Group words into display groups."""
    groups = []
    current_group = []

    for word in words:
        current_group.append(word)

        if len(current_group) >= max_per_group:
            groups.append(current_group)
            current_group = []

    if current_group:
        groups.append(current_group)

    return groups


def _get_alignment(position: str) -> int:
    """Convert position name to ASS alignment number."""
    # ASS alignment: 1-3 bottom, 4-6 middle, 7-9 top (numpad layout)
    return {"bottom": 2, "center": 5, "top": 8}.get(position, 2)
