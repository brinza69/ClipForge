"""
ClipForge — Auto Story Doodle: low-level FFmpeg helpers for the renderer.

Split out of renderer.py to stay under the 500-line file limit. Contains:
  - binary discovery (ffmpeg/ffprobe)
  - subprocess execution + nvenc probing
  - path/filter string escaping
  - placeholder frame generation
  - zoompan filter construction
  - per-scene segment rendering
  - audio/video concat helpers

Only imported by renderer.py within this package (no cross-module imports
outside this package).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("clipforge.doodle.renderer")

FPS = 30
_NVENC_CACHE: Dict[str, bool] = {}


# ---------------------------------------------------------------------------
# Binary discovery
# ---------------------------------------------------------------------------

def ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def ffprobe_bin() -> str:
    return shutil.which("ffprobe") or "ffprobe"


def creationflags() -> int:
    return 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW on Windows


async def run_ffmpeg(args: List[str], timeout: float = 1800.0) -> str:
    """Run ffmpeg (or ffprobe) and return stderr text. Raises RuntimeError
    with the last ~400 chars of stderr on non-zero exit."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=creationflags(),
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        with contextlib.suppress(Exception):
            proc.kill()
        raise RuntimeError(f"ffmpeg timed out after {timeout:.0f}s: {' '.join(args)}")

    stderr_text = (stderr_b or b"").decode("utf-8", errors="replace")
    if proc.returncode != 0:
        tail = stderr_text.strip()[-400:]
        raise RuntimeError(f"ffmpeg failed (exit {proc.returncode}): {tail}")
    return stderr_text


async def has_nvenc() -> bool:
    ffmpeg_path = ffmpeg_bin()
    if ffmpeg_path in _NVENC_CACHE:
        return _NVENC_CACHE[ffmpeg_path]

    ok = False
    try:
        proc = await asyncio.create_subprocess_exec(
            ffmpeg_path, "-hide_banner", "-encoders",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=creationflags(),
        )
        stdout_b, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        if b"h264_nvenc" in (stdout_b or b""):
            probe = await asyncio.create_subprocess_exec(
                ffmpeg_path, "-y", "-hide_banner", "-v", "error",
                "-f", "lavfi", "-i", "color=c=black:s=64x64:r=30",
                "-frames:v", "1",
                "-c:v", "h264_nvenc",
                "-f", "null", "-",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=creationflags(),
            )
            await asyncio.wait_for(probe.communicate(), timeout=15)
            ok = probe.returncode == 0
    except Exception as e:
        logger.info(f"NVENC probe failed (will use libx264): {e}")
        ok = False

    _NVENC_CACHE[ffmpeg_path] = ok
    logger.info("NVENC available — using h264_nvenc" if ok else "NVENC unavailable — using libx264")
    return ok


async def probe_duration(path: Path) -> float:
    args = [
        ffprobe_bin(), "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=creationflags(),
    )
    stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=30)
    if proc.returncode != 0:
        tail = (stderr_b or b"").decode("utf-8", errors="replace").strip()[-400:]
        raise RuntimeError(f"ffprobe failed on {path}: {tail}")
    text = (stdout_b or b"").decode("utf-8", errors="replace").strip()
    try:
        return float(text)
    except ValueError:
        raise RuntimeError(f"ffprobe returned non-numeric duration for {path}: {text!r}")


# ---------------------------------------------------------------------------
# Path / filter escaping helpers
# ---------------------------------------------------------------------------

def escape_filter_path(path: Path) -> str:
    """Escape an absolute Windows path for use inside an ffmpeg filtergraph
    string (e.g. subtitles=filename='...'). Forward slashes + escaped colon."""
    p = str(path).replace("\\", "/")
    p = p.replace(":", "\\:")
    return p


def escape_drawtext(text: str) -> str:
    """Escape text for ffmpeg drawtext filter (colons, quotes, backslashes,
    percent)."""
    text = text.replace("\\", "\\\\")
    text = text.replace(":", "\\:")
    text = text.replace("'", "’")  # curly apostrophe avoids quote issues
    text = text.replace("%", "\\%")
    return text


def even(n: int) -> int:
    n = int(round(n))
    return n if n % 2 == 0 else n + 1


# ---------------------------------------------------------------------------
# Placeholder frame generation
# ---------------------------------------------------------------------------

_FONT_FILE_CACHE: Optional[str] = None


def _find_font_file() -> Optional[str]:
    """
    Locate a usable TTF for drawtext's `fontfile` option.

    We use `fontfile=<path>` rather than `font=<name>` because this ffmpeg
    build's fontconfig lookup (`font=`) crashes with an access violation on
    machines without a fontconfig.conf (verified: Windows box with no
    fontconfig config file present). `fontfile=` bypasses fontconfig
    entirely by loading the TTF directly via freetype.
    """
    global _FONT_FILE_CACHE
    if _FONT_FILE_CACHE is not None:
        return _FONT_FILE_CACHE or None

    candidates = [
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/calibri.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for c in candidates:
        if Path(c).exists():
            _FONT_FILE_CACHE = c
            return c
    _FONT_FILE_CACHE = ""
    return None


def _escape_fontfile(path: str) -> str:
    r"""
    Escape a font file path for drawtext's `fontfile=` option value.

    drawtext applies its OWN colon-unescaping to option values on top of the
    filtergraph parser's, so a Windows drive-letter colon here needs to
    survive two escaping passes: `\\:` (double backslash), not the single
    `\:` that's sufficient for the outer `subtitles=filename=...` filter.
    Verified empirically against ffmpeg 8.1 (gyan.dev build): `\:` alone
    still fails to parse ("No option name near ...").
    """
    p = path.replace("\\", "/")
    p = p.replace(":", "\\\\:")
    return p


async def make_placeholder_image(text: str, width: int, height: int, out_path: Path) -> None:
    """White background + big black centered text (drawtext), no external
    image deps (PIL not required)."""
    safe_text = escape_drawtext(text or "Image coming soon")
    font_size = max(28, round(min(width, height) * 0.06))
    font_file = _find_font_file()
    font_arg = f"fontfile={_escape_fontfile(font_file)}:" if font_file else ""
    drawtext = (
        f"drawtext=text='{safe_text}':fontcolor=black:fontsize={font_size}:"
        f"{font_arg}box=0:line_spacing=10:"
        "x=(w-text_w)/2:y=(h-text_h)/2:"
        "fix_bounds=1"
    )
    vf = f"color=c=white:s={width}x{height}:d=1,{drawtext}"
    args = [
        ffmpeg_bin(), "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", vf,
        "-frames:v", "1",
        str(out_path),
    ]
    await run_ffmpeg(args, timeout=30)


# ---------------------------------------------------------------------------
# Motion (zoompan) filter construction
# ---------------------------------------------------------------------------

def zoompan_filter(width: int, height: int, duration: float, motion_style: str,
                    intensity: float, scene_index: int) -> str:
    """
    Build the zoompan filter for one scene. Source is pre-scaled 2x (by the
    caller) to avoid jitter, then zoompan'd back down to the target
    resolution. Returns "" for motion_style == "none" (caller should skip
    zoompan entirely in that case).
    """
    frames = max(1, round(duration * FPS))
    intensity = max(0.0, min(1.0, intensity))
    max_zoom = 1.0 + 0.08 * (intensity if intensity > 0 else 0.5)

    if motion_style == "none":
        return ""

    effective_style = motion_style
    if motion_style == "subtle":
        effective_style = "zoom_in" if scene_index % 2 == 0 else "zoom_out"

    # Linear ramp across the WHOLE scene, expressed on the output frame number
    # `on` (deterministic — avoids zoompan's stateful-`zoom` pitfalls). The old
    # expressions were broken in both directions: zoom_in stepped by
    # max_zoom/frames from zoom=1.0 so it hit the cap in a fraction of a second
    # (instant jump, then frozen), and zoom_out tried to step DOWN from 1.0
    # (zoompan starts at 1.0, can't go below) so it never moved at all.
    step = (max_zoom - 1.0) / frames
    if effective_style == "zoom_in":
        z_expr = f"min(1.0+{step:.6f}*on,{max_zoom:.4f})"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"
    elif effective_style == "zoom_out":
        z_expr = f"max({max_zoom:.4f}-{step:.6f}*on,1.0)"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"
    elif effective_style == "pan":
        # Slow horizontal pan at fixed mild zoom so we have room to pan.
        z_expr = "1.06"
        pan_range = 0.10 * (intensity if intensity > 0 else 0.5)
        denom = max(frames - 1, 1)
        x_expr = f"(iw-iw/zoom)*(on/{denom})*{pan_range:.4f}+(iw-iw/zoom)/2*(1-{pan_range:.4f})"
        y_expr = "ih/2-(ih/zoom/2)"
    else:
        z_expr = "1.0"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"

    return (
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':"
        f"d={frames}:s={width}x{height}:fps={FPS}"
    )


async def render_scene_segment(
    image_path: Path,
    duration: float,
    width: int,
    height: int,
    motion_style: str,
    motion_intensity: float,
    scene_index: int,
    out_path: Path,
) -> None:
    duration = max(0.1, float(duration))
    scale_pad = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=white"
    )

    zoompan = zoompan_filter(width, height, duration, motion_style, motion_intensity, scene_index)

    if zoompan:
        # Upscale 2x before zoompan (standard trick to avoid jitter), then
        # zoompan handles the final scale back down to width x height.
        vf = f"{scale_pad},scale={width * 2}:{height * 2},{zoompan},format=yuv420p"
    else:
        vf = f"{scale_pad},format=yuv420p"

    args = [
        ffmpeg_bin(), "-y", "-loglevel", "error",
        "-loop", "1",
        "-i", str(image_path),
        "-t", f"{duration:.3f}",
        "-vf", vf,
        "-r", str(FPS),
        "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p",
        str(out_path),
    ]
    await run_ffmpeg(args, timeout=120)


# ---------------------------------------------------------------------------
# Concat helpers
# ---------------------------------------------------------------------------

async def concat_audio(audio_paths: List[Path], out_path: Path) -> float:
    if len(audio_paths) == 1:
        shutil.copyfile(audio_paths[0], out_path)
        return await probe_duration(out_path)

    list_path = out_path.parent / f"_audio_concat_{out_path.stem}.txt"
    lines = []
    for p in audio_paths:
        escaped = str(p.resolve()).replace("\\", "/").replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    list_path.write_text("\n".join(lines), encoding="utf-8")

    try:
        args = [
            ffmpeg_bin(), "-y", "-loglevel", "error",
            "-f", "concat", "-safe", "0",
            "-i", str(list_path),
            "-c", "pcm_s16le",
            str(out_path),
        ]
        await run_ffmpeg(args, timeout=300)
    finally:
        list_path.unlink(missing_ok=True)

    return await probe_duration(out_path)


async def concat_video_segments(segment_paths: List[Path], out_path: Path) -> None:
    list_path = out_path.parent / "_video_concat_list.txt"
    lines = []
    for p in segment_paths:
        escaped = str(p.resolve()).replace("\\", "/").replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    list_path.write_text("\n".join(lines), encoding="utf-8")

    try:
        args = [
            ffmpeg_bin(), "-y", "-loglevel", "error",
            "-f", "concat", "-safe", "0",
            "-i", str(list_path),
            "-c", "copy",
            str(out_path),
        ]
        await run_ffmpeg(args, timeout=600)
    finally:
        list_path.unlink(missing_ok=True)
