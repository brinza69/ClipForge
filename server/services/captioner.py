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


# Presets, safe-zone constants and the color helper live in captioner_presets
# (extracted to keep this file under the 500-line limit). Re-exported so
# existing `from services.captioner import DEFAULT_PRESETS / hex_to_ass_color`
# imports keep working.
from services.captioner_presets import (  # noqa: E402,F401
    SAFE_TOP, SAFE_CAPTION_BOTTOM, SAFE_CAPTION_CENTER, SAFE_HOOK_MID_Y,
    DEFAULT_PRESETS, hex_to_ass_color,
)


def generate_captions(
    segments: List[Dict],
    clip_start: float,
    clip_end: float,
    preset: Optional[Dict] = None,
    output_path: Optional[str] = None,
    hook_text: Optional[str] = None,
    style_overrides: Optional[Dict[str, Any]] = None,
    hook_bg_enabled: bool = True,
    title_text: Optional[str] = None,
    creator_tag_text: Optional[str] = None,
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

    # User-provided subtitle_x/subtitle_y (from the editor sliders) ALWAYS win over
    # the preset's `position` field. This is what the preview shows, so exporting
    # anything else creates a mismatch between editor and final MP4.
    sub_x_override = (style_overrides or {}).get("subtitle_x")
    sub_y_override = (style_overrides or {}).get("subtitle_y")
    if sub_y_override is not None:
        preset["position"] = "bottom"  # force bottom alignment; we position via marginv
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

    # Apply subtitle position overrides.
    # subtitle_y (0-100) maps to "percent from top of frame where the BOTTOM of the
    # caption text sits" — matches the preview which uses `translate(-50%, -100%)`.
    # With alignment=2 (bottom-center), ASS marginv = distance from frame bottom
    # to caption bottom → marginv = (100 - subtitle_y) / 100 * export_height.
    play_res_y = int(subs.info.get("PlayResY", settings.export_height))
    if sub_y_override is not None:
        # Force bottom alignment so the mapping is consistent (we overrode
        # preset["position"] above, but belt-and-suspenders).
        normal_style.alignment = 2
        normal_style.marginv = max(20, int((100 - sub_y_override) / 100 * play_res_y))

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
        hook_style.fontname = "Arial"
        hook_style.fontsize = (style_overrides or {}).get("hook_font_size") or 46
        hook_style.bold = True

        # Hook position: use hook_x/hook_y overrides or defaults
        hook_x_pos = (style_overrides or {}).get("hook_x")  # 0-100 percentage
        hook_y_pos = (style_overrides or {}).get("hook_y")  # 0-100 percentage
        if hook_x_pos is not None and hook_y_pos is not None:
            # Use \pos override in the event text instead of style alignment
            hook_style.alignment = 5  # Center anchor for \pos
            hook_style.marginv = 0
            hook_style.marginl = 0
            hook_style.marginr = 0
        else:
            hook_style.alignment = 5  # Center (numpad)
            hook_style.marginv = 280  # Push above dead-center
            hook_style.marginl = 80
            hook_style.marginr = 80

        hook_box_size = (style_overrides or {}).get("hook_box_size") or 24
        hook_style.primarycolor = hex_to_ass_color(
            (style_overrides or {}).get("hook_text_color") or "#FFFFFF"
        )
        if hook_bg_enabled:
            hook_style.borderstyle = 3   # Opaque background box
            hook_style.outline = hook_box_size
            hook_style.shadow = max(6, hook_box_size // 4)
            hook_style.outlinecolor = hex_to_ass_color(
                (style_overrides or {}).get("hook_bg_color") or "#0A0A0A"
            )
            hook_style.backcolor = hex_to_ass_color("#000000B0")
        else:
            hook_style.borderstyle = 1   # Outline only, no box
            hook_style.outline = 3
            hook_style.shadow = 1
            hook_style.outlinecolor = hex_to_ass_color("#000000")
            hook_style.backcolor = hex_to_ass_color("#00000000")
        subs.styles["Hook"] = hook_style

        clip_duration = clip_end - clip_start
        # Use explicit duration from overrides, or auto-calculate
        hook_dur_override = (style_overrides or {}).get("hook_duration_seconds")
        if hook_dur_override and hook_dur_override > 0:
            hook_duration_ms = int(hook_dur_override * 1000)
        else:
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
        # Still generate hook / title / creator tag if present
        if (hook_text or title_text or creator_tag_text) and output_path:
            if hook_text:
                _add_hook_event(
                    subs, hook_text, hook_duration_ms, hook_fade_ms,
                    hook_x=(style_overrides or {}).get("hook_x"),
                    hook_y=(style_overrides or {}).get("hook_y"),
                    hook_font_size=(style_overrides or {}).get("hook_font_size"),
                    hook_box_size=(style_overrides or {}).get("hook_box_size"),
                    hook_box_width=(style_overrides or {}).get("hook_box_width"),
                )
            if title_text:
                clip_duration_ms = int((clip_end - clip_start) * 1000)
                _add_title_event(
                    subs, title_text, clip_duration_ms,
                    title_x=(style_overrides or {}).get("title_x"),
                    title_y=(style_overrides or {}).get("title_y"),
                    title_font_size=(style_overrides or {}).get("title_font_size"),
                    title_box_size=(style_overrides or {}).get("title_box_size"),
                    title_box_width=(style_overrides or {}).get("title_box_width"),
                    title_bg_enabled=(style_overrides or {}).get("title_bg_enabled", True),
                )
            if creator_tag_text:
                clip_duration_ms = int((clip_end - clip_start) * 1000)
                _add_creator_tag_event(
                    subs, creator_tag_text, clip_duration_ms,
                    tag_x=(style_overrides or {}).get("creator_tag_x"),
                    tag_y=(style_overrides or {}).get("creator_tag_y"),
                    tag_font_size=(style_overrides or {}).get("creator_tag_font_size"),
                    tag_opacity=(style_overrides or {}).get("creator_tag_opacity"),
                )
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
        _add_hook_event(
            subs, hook_text, hook_duration_ms, hook_fade_ms,
            hook_x=(style_overrides or {}).get("hook_x"),
            hook_y=(style_overrides or {}).get("hook_y"),
            hook_font_size=(style_overrides or {}).get("hook_font_size"),
            hook_box_size=(style_overrides or {}).get("hook_box_size"),
            hook_box_width=(style_overrides or {}).get("hook_box_width"),
        )

    # Insert persistent title event (full duration)
    if title_text:
        clip_duration_ms = int((clip_end - clip_start) * 1000)
        _add_title_event(
            subs, title_text, clip_duration_ms,
            title_x=(style_overrides or {}).get("title_x"),
            title_y=(style_overrides or {}).get("title_y"),
            title_font_size=(style_overrides or {}).get("title_font_size"),
            title_box_size=(style_overrides or {}).get("title_box_size"),
            title_box_width=(style_overrides or {}).get("title_box_width"),
            title_bg_enabled=(style_overrides or {}).get("title_bg_enabled", True),
        )

    # Insert persistent creator tag (full duration, translucent, no box)
    if creator_tag_text:
        clip_duration_ms = int((clip_end - clip_start) * 1000)
        _add_creator_tag_event(
            subs, creator_tag_text, clip_duration_ms,
            tag_x=(style_overrides or {}).get("creator_tag_x"),
            tag_y=(style_overrides or {}).get("creator_tag_y"),
            tag_font_size=(style_overrides or {}).get("creator_tag_font_size"),
            tag_opacity=(style_overrides or {}).get("creator_tag_opacity"),
        )

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

# Caption event builders live in captioner_events (extracted for the
# 500-line limit). Import the ones generate_captions calls.
from services.captioner_events import (  # noqa: E402
    _extract_clip_words,
    _generate_word_animation,
    _generate_phrase_animation,
    _generate_line_animation,
    _add_hook_event,
    _add_title_event,
    _add_creator_tag_event,
    _get_alignment,
)
