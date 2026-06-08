"""
ClipForge — Caption presets + constants (extracted from captioner.py).

Pure data + the pure hex_to_ass_color helper. captioner.py re-exports these
so `from services.captioner import DEFAULT_PRESETS, hex_to_ass_color`
keeps working for existing callers (caption_overlays, caption_templates).
"""

from __future__ import annotations


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

