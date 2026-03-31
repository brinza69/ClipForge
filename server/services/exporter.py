"""
ClipForge — Export Service
FFmpeg-based video export pipeline:
  trim → reframe/crop → burn captions → normalize audio → render H.264 mp4
"""

import logging
import asyncio
import subprocess
import os
from pathlib import Path
from typing import Optional, Callable, Awaitable, Dict, Any

from config import settings
from services.reframer import build_crop_filter

logger = logging.getLogger("clipforge.exporter")


async def export_clip(
    video_path: str,
    output_path: str,
    start_time: float,
    end_time: float,
    reframe_data: Optional[Dict] = None,
    captions_path: Optional[str] = None,
    on_progress: Optional[Callable[[float, str], Awaitable[None]]] = None,
    width: int = None,
    height: int = None,
    fps: int = None,
    bitrate: str = None,
) -> str:
    """
    Export a video clip with reframing, captions, and audio normalization.

    Returns the output file path.
    """
    w = width or settings.export_width
    h = height or settings.export_height
    out_fps = fps or settings.export_fps
    br = bitrate or settings.export_bitrate

    duration = end_time - start_time

    logger.info(f"Exporting clip: {video_path} [{start_time:.1f}s-{end_time:.1f}s] → {output_path}")

    # Ensure output directory exists
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Build FFmpeg filter chain
    filters = []

    # 1. Crop / reframe
    if reframe_data:
        crop_filter = build_crop_filter(reframe_data)
        filters.append(crop_filter)
    else:
        # Default center crop to 9:16
        filters.append(f"crop=ih*9/16:ih:(iw-ih*9/16)/2:0")

    # 2. Scale to output resolution
    filters.append(f"scale={w}:{h}:flags=lanczos")

    # 3. Set framerate
    filters.append(f"fps={out_fps}")

    # Build the command
    cmd = [
        "ffmpeg",
        "-y",                                    # Overwrite output
        "-ss", str(start_time),                  # Seek to start
        "-i", video_path,                        # Input file
        "-t", str(duration),                     # Duration
    ]

    # Build video filter string
    vf_chain = ",".join(filters)

    # Add subtitle burn-in if captions exist
    if captions_path and Path(captions_path).exists():
        # Subtitles filter must come after scaling
        escaped_path = captions_path.replace("\\", "/").replace(":", "\\:")
        vf_chain += f",subtitles='{escaped_path}'"

    cmd.extend([
        "-vf", vf_chain,
        "-c:v", settings.export_codec,           # H.264
        "-preset", "medium",
        "-crf", "18",                            # High quality
        "-b:v", br,
        "-maxrate", br,
        "-bufsize", f"{int(br.rstrip('MmKk')) * 2}M" if br.rstrip("MmKk").isdigit() else "16M",
        "-c:a", settings.export_audio_codec,     # AAC
        "-b:a", settings.export_audio_bitrate,
        "-ar", "44100",
        "-af", "loudnorm=I=-16:LRA=11:TP=-1.5", # Audio normalization
        "-movflags", "+faststart",               # Web-optimized
        "-pix_fmt", "yuv420p",                   # Compatibility
        "-progress", "pipe:1",                   # Progress output
        output_path,
    ])

    logger.info(f"FFmpeg command: {' '.join(cmd)}")

    if on_progress:
        await on_progress(0.05, "Starting export...")

    # Run FFmpeg
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
    )

    # Parse progress
    while True:
        line = await process.stdout.readline()
        if not line:
            break

        line_str = line.decode("utf-8", errors="replace").strip()

        if line_str.startswith("out_time_us="):
            try:
                time_us = int(line_str.split("=")[1])
                time_s = time_us / 1_000_000
                progress = min(time_s / duration, 0.95)
                if on_progress:
                    pct = int(progress * 100)
                    await on_progress(progress, f"Rendering... {pct}%")
            except (ValueError, ZeroDivisionError):
                pass

    await process.wait()

    if process.returncode != 0:
        stderr = await process.stderr.read()
        error = stderr.decode("utf-8", errors="replace")
        logger.error(f"FFmpeg export failed: {error[:500]}")
        raise RuntimeError(f"Export failed: {error[:300]}")

    # Verify output exists and has content
    output = Path(output_path)
    if not output.exists() or output.stat().st_size < 1000:
        raise RuntimeError("Export produced empty or missing file")

    filesize = output.stat().st_size
    logger.info(f"Export complete: {output_path} ({filesize / (1024*1024):.1f} MB)")

    if on_progress:
        await on_progress(1.0, "Export complete")

    return str(output)


async def generate_thumbnail(
    video_path: str,
    output_path: str,
    timestamp: float = 1.0,
) -> str:
    """Generate a thumbnail from a video at a specific timestamp."""
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(timestamp),
        "-i", video_path,
        "-vframes", "1",
        "-vf", "scale=640:-1",
        "-q:v", "2",
        output_path,
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
    )
    await process.wait()

    return output_path
