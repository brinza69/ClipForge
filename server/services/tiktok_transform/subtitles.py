"""
ClipForge — Video Transformare TikTok: subtitle cue building (step 6, spec §11).

build_subtitles() splits the Romanian script into short display cues, times
them across the real voice-over duration, and writes both:
  - a plain SRT (always — even when subtitle_mode == "none", so the text is
    still available for review/export even if nothing gets burned), and
  - a libass ASS file via services.caption_overlays.build_overlays_ass()
    (skipped when subtitle_mode == "none" — the caller must not burn
    anything, signalled by "ass": None in the return value).

Reuses rather than reimplements:
  - services.doodle.subtitles.split_phrases() for the sentence/phrase-level
    text splitter (punctuation-boundary aware, already handles windowing
    long fragments and merging tiny ones).
  - services.caption_overlays.build_overlays_ass() for the actual ASS/libass
    styling (template_id + scale), so captions look like every other
    caption in the app instead of a bespoke one-off renderer.
  - services.captioner_presets.SAFE_CAPTION_BOTTOM, the existing 9:16
    safe-zone constant, to keep captions low but clear of TikTok's bottom
    description overlay and (being x-centered) its right-hand action column.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

from services.captioner_presets import SAFE_CAPTION_BOTTOM
from services.caption_overlays import build_overlays_ass
from services.doodle.subtitles import split_phrases

logger = logging.getLogger("clipforge.tiktok.subtitles")

# SAFE_CAPTION_BOTTOM etc. (services/captioner_presets.py) were calibrated
# against a canonical 1080x1920 frame; turn the pixel margin into a fraction
# so it scales correctly if export_width/export_height ever differ.
_CANONICAL_H = 1920.0

# Default short-phrase cap when the caller hasn't set a tighter
# caption_words_per_chunk (spec: "sentence or phrase level, max 2 lines").
_DEFAULT_MAX_WORDS = 8

# build_overlays_ass renders with \q2 (no runtime auto-wrap — see its own
# docstring), so anything longer than this gets an explicit \n line break at
# the word midpoint. Below this, a cue reads fine on one line.
_WRAP_AT_WORDS = 5

# Trailing punctuation stripped when settings.caption_strip_punct is set.
# Deliberately narrow (not "!?…") — those carry cadence/emphasis that matters
# for a narrated hook/CTA script.
_STRIP_TRAILING = ".,;: "


def _wrap_two_lines(text: str) -> str:
    """Break `text` into at most 2 lines at the nearest-to-middle word boundary."""
    words = text.split()
    if len(words) <= _WRAP_AT_WORDS:
        return text
    mid = len(words) // 2
    return " ".join(words[:mid]) + "\n" + " ".join(words[mid:])


def _srt_timestamp(seconds: float) -> str:
    """Seconds -> SRT timestamp HH:MM:SS,mmm."""
    seconds = max(0.0, seconds)
    total_ms = round(seconds * 1000)
    hours, rem_ms = divmod(total_ms, 3_600_000)
    minutes, rem_ms = divmod(rem_ms, 60_000)
    secs, millis = divmod(rem_ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _build_cues(text: str, voice_duration: float, settings: Dict) -> List[Dict]:
    """Phrase-split `text` (reusing doodle's split_phrases) and distribute the
    resulting cues across voice_duration proportionally to character count.
    """
    max_words = _DEFAULT_MAX_WORDS
    try:
        wpc = int(settings.get("caption_words_per_chunk") or 0)
    except (TypeError, ValueError):
        wpc = 0
    if wpc > 1:
        max_words = wpc

    raw_cues = split_phrases(text, max_words=max_words)
    if not raw_cues:
        return []

    if settings.get("caption_strip_punct"):
        raw_cues = [c.rstrip(_STRIP_TRAILING) or c for c in raw_cues]

    weights = [len(c) for c in raw_cues]
    total_weight = sum(weights) or 1

    cues: List[Dict] = []
    t = 0.0
    for raw, w in zip(raw_cues, weights):
        dur = voice_duration * (w / total_weight)
        start = t
        end = min(t + dur, voice_duration)
        t = start + dur
        cues.append({
            "text": _wrap_two_lines(raw),
            "start": round(start, 3),
            "end": round(end, 3),
        })
    return cues


def _write_srt(cues: List[Dict], srt_path: str) -> str:
    lines: List[str] = []
    for i, cue in enumerate(cues, start=1):
        lines.append(str(i))
        lines.append(f"{_srt_timestamp(cue['start'])} --> {_srt_timestamp(cue['end'])}")
        lines.append(cue["text"])
        lines.append("")
    content = "\n".join(lines) + ("\n" if lines else "")

    path = Path(srt_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)


def build_subtitles(
    text: str,
    voice_duration: float,
    settings: Dict,
    *,
    srt_path: str,
    ass_path: str,
    video_w: int = 1080,
    video_h: int = 1920,
) -> Dict:
    """Split, time, and write subtitles for one project's voice-over.

    Always writes `srt_path` (UTF-8, diacritics intact). Writes `ass_path`
    via caption_overlays.build_overlays_ass() unless
    settings["subtitle_mode"] == "none", in which case the SRT still exists
    but the returned "ass" is None — the caller (montage.render_final) must
    then encode without a caption burn-in.

    Returns {"srt": str, "ass": str | None, "cues": [{"text","start","end"}]}.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("build_subtitles: text is empty")
    voice_duration = max(0.1, float(voice_duration or 0.0))

    cues = _build_cues(text, voice_duration, settings)
    srt_out = _write_srt(cues, srt_path)

    mode = str(settings.get("subtitle_mode") or "none").strip().lower()
    ass_out: Optional[str] = None

    if mode != "none" and cues:
        template_id = settings.get("caption_template_id") or "bold_impact"
        scale = float(settings.get("caption_scale") or 1.0)

        # "low but inside the safe zone, max ~15-20% of frame height" (spec
        # §11): SAFE_CAPTION_BOTTOM is the existing from-bottom margin used
        # elsewhere for bottom-aligned captions at the 1920px canonical
        # height; build_overlays_ass places text by its CENTER (\pos, an
        # #5 middle-center alignment) rather than a bottom margin, so convert
        # the margin into a center y-fraction here.
        y_pct = max(0.05, min(0.95, 1.0 - (SAFE_CAPTION_BOTTOM / _CANONICAL_H)))

        style_override: Dict = {}
        if settings.get("caption_uppercase"):
            # Explicit per-project override — never mutate the preset dict
            # itself (matches the captioner.py custom-style convention).
            style_override["uppercase"] = True

        overlays = [
            {
                "text": cue["text"],
                "start_t": cue["start"],
                "end_t": max(cue["start"] + 0.05, cue["end"]),
                "template_id": template_id,
                "scale": scale,
                "x_pct": 0.5,
                "y_pct": y_pct,
                **({"style": style_override} if style_override else {}),
            }
            for cue in cues
        ]
        ass_out = build_overlays_ass(overlays, video_w, video_h, ass_path)
    elif mode == "none":
        logger.info(
            "tiktok subtitles: subtitle_mode='none' — SRT written (%s), ASS skipped (nothing to burn)",
            srt_out,
        )

    return {"srt": srt_out, "ass": ass_out, "cues": cues}
