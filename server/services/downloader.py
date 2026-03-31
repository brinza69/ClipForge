"""
ClipForge — Downloader Service
Downloads videos from URLs using yt-dlp with progress tracking.
"""

import logging
import subprocess
import json
import re
import os
import asyncio
from pathlib import Path
from typing import Optional, Callable, Awaitable

from config import settings

logger = logging.getLogger("clipforge.downloader")


async def download_video(
    url: str,
    project_id: str,
    on_progress: Optional[Callable[[float, str], Awaitable[None]]] = None,
    format_id: Optional[str] = None,
    audio_only: bool = False,
) -> dict:
    """
    Download a video using yt-dlp.

    Returns dict with:
        video_path: str
        duration: float
        width: int
        height: int
        fps: float
        filesize: int
    """
    output_dir = settings.media_dir / project_id
    output_dir.mkdir(parents=True, exist_ok=True)

    output_template = str(output_dir / "source.%(ext)s")

    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--no-warnings",
        "--newline",  # Progress on new lines
        "--progress-template", "%(progress._percent_str)s %(progress._speed_str)s %(progress._eta_str)s",
        "-o", output_template,
    ]

    if audio_only:
        cmd.extend([
            "-x",
            "--audio-format", "wav",
            "--audio-quality", "0",
        ])
    elif format_id:
        cmd.extend(["-f", format_id])
    else:
        # Best quality with reasonable size
        cmd.extend([
            "-f", "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
            "--merge-output-format", "mp4",
        ])

    cmd.append(url)

    logger.info(f"Starting download: {' '.join(cmd)}")

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
    )

    last_progress = 0.0
    while True:
        line = await process.stdout.readline()
        if not line:
            break
        line_str = line.decode("utf-8", errors="replace").strip()

        # Parse progress from yt-dlp output
        percent_match = re.search(r"(\d+\.?\d*)%", line_str)
        if percent_match and on_progress:
            progress = float(percent_match.group(1)) / 100.0
            if progress > last_progress:
                last_progress = progress
                await on_progress(progress, f"Downloading... {percent_match.group(1)}%")

    await process.wait()

    if process.returncode != 0:
        stderr = await process.stderr.read()
        error_msg = stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"Download failed: {error_msg[:300]}")

    # Find the downloaded file
    video_path = None
    for f in output_dir.iterdir():
        if f.stem == "source" and f.suffix in (".mp4", ".webm", ".mkv", ".wav", ".mp3"):
            video_path = f
            break

    if not video_path:
        raise RuntimeError("Download completed but file not found")

    # Get media info using ffprobe
    media_info = await get_media_info(video_path)

    if on_progress:
        await on_progress(1.0, "Download complete")

    logger.info(f"Download complete: {video_path} ({media_info.get('filesize', 0)} bytes)")

    return {
        "video_path": str(video_path),
        **media_info,
    }


async def get_media_info(file_path: Path) -> dict:
    """Extract media info using ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(file_path),
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
    )

    stdout, _ = await process.communicate()
    info = json.loads(stdout.decode())

    duration = float(info.get("format", {}).get("duration", 0))
    filesize = int(info.get("format", {}).get("size", 0))

    width = height = fps = 0
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            width = stream.get("width", 0)
            height = stream.get("height", 0)
            fps_str = stream.get("r_frame_rate", "30/1")
            try:
                num, den = fps_str.split("/")
                fps = round(float(num) / float(den), 2)
            except (ValueError, ZeroDivisionError):
                fps = 30.0
            break

    return {
        "duration": duration,
        "width": width,
        "height": height,
        "fps": fps,
        "filesize": filesize,
    }
