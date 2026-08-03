"""
ClipForge — shared ffmpeg/ffprobe helpers for the AI Stream Clipper.

One place to resolve the binaries and run them safely, so every clipper module
behaves identically. Mirrors the resolution logic already used by
services/caption_overlays.py and services/downloader.py (honour
settings.ffmpeg_location first, then PATH).

Two rules enforced here, both from hard-won repo experience:

  * Commands are ALWAYS argument lists. Never a shell string, never
    shell=True — a title or path with a quote in it must not be able to run
    anything.
  * Long-running children get their stderr drained by capture_output plus a
    timeout. The repo has already been bitten by a chatty child (realesrgan)
    filling the 64 KB pipe buffer and blocking forever; when output size is
    genuinely unbounded, redirect to a file instead of PIPE.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Sequence

logger = logging.getLogger("clipforge.clipper.ffmpeg")


class FFmpegError(RuntimeError):
    """An ffmpeg/ffprobe invocation failed. Carries a trimmed stderr tail."""


def creationflags() -> int:
    """CREATE_NO_WINDOW on Windows so batch runs don't flash console windows."""
    return 0x08000000 if os.name == "nt" else 0


def _resolve(name: str) -> str:
    from config import settings

    exe = f"{name}.exe" if os.name == "nt" else name
    loc = settings.ffmpeg_location
    if loc:
        candidate = Path(loc) / exe
        if candidate.exists():
            return str(candidate)
    return shutil.which(exe) or exe


def ffmpeg_bin() -> str:
    return _resolve("ffmpeg")


def ffprobe_bin() -> str:
    return _resolve("ffprobe")


def run(cmd: Sequence[str], *, timeout: float = 600.0, what: str = "ffmpeg") -> str:
    """Run an ffmpeg-family command. Returns stdout; raises FFmpegError.

    `cmd` must be a sequence of already-separated arguments — never a string.
    """
    if isinstance(cmd, (str, bytes)):  # pragma: no cover - programmer error
        raise TypeError("run() takes an argument list, not a shell string")
    try:
        proc = subprocess.run(
            list(cmd),
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=creationflags(),
        )
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError(f"{what} timed out after {timeout:.0f}s") from exc

    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-8:])
        raise FFmpegError(f"{what} failed (rc={proc.returncode}): {tail}")
    return proc.stdout or ""


def probe(path: str) -> dict[str, Any]:
    """Full ffprobe JSON for a media file (format + streams)."""
    out = run(
        [
            ffprobe_bin(), "-v", "error",
            "-print_format", "json",
            "-show_format", "-show_streams",
            str(path),
        ],
        timeout=120,
        what="ffprobe",
    )
    try:
        return json.loads(out or "{}")
    except json.JSONDecodeError as exc:
        raise FFmpegError(f"ffprobe returned invalid JSON for {path}") from exc


def video_info(path: str) -> dict[str, Any]:
    """Normalised {width, height, fps, duration, has_audio, codec} for a file.

    fps comes from avg_frame_rate ("60000/1001"), falling back to r_frame_rate.
    duration prefers the container's, falling back to the video stream's.
    """
    data = probe(path)
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)

    if not video:
        raise FFmpegError(f"no video stream in {path}")

    def _rate(value: str | None) -> float:
        if not value or "/" not in value:
            try:
                return float(value) if value else 0.0
            except (TypeError, ValueError):
                return 0.0
        num, _, den = value.partition("/")
        try:
            n, d = float(num), float(den)
        except ValueError:
            return 0.0
        return n / d if d else 0.0

    fps = _rate(video.get("avg_frame_rate")) or _rate(video.get("r_frame_rate"))

    duration = 0.0
    for source in (data.get("format", {}).get("duration"), video.get("duration")):
        try:
            duration = float(source)
            if duration > 0:
                break
        except (TypeError, ValueError):
            continue

    return {
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": round(fps, 3),
        "duration": round(duration, 3),
        "has_audio": has_audio,
        "codec": video.get("codec_name") or "",
    }


def even(value: float) -> int:
    """Round to the nearest even integer.

    H.264 with yuv420p REQUIRES even width and height; an odd crop or scale
    makes ffmpeg refuse the encode outright. Every rectangle that reaches a
    filtergraph goes through this.
    """
    n = int(round(float(value)))
    return n - (n % 2)


def escape_filter_path(path: str | Path) -> str:
    """Escape a path for use INSIDE an ffmpeg filter argument.

    libass filters take `subtitles=filename='C\\:/x/y.ass'`: forward slashes,
    and the Windows drive colon escaped so the filter parser doesn't read it as
    an option separator. Same transformation the existing caption code applies.
    """
    return str(path).replace("\\", "/").replace(":", "\\:")
