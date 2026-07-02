"""
ClipForge — Auto Story Doodle: subtitle generation.

Builds a plain SRT file from per-scene REAL audio durations (cumulative
timeline) and returns ffmpeg `subtitles=...:force_style=...` style strings
for the three supported presets.

No external deps — SRT is trivial text, and force_style is just an ASS-style
override string consumed by ffmpeg's `subtitles` filter.
"""

from __future__ import annotations

from typing import Dict, List, Tuple


def _srt_timestamp(seconds: float) -> str:
    """Format seconds as SRT timestamp: HH:MM:SS,mmm"""
    if seconds < 0:
        seconds = 0.0
    total_ms = round(seconds * 1000)
    hours, rem_ms = divmod(total_ms, 3_600_000)
    minutes, rem_ms = divmod(rem_ms, 60_000)
    secs, millis = divmod(rem_ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def build_srt(scenes: List[Dict]) -> str:
    """
    Build an SRT string using cumulative REAL audio_duration timings.

    Each scene's subtitle (falls back to narration if subtitle is missing)
    is shown for the scene's audio_duration, back to back starting at 0.
    Scenes without an audio_duration are skipped for timing purposes but
    still consume no time (treated as zero-length — in practice all scenes
    should have audio_duration by the time this is called for rendering).
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

        text = (scene.get("subtitle") or scene.get("narration") or "").strip()
        start = cursor
        end = cursor + duration
        cursor = end

        if not text:
            continue

        lines.append(str(idx))
        lines.append(f"{_srt_timestamp(start)} --> {_srt_timestamp(end)}")
        lines.append(text)
        lines.append("")
        idx += 1

    return "\n".join(lines) + ("\n" if lines else "")


def subtitle_style_args(style: str, resolution: Tuple[int, int]) -> str:
    """
    Return a force_style=... string (WITHOUT the leading "force_style=") body
    suitable for ffmpeg's `subtitles` filter, per named style.

    resolution: (width, height) of the final output — used to scale font
    size sensibly across 16:9 / 9:16 / 1:1 outputs.
    """
    width, height = resolution
    # Use the smaller dimension as the scale reference so portrait (9:16)
    # and square (1:1) outputs don't get comically huge or tiny text.
    ref = min(width, height)

    if style == "tiktok_bold":
        font_size = max(28, round(ref * 0.062))
        margin_v = round(height * 0.16)
        return (
            "FontName=Arial Black,"
            f"Fontsize={font_size},"
            "PrimaryColour=&H00FFFFFF,"
            "OutlineColour=&H00000000,"
            "BackColour=&H00000000,"
            "Bold=1,"
            "BorderStyle=1,"
            "Outline=4,"
            "Shadow=2,"
            "Alignment=2,"
            f"MarginV={margin_v},"
            "MarginL=40,MarginR=40,"
            "Spacing=0.5"
        )

    if style == "minimal":
        font_size = max(18, round(ref * 0.032))
        margin_v = round(height * 0.06)
        return (
            "FontName=Arial,"
            f"Fontsize={font_size},"
            "PrimaryColour=&H00FFFFFF,"
            "OutlineColour=&H00000000,"
            "BackColour=&H00000000,"
            "Bold=0,"
            "BorderStyle=1,"
            "Outline=1.2,"
            "Shadow=0.6,"
            "Alignment=2,"
            f"MarginV={margin_v},"
            "MarginL=60,MarginR=60"
        )

    # "youtube_clean" (default): white text on a semi-transparent black box.
    font_size = max(22, round(ref * 0.042))
    margin_v = round(height * 0.08)
    return (
        "FontName=Arial,"
        f"Fontsize={font_size},"
        "PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,"
        # BackColour alpha byte 0x40 => mostly-opaque translucent box
        # (ASS &HAABBGGRR: AA=40 hex ~= 75% opaque, BGR=000000 black).
        "BackColour=&H40000000,"
        "Bold=1,"
        "BorderStyle=3,"
        "Outline=1,"
        "Shadow=0,"
        "Alignment=2,"
        f"MarginV={margin_v},"
        "MarginL=80,MarginR=80"
    )
