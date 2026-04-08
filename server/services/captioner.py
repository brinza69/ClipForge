"""
ClipForge — Caption Service (v2)
Generates animated ASS subtitles for word-by-word highlight captions
in TikTok/Reels style, then burns them into video via FFmpeg.

v2 improvements:
  - Premium CapCut-like font choices and styling
  - Intelligent safe-zone positioning (avoids TikTok UI, speaker face)
  - Better hook text with fade animation and premium look
  - More differentiated presets
  - Anti-collision between hook text and captions
"""

import logging
import math
from pathlib import Path
from typing import List, Dict, Any, Optional

import pysubs2

from config import settings

logger = logging.getLogger("clipforge.captioner")


# ---------------------------------------------------------------------------
# Safe zone constants for 1080x1920 (9:16) output
# ---------------------------------------------------------------------------
# TikTok UI overlay zones to avoid:
#   Top: 0-160px (status bar, back button, username)
#   Bottom: 1620-1920px (like/comment/share, caption text, progress bar)
#   Right: 960-1080px (action buttons: heart, comment, share, etc.)
#
# Safe zones (marginv = distance FROM the edge that the alignment points to):
#   alignment 2 (bottom-center) → marginv pushes UP from bottom
#   alignment 5 (mid-center) → marginv offsets from center
#   alignment 8 (top-center) → marginv pushes DOWN from top
#
# TikTok description overlay occupies bottom ~300px.
# TikTok action buttons occupy right side ~120px (not affected by marginv).
# We want captions ABOVE the description area → ~480px from bottom edge.
SAFE_TOP = 200          # Below status bar
SAFE_CAPTION_BOTTOM = 480   # Caption bottom margin — well above TikTok description/UI
SAFE_CAPTION_CENTER = 120   # Center-aligned captions vertical offset (slight up-shift)
SAFE_HOOK_MID_Y = 700       # Hook text: ~37% from top of 1920 frame = mid-frame area


# ---------------------------------------------------------------------------
# Caption presets — premium CapCut/TikTok-native styling
# ---------------------------------------------------------------------------
# Font priority: use bold/heavy system fonts that look good on mobile.
# Windows: Impact, Arial Black, Segoe UI Black, Bahnschrift Bold
# Fallback fonts are listed for cross-platform (Linux/Mac).
#
# Key design principles:
#   - High contrast (thick outline or box background)
#   - Large enough to read on phone (min 58px at 1080 width)
#   - Short word groups (2-4 words max per display)
#   - Bold/heavy weight always

DEFAULT_PRESETS = {
    "bold_impact": {
        "name": "Bold Impact",
        "font_family": "Impact",
        "font_size": 72,
        "font_weight": "Bold",
        "text_color": "#FFFFFF",
        "highlight_color": "#FFD700",
        "outline_color": "#000000",
        "outline_width": 5,
        "shadow_offset": 2.5,
        "shadow_color": "#000000B0",
        "position": "bottom",
        "uppercase": True,
        "animation": "word",
        "max_words_per_line": 3,
    },
    "clean_minimal": {
        "name": "Clean Minimal",
        "font_family": "Segoe UI",
        "font_size": 62,
        "font_weight": "Bold",
        "text_color": "#FFFFFF",
        "highlight_color": "#00D4FF",
        "outline_color": "#000000",
        "outline_width": 4,
        "shadow_offset": 2,
        "shadow_color": "#00000080",
        "position": "bottom",
        "uppercase": False,
        "animation": "phrase",
        "max_words_per_line": 3,
    },
    "neon_pop": {
        "name": "Neon Pop",
        "font_family": "Arial Black",
        "font_size": 74,
        "font_weight": "Black",
        "text_color": "#FFFFFF",
        "highlight_color": "#FF3366",
        "outline_color": "#1A0033",
        "outline_width": 5,
        "shadow_offset": 3,
        "shadow_color": "#FF336650",
        "position": "center",
        "uppercase": True,
        "animation": "word",
        "max_words_per_line": 2,
    },
    "classic_white": {
        "name": "Classic White",
        "font_family": "Segoe UI",
        "font_size": 62,
        "font_weight": "Bold",
        "text_color": "#FFFFFF",
        "highlight_color": "#FFFFFF",
        "outline_color": "#000000",
        "outline_width": 4,
        "shadow_offset": 2,
        "shadow_color": "#000000A0",
        "position": "bottom",
        "uppercase": False,
        "animation": "phrase",
        "max_words_per_line": 4,
    },
    "karaoke_yellow": {
        "name": "Karaoke Yellow",
        "font_family": "Arial Black",
        "font_size": 68,
        "font_weight": "Bold",
        "text_color": "#FFFFFF",
        "highlight_color": "#FFE600",
        "highlight_bg_color": "#FFE600",
        "outline_color": "#000000",
        "outline_width": 4,
        "shadow_offset": 2,
        "shadow_color": "#000000A0",
        "position": "bottom",
        "uppercase": True,
        "animation": "word",
        "max_words_per_line": 3,
    },
    "boxed_white": {
        "name": "Boxed White",
        "font_family": "Segoe UI",
        "font_size": 64,
        "font_weight": "Bold",
        "text_color": "#FFFFFF",
        "highlight_color": "#FFFFFF",
        "highlight_bg_color": "#000000",
        "outline_color": "#000000",
        "outline_width": 14,
        "shadow_offset": 0,
        "shadow_color": "#00000000",
        "position": "bottom",
        "uppercase": False,
        "animation": "word",
        "max_words_per_line": 3,
        "borderstyle": 3,  # Opaque box
    },
    "viral_gradient": {
        "name": "Viral Gradient",
        "font_family": "Impact",
        "font_size": 76,
        "font_weight": "Bold",
        "text_color": "#FFFFFF",
        "highlight_color": "#FF6B35",
        "outline_color": "#000000",
        "outline_width": 5,
        "shadow_offset": 3,
        "shadow_color": "#FF6B3540",
        "position": "bottom",
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
    hook_text: Optional[str] = None,
    style_overrides: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Generate an ASS subtitle file with animated word-by-word captions.

    Args:
        segments: Transcript segments with word-level timestamps
        clip_start: Start time of the clip in the source video
        clip_end: End time of the clip
        preset: Caption style preset dict (or uses bold_impact default)
        output_path: Where to save the ASS file
        hook_text: Optional viral hook box text for the beginning

    Returns:
        Path to the generated ASS file
    """
    preset = dict(preset or DEFAULT_PRESETS["bold_impact"])  # copy so overrides don't mutate original
    if style_overrides:
        if style_overrides.get("caption_font_size"):
            preset["font_size"] = style_overrides["caption_font_size"]
        if style_overrides.get("caption_text_color"):
            preset["text_color"] = style_overrides["caption_text_color"]
        if style_overrides.get("caption_highlight_color"):
            preset["highlight_color"] = style_overrides["caption_highlight_color"]
        if style_overrides.get("caption_outline_color"):
            preset["outline_color"] = style_overrides["caption_outline_color"]
        if style_overrides.get("caption_y_position"):
            preset["position"] = style_overrides["caption_y_position"]
    logger.info(f"Generating captions [{clip_start:.1f}s-{clip_end:.1f}s] preset={preset.get('name', 'custom')}")

    subs = pysubs2.SSAFile()
    subs.info["PlayResX"] = str(settings.export_width)
    subs.info["PlayResY"] = str(settings.export_height)

    # --- Positioning based on preset ---
    position = preset.get("position", "bottom")
    if position == "bottom":
        caption_marginv = SAFE_CAPTION_BOTTOM
    elif position == "center":
        caption_marginv = SAFE_CAPTION_CENTER
    else:  # top
        caption_marginv = SAFE_TOP

    # --- Create main caption style ---
    normal_style = pysubs2.SSAStyle()
    normal_style.fontname = preset.get("font_family", "Impact")
    normal_style.fontsize = preset.get("font_size", 72)
    normal_style.bold = preset.get("font_weight", "Bold") in ("Bold", "Black", "ExtraBold", "SemiBold")
    normal_style.primarycolor = hex_to_ass_color(preset.get("text_color", "#FFFFFF"))
    normal_style.outlinecolor = hex_to_ass_color(preset.get("outline_color", "#000000"))
    normal_style.backcolor = hex_to_ass_color(preset.get("shadow_color", "#000000B0"))
    # Enforce minimum outline for readability
    normal_style.outline = max(preset.get("outline_width", 5), 3)
    normal_style.shadow = max(preset.get("shadow_offset", 2.5), 1.5)
    normal_style.borderstyle = preset.get("borderstyle", 1)  # 1=outline+shadow, 3=opaque box
    normal_style.alignment = _get_alignment(position)
    normal_style.marginv = caption_marginv
    # Horizontal margins to keep text from touching edges
    normal_style.marginl = 60
    normal_style.marginr = 60

    subs.styles["Default"] = normal_style

    # --- Highlighted word style ---
    highlight_style = normal_style.copy()
    highlight_style.primarycolor = hex_to_ass_color(preset.get("highlight_color", "#FFD700"))

    if preset.get("highlight_bg_color"):
        # Karaoke/boxed style: opaque background on highlighted word
        highlight_style.borderstyle = 3
        highlight_style.outlinecolor = hex_to_ass_color(preset.get("highlight_bg_color"))
        highlight_style.primarycolor = hex_to_ass_color("#000000")
        highlight_style.outline = max(preset.get("outline_width", 14), 10)
    subs.styles["Highlight"] = highlight_style

    # --- Hook text style (premium box) ---
    hook_duration_ms = 0
    hook_fade_ms = 300  # Fade in/out duration

    if hook_text:
        hook_style = pysubs2.SSAStyle()
        # Prioritize system fonts available on Windows, macOS, and Linux
        hook_style.fontname = "Arial"  # Universal fallback; Impact looks too aggressive for hook box
        hook_style.fontsize = (style_overrides or {}).get("hook_font_size") or 46
        hook_style.bold = True
        # Place hook in the upper-mid area (~35% from top) — strong visual
        # position without covering the speaker's face
        hook_style.alignment = 5  # Center (numpad)
        hook_style.marginv = 280  # Push above dead-center
        hook_style.marginl = 80
        hook_style.marginr = 80
        hook_style.borderstyle = 3   # Opaque background box
        hook_style.outline = 24      # Generous padding = "pill" shape
        hook_style.shadow = 6        # Drop shadow for depth
        hook_style.primarycolor = hex_to_ass_color(
            (style_overrides or {}).get("hook_text_color") or "#FFFFFF"
        )
        hook_style.outlinecolor = hex_to_ass_color(
            (style_overrides or {}).get("hook_bg_color") or "#0A0A0A"
        )      # Near-black background
        hook_style.backcolor = hex_to_ass_color("#000000B0")       # Shadow color
        subs.styles["Hook"] = hook_style

        clip_duration = clip_end - clip_start
        # Hook should be visible for 3-5s; on very short clips cap at 15% of duration
        hook_duration_ms = int(min(5.0, max(3.0, clip_duration * 0.15)) * 1000)

    # --- Anti-collision: when hook is mid-screen and captions are center-aligned,
    # push captions to bottom during hook display to avoid overlap ---
    need_collision_style = bool(hook_text) and position == "center"
    if need_collision_style:
        collision_style = normal_style.copy()
        collision_style.alignment = 2   # Bottom-center
        collision_style.marginv = SAFE_CAPTION_BOTTOM
        subs.styles["DefaultBottom"] = collision_style

        collision_hl = highlight_style.copy()
        collision_hl.alignment = 2
        collision_hl.marginv = SAFE_CAPTION_BOTTOM
        subs.styles["HighlightBottom"] = collision_hl

    # --- Collect words within clip range ---
    clip_words = _extract_clip_words(segments, clip_start, clip_end)

    if not clip_words:
        logger.warning("No words found for caption generation")
        # Still generate hook if present
        if hook_text and output_path:
            _add_hook_event(subs, hook_text, hook_duration_ms, hook_fade_ms)
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            subs.save(output_path, encoding="utf-8")
            return output_path
        return ""

    # Apply uppercase
    if preset.get("uppercase", True):
        for w in clip_words:
            w["word"] = w["word"].upper()

    # Group words and generate animation events
    max_words = preset.get("max_words_per_line", 3)
    animation = preset.get("animation", "word")

    if animation == "word":
        _generate_word_animation(subs, clip_words, max_words)
    elif animation == "phrase":
        _generate_phrase_animation(subs, clip_words, max_words + 1)
    else:
        _generate_line_animation(subs, clip_words, max_words + 2)

    # Anti-collision: push overlapping captions to bottom during hook
    if need_collision_style and hook_duration_ms > 0:
        for event in subs.events:
            if event.start < hook_duration_ms:
                if event.style == "Default":
                    event.style = "DefaultBottom"
                elif event.style == "Highlight":
                    event.style = "HighlightBottom"

    # Insert hook event
    if hook_text:
        _add_hook_event(subs, hook_text, hook_duration_ms, hook_fade_ms)

    # Save
    if not output_path:
        output_path = str(settings.temp_dir / "captions.ass")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    subs.save(output_path, encoding="utf-8")
    logger.info(f"Captions saved: {output_path} ({len(subs.events)} events)")
    return output_path


# ---------------------------------------------------------------------------
# Word extraction
# ---------------------------------------------------------------------------

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
):
    """Add the hook text event with fade + scale-up animation for premium feel."""
    # Wrap with fad (fade) + fscx/fscy scale animation:
    # Start at 90% scale, grow to 100% over first 400ms for a subtle pop-in
    scale_in = 400  # ms for scale animation
    # ASS animation: \t(0,400,\fscx100\fscy100) starting from \fscx92\fscy92
    anim = (
        f"{{\\fad({fade_ms},{fade_ms})"
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
