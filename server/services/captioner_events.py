"""
ClipForge — Caption ASS event builders (extracted from captioner.py).

The animation + event-construction helpers used by generate_captions.
Self-contained: depends only on pysubs2 + hex_to_ass_color. Extracted to
keep captioner.py under the 500-line limit.
"""

from __future__ import annotations

import math
from typing import List, Dict, Any, Optional

import pysubs2

from services.captioner_presets import hex_to_ass_color


def _extract_clip_words(
    segments: List[Dict],
    clip_start: float,
    clip_end: float,
) -> List[Dict]:
    """Extract and normalize words from transcript segments within clip range."""
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
            # No word-level timestamps — distribute evenly
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

    return clip_words


# ---------------------------------------------------------------------------
# Animation generators
# ---------------------------------------------------------------------------

def _generate_word_animation(
    subs: pysubs2.SSAFile,
    words: List[Dict],
    max_per_line: int,
):
    """Word-by-word highlight with subtle scale emphasis on current word."""
    groups = _group_words(words, max_per_line)

    for group in groups:
        for i, word_info in enumerate(group):
            parts = []
            for j, w in enumerate(group):
                if j == i:
                    # Scale up highlighted word slightly (105%) for emphasis pop
                    parts.append(
                        f'{{\\rHighlight\\fscx105\\fscy105}}{w["word"]}{{\\rDefault}}'
                    )
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
    """Phrase-level: show entire phrase highlighted as a group."""
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
    """Line-level: show and fade lines."""
    groups = _group_words(words, max_per_line)

    for group in groups:
        text = " ".join(w["word"] for w in group)
        event = pysubs2.SSAEvent(
            start=int(group[0]["start"] * 1000),
            end=int(group[-1]["end"] * 1000),
            text=f"{{\\fad(120,120)}}{text}",
            style="Default",
        )
        subs.events.append(event)


# ---------------------------------------------------------------------------
# Hook text
# ---------------------------------------------------------------------------

def _add_hook_event(
    subs: pysubs2.SSAFile,
    hook_text: str,
    duration_ms: int,
    fade_ms: int = 300,
    hook_x: int = None,
    hook_y: int = None,
    hook_font_size: int = None,
    hook_box_size: int = None,
    hook_box_width: int = None,
):
    """Add the hook text event with fade + scale-up animation for premium feel.

    Hook text wraps inside the box via ASS \\q1 (word-wrap) mode.
    If hook_x/hook_y are provided (0-100 percentage), position with \\pos.
    hook_box_width adds extra horizontal padding via \\fsp (letter spacing).
    """
    scale_in = 400
    play_res_x = int(subs.info.get("PlayResX", 1080))
    play_res_y = int(subs.info.get("PlayResY", 1920))

    # Build position override tag if x/y provided
    pos_tag = ""
    if hook_x is not None and hook_y is not None:
        px = int(hook_x / 100 * play_res_x)
        py = int(hook_y / 100 * play_res_y)
        pos_tag = f"\\pos({px},{py})"

    # Extra horizontal padding via letter spacing when box_width > box_size
    fsp_tag = ""
    effective_box_size = hook_box_size or 24
    effective_box_width = hook_box_width or effective_box_size
    if effective_box_width > effective_box_size:
        # Scale the extra width into letter spacing (approximate visual match)
        extra_px = effective_box_width - effective_box_size
        fsp_tag = f"\\fsp{extra_px}"

    # Enable word wrapping (\\q1 = end-of-line wrapping, respects \\ClipRect / margins)
    anim = (
        f"{{\\q1{pos_tag}{fsp_tag}"
        f"\\fad({fade_ms},{fade_ms})"
        f"\\fscx92\\fscy92"
        f"\\t(0,{scale_in},\\fscx100\\fscy100)"
        f"}}{hook_text}"
    )
    hook_event = pysubs2.SSAEvent(
        start=0,
        end=duration_ms,
        text=anim,
        style="Hook",
    )
    subs.events.insert(0, hook_event)


def _add_title_event(
    subs: pysubs2.SSAFile,
    title_text: str,
    duration_ms: int,
    title_x: int = None,
    title_y: int = None,
    title_font_size: int = None,
    title_box_size: int = None,
    title_box_width: int = None,
    title_bg_enabled: bool = True,
):
    """Add a persistent title overlay (full duration) styled like the hook box.

    Differs from the hook box in that it stays visible for the entire clip
    duration and has no fade animation. Position via \\pos when x/y given.
    """
    play_res_x = int(subs.info.get("PlayResX", 1080))
    play_res_y = int(subs.info.get("PlayResY", 1920))

    title_style = pysubs2.SSAStyle()
    title_style.fontname = "Arial"
    title_style.fontsize = title_font_size or 46
    title_style.bold = True
    box_size = title_box_size or 24
    if title_bg_enabled:
        title_style.borderstyle = 3  # Opaque background box
        title_style.outline = box_size
        title_style.shadow = max(6, box_size // 4)
        title_style.primarycolor = hex_to_ass_color("#FFFFFF")
        title_style.outlinecolor = hex_to_ass_color("#0A0A0A")
        title_style.backcolor = hex_to_ass_color("#000000B0")
    else:
        title_style.borderstyle = 1  # Outline only, no box
        title_style.outline = max(3, box_size // 6)
        title_style.shadow = 2
        title_style.primarycolor = hex_to_ass_color("#FFFFFF")
        title_style.outlinecolor = hex_to_ass_color("#000000")
        title_style.backcolor = hex_to_ass_color("#00000080")
    title_style.alignment = 5  # center anchor (works for \pos)
    title_style.marginv = 0
    title_style.marginl = 0
    title_style.marginr = 0
    subs.styles["Title"] = title_style

    # Position
    pos_tag = ""
    if title_x is not None and title_y is not None:
        px = int(title_x / 100 * play_res_x)
        py = int(title_y / 100 * play_res_y)
        pos_tag = f"\\pos({px},{py})"
    else:
        # Default near top-center
        px = play_res_x // 2
        py = int(play_res_y * 0.18)
        pos_tag = f"\\pos({px},{py})"

    # Letter spacing for extra horizontal padding
    fsp_tag = ""
    box_width = title_box_width or box_size
    if box_width > box_size:
        fsp_tag = f"\\fsp{box_width - box_size}"

    text = f"{{\\q1{pos_tag}{fsp_tag}}}{title_text}"
    event = pysubs2.SSAEvent(start=0, end=duration_ms, text=text, style="Title")
    subs.events.insert(0, event)


def _add_creator_tag_event(
    subs: pysubs2.SSAFile,
    tag_text: str,
    duration_ms: int,
    tag_x: int = None,
    tag_y: int = None,
    tag_font_size: int = None,
    tag_opacity: float = None,
):
    """Add a persistent translucent creator-tag overlay (watermark) for the full duration.

    Plain text — no background box — with per-character alpha driven by
    tag_opacity (0.0 fully transparent … 1.0 fully opaque). Positioned via
    percentage coords (tag_x, tag_y); defaults to bottom-center.
    """
    play_res_x = int(subs.info.get("PlayResX", 1080))
    play_res_y = int(subs.info.get("PlayResY", 1920))

    font_size = int(tag_font_size) if tag_font_size else 32
    opacity = tag_opacity if tag_opacity is not None else 0.7
    opacity = max(0.0, min(1.0, float(opacity)))
    # ASS alpha byte: 00=opaque, FF=transparent — inverted from the familiar
    # 0.0-1.0 opacity scale, so we map (1 - opacity) * 255.
    alpha_byte = int((1.0 - opacity) * 255)
    alpha_hex = f"{alpha_byte:02X}"

    tag_style = pysubs2.SSAStyle()
    tag_style.fontname = "Arial"
    tag_style.fontsize = font_size
    tag_style.bold = True
    tag_style.borderstyle = 1  # Outline (no background box)
    tag_style.outline = max(1, font_size // 16)
    tag_style.shadow = max(1, font_size // 20)
    tag_style.primarycolor = f"&H{alpha_hex}FFFFFF"  # white w/ alpha
    tag_style.outlinecolor = f"&H{alpha_hex}000000"  # black outline w/ alpha
    tag_style.backcolor = "&HFF000000"
    tag_style.alignment = 5  # center anchor (for \pos)
    tag_style.marginv = 0
    tag_style.marginl = 0
    tag_style.marginr = 0
    subs.styles["CreatorTag"] = tag_style

    # Default position: bottom-center at ~92% from top
    px = int((tag_x if tag_x is not None else 50) / 100 * play_res_x)
    py = int((tag_y if tag_y is not None else 92) / 100 * play_res_y)
    pos_tag = f"\\pos({px},{py})"

    text = f"{{{pos_tag}}}{tag_text}"
    event = pysubs2.SSAEvent(start=0, end=duration_ms, text=text, style="CreatorTag")
    subs.events.append(event)


def _add_part_label_event(
    subs: pysubs2.SSAFile,
    part_num: int,
    total_parts: int,
    duration_ms: int,
    style_overrides: dict = None,
):
    """Add a 'Part X/Y' label overlay that shows for the entire part duration."""
    overrides = style_overrides or {}

    label_style = pysubs2.SSAStyle()
    label_style.fontname = "Arial"
    label_style.fontsize = overrides.get("part_label_font_size") or 32
    label_style.bold = True
    label_style.borderstyle = 3  # Opaque box
    label_box_size = overrides.get("part_label_box_size") or 14
    label_style.outline = label_box_size
    label_style.shadow = max(3, label_box_size // 4)
    label_style.primarycolor = hex_to_ass_color(
        overrides.get("part_label_text_color") or "#FFFFFF"
    )
    label_style.outlinecolor = hex_to_ass_color(
        overrides.get("part_label_bg_color") or "#000000CC"
    )
    label_style.backcolor = hex_to_ass_color("#00000080")

    play_res_x = int(subs.info.get("PlayResX", 1080))
    play_res_y = int(subs.info.get("PlayResY", 1920))

    # Position: use part_label_x/part_label_y (0-100) or default top-right
    label_x = overrides.get("part_label_x")
    label_y = overrides.get("part_label_y")
    if label_x is not None and label_y is not None:
        label_style.alignment = 5  # center anchor
        label_style.marginv = 0
        label_style.marginl = 0
        label_style.marginr = 0
        px = int(label_x / 100 * play_res_x)
        py = int(label_y / 100 * play_res_y)
        pos_tag = f"\\pos({px},{py})"
    else:
        # Default: top-right area
        label_style.alignment = 9  # top-right
        label_style.marginv = 180
        label_style.marginr = 40
        label_style.marginl = 40
        pos_tag = ""

    subs.styles["PartLabel"] = label_style

    label_text = f"Part {part_num}/{total_parts}"
    event = pysubs2.SSAEvent(
        start=0,
        end=duration_ms,
        text=f"{{{pos_tag}}}{label_text}" if pos_tag else label_text,
        style="PartLabel",
    )
    subs.events.append(event)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _group_words(words: List[Dict], max_per_group: int) -> List[List[Dict]]:
    """
    Group words into display groups with punctuation-aware and pause-aware breaks.

    Rules:
      - Break after sentence-ending punctuation (. ! ?)
      - Break after commas/semicolons if group has >= 2 words already
      - Break on natural speech pauses (>0.5s gap between words)
      - Never exceed max_per_group
      - Avoid orphan words (1-word groups) by merging with previous group
    """
    groups: List[List[Dict]] = []
    current_group: List[Dict] = []

    for idx, word in enumerate(words):
        current_group.append(word)
        w = word["word"].rstrip()

        # Check for sentence-ending punctuation
        is_sentence_end = bool(w and w[-1] in ".!?")
        # Check for clause break (comma, semicolon, colon)
        is_clause_break = bool(w and w[-1] in ",;:" and len(current_group) >= 2)
        # Check for natural speech pause (gap > 0.5s to next word)
        is_pause = False
        if idx + 1 < len(words) and len(current_group) >= 2:
            gap = words[idx + 1]["start"] - word["end"]
            is_pause = gap > 0.5

        if len(current_group) >= max_per_group or is_sentence_end or is_clause_break or is_pause:
            groups.append(current_group)
            current_group = []

    if current_group:
        # Avoid orphan: if last group is just 1 word, merge with previous group
        # unless the previous group is already at max length
        if len(current_group) == 1 and groups and len(groups[-1]) < max_per_group:
            groups[-1].extend(current_group)
        else:
            groups.append(current_group)

    return groups


def _get_alignment(position: str) -> int:
    """Convert position name to ASS alignment number (numpad layout)."""
    return {"bottom": 2, "center": 5, "top": 8}.get(position, 2)
