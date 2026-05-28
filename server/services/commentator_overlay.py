"""
ClipForge — Commentator Overlay

Composites a small "commentator" video on top of the final captioned remix.

Pipeline:

  main video (with captions)
       │
       │   ┌─ commentator clip (chroma-keyed or alpha)
       │   │
       │   ▼   loop while shorter than main, scaled to {scale × main_w},
       │   │   positioned at the chosen corner (or custom x_pct/y_pct)
       │   │
       ▼   ▼
   final mp4 (same dimensions as main, audio = main's audio only)

The overlay audio is dropped — only the underlying TTS voice survives. That's
what you want for a podcast-style avatar (its lip-flap animation might be
synced to placeholder audio that would clash with the actual narration).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from config import settings
from services.commentators import (
    _video_path as commentator_video_path,
    _ai_processed_path as commentator_ai_path,
    get_preset,
)

logger = logging.getLogger("clipforge.commentator_overlay")


def _ffmpeg() -> str:
    loc = settings.ffmpeg_location
    if loc:
        exe = Path(loc) / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if exe.exists():
            return str(exe)
    return shutil.which("ffmpeg") or "ffmpeg"


def _creationflags() -> int:
    return 0x08000000 if os.name == "nt" else 0


def composite_commentator(
    main_video_path: str,
    output_path: str,
    *,
    preset_id: str,
    chroma_override: Optional[str] = None,            # "__none__" disables keying; "" or None uses preset
    chroma_similarity_override: Optional[float] = None,
    chroma_blend_override: Optional[float] = None,
    # Legacy params accepted for API stability; ignored.
    scale: Optional[float] = None,
    position: Optional[str] = None,
    x_pct: Optional[float] = None,
    y_pct: Optional[float] = None,
    margin_pct: float = 0.02,
) -> dict:
    """
    Composite a full-frame commentator overlay onto the main video.

    The commentator clip is stretched to match the main video's exact pixel
    dimensions and overlaid at (0, 0). If a chroma key is configured on the
    preset (or passed via chroma_override), the corresponding background
    color is keyed out so the main video shows through.
    """
    preset = get_preset(preset_id)
    if not preset:
        raise FileNotFoundError(f"commentator preset not found: {preset_id}")

    # AI-processed WebM (real alpha) takes precedence over the raw mp4 when
    # the preset was processed with rembg. This bypasses chromakey entirely.
    ai_path = commentator_ai_path(preset_id)
    use_ai = ai_path.exists()
    src_overlay = ai_path if use_ai else commentator_video_path(preset_id)
    if not src_overlay.exists():
        raise FileNotFoundError(f"commentator video missing for preset: {preset_id}")

    main_w, main_h = _probe_dims(main_video_path)
    # Even-align for libx264 yuv420p compatibility (already even for typical
    # source dims, but safe to enforce).
    main_w -= main_w % 2
    main_h -= main_h % 2

    # Resolve chroma settings with three-way precedence:
    #   "__none__" sentinel  → disable keying entirely (per-run override)
    #   explicit non-empty   → use override
    #   None/empty           → fall back to preset's saved value
    # When AI-processed alpha is in use, force chroma_color=None so the
    # already-baked alpha channel is what drives transparency.
    if use_ai:
        chroma_color = None
    elif chroma_override == "__none__":
        chroma_color = None
    elif chroma_override:
        chroma_color = chroma_override
    else:
        chroma_color = preset.get("chroma_key")

    chroma_similarity = float(
        chroma_similarity_override
        if chroma_similarity_override is not None
        else (preset.get("chroma_similarity") or 0.10)
    )
    chroma_blend = float(
        chroma_blend_override
        if chroma_blend_override is not None
        else (preset.get("chroma_blend") or 0.05)
    )

    # Overlay filter chain:
    #   1. loop forever so it covers the full main duration
    #   2. (optional) chroma-key the background to transparent
    #   3. SCALE to exact main dimensions (no aspect preservation — user
    #      authored the clip at the target resolution; if they didn't, the
    #      slight stretch is fine and matches their explicit ask of
    #      "suprapus complet, aceleași dimensiuni")
    #   4. force yuva420p so alpha rides through overlay
    overlay_filters = [
        "loop=loop=-1:size=32767:start=0",
    ]
    if chroma_color:
        overlay_filters.append(
            f"chromakey=color={chroma_color}:similarity={chroma_similarity:.3f}:blend={chroma_blend:.3f}"
        )
    overlay_filters += [
        f"scale={main_w}:{main_h}",
        "format=yuva420p",
    ]
    overlay_chain = ",".join(overlay_filters)

    filter_complex = (
        f"[1:v]{overlay_chain}[ovl];"
        f"[0:v][ovl]overlay=0:0:shortest=1[out]"
    )

    cmd = [
        _ffmpeg(), "-y", "-loglevel", "error",
        "-i", str(main_video_path),       # input 0 = main captioned mp4
    ]
    # VP9 WebM with `alpha_mode=1` needs the libvpx-vp9 decoder explicitly
    # — auto-detect picks a generic VP9 decoder that drops the alpha plane.
    if use_ai:
        cmd += ["-c:v", "libvpx-vp9"]
    cmd += [
        "-i", str(src_overlay),           # input 1 = commentator clip
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-map", "0:a?",                   # keep main's audio only
        "-c:v", "libx264", "-preset", "slow", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ]
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(cmd, capture_output=True, text=True, creationflags=_creationflags())
    if r.returncode != 0:
        tail = "\n".join((r.stderr or "").strip().splitlines()[-10:])
        raise RuntimeError(f"commentator overlay ffmpeg failed: {tail}")

    return {
        "preset_id": preset_id,
        "overlay_size_px": [main_w, main_h],
        "anchor_xy": [0, 0],
        "chroma_key": chroma_color,
    }


def _probe_dims(path: str) -> tuple[int, int]:
    ffprobe = _ffmpeg().replace("ffmpeg", "ffprobe")
    if not Path(ffprobe).exists() and ffprobe != "ffprobe":
        ffprobe = shutil.which("ffprobe") or "ffprobe"
    r = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, creationflags=_creationflags(),
    )
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {r.stderr[-300:]}")
    lines = [line.strip() for line in r.stdout.splitlines() if line.strip()]
    if len(lines) < 2:
        raise RuntimeError(f"ffprobe didn't return dims for {path}: {r.stdout!r}")
    return int(lines[0]), int(lines[1])
