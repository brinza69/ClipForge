"""
ClipForge — Export Service
FFmpeg-based video export pipeline:
  trim → reframe/crop → burn captions → normalize audio → render H.264 mp4
"""

import logging
import asyncio
import subprocess
import threading
import shutil
import tempfile
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
    encoding_preset: str = None,
) -> str:
    """
    Export a video clip with reframing, captions, and audio normalization.

    Returns the output file path.
    """
    w = width or settings.export_width
    h = height or settings.export_height
    out_fps = fps or settings.export_fps
    br = bitrate or settings.export_bitrate
    def _calc_bufsize(bitrate_str: str) -> str:
        """
        ffmpeg bufsize must match bitrate unit scale.
        Our default is usually like '4000k' (k = kbps), not '4000' megabits.
        """
        s = (bitrate_str or "").strip().lower()
        if not s:
            return "16M"
        try:
            if s.endswith("k"):
                v = int(float(s[:-1]))
                return f"{v * 2}k"
            if s.endswith("m"):
                v = int(float(s[:-1]))
                return f"{v * 2}M"
            if s.endswith("g"):
                v = float(s[:-1])
                return f"{int(v * 2)}G"
            # No suffix: interpret as Mbps-like and be conservative.
            v = int(float(s))
            return f"{max(1, v * 2)}M"
        except Exception:
            return "16M"

    bufsize = _calc_bufsize(br)

    duration = end_time - start_time

    logger.info(f"Exporting clip: {video_path} [{start_time:.1f}s-{end_time:.1f}s] → {output_path}")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    blurred_mode = bool(reframe_data and reframe_data.get("mode") in {"blurred", "blurred_background", "blurredBackground"})

    # Detect letterbox mode: if export resolution is landscape (w > h) we letterbox
    # the landscape video into a 9:16 vertical container instead of cropping.
    is_landscape_export = w > h
    if is_landscape_export:
        # Final output is always 9:16 — swap dimensions for the container
        container_w, container_h = h, w  # e.g. 1920x1080 → 1080x1920 container
    else:
        container_w, container_h = w, h

    # Build FFmpeg command
    cmd = [
        "ffmpeg",
        "-y",                                    # Overwrite output
        "-ss", str(start_time),                  # Seek to start
        "-i", video_path,                        # Input file
        "-t", str(duration),                     # Duration
    ]

    # Build video filter(s)
    if not reframe_data:
        crop_filter = f"crop=ih*9/16:ih:(iw-ih*9/16)/2:0"
    else:
        crop_filter = build_crop_filter(reframe_data)

    # Subtitles burn-in if captions exist.
    # libass on Windows hangs if the ASS path contains spaces. Copy to a
    # space-free temp path before passing to ffmpeg to avoid this.
    subtitles_filter = None
    _temp_captions: Optional[str] = None
    if captions_path and Path(captions_path).exists():
        safe_path = Path(captions_path)
        if " " in str(safe_path):
            fd, tmp = tempfile.mkstemp(suffix=".ass", prefix="cf_", dir=tempfile.gettempdir())
            os.close(fd)
            shutil.copy2(str(safe_path), tmp)
            safe_path = Path(tmp)
            _temp_captions = tmp
        # FFmpeg needs forward slashes and escaped colons in filter paths.
        abs_path = str(safe_path.resolve()).replace("\\", "/")
        escaped_path = abs_path.replace(":", "\\:")
        subtitles_filter = f"subtitles='{escaped_path}'"

    fade_filters = f"fade=t=in:st=0:d=0.4,fade=t=out:st={max(0, duration - 0.3):.3f}:d=0.3"
    audio_filters = f"loudnorm=I=-16:LRA=11:TP=-1.5,afade=t=in:st=0:d=0.3,afade=t=out:st={max(0, duration - 0.4):.3f}:d=0.4"
    encoding_args = [
        "-c:v", settings.export_codec,
        "-preset", encoding_preset or "medium",
        "-crf", "17",
        "-b:v", br,
        "-maxrate", br,
        "-bufsize", bufsize,
        "-c:a", settings.export_audio_codec,
        "-b:a", settings.export_audio_bitrate,
        "-ar", "44100",
        "-af", audio_filters,
        "-movflags", "+faststart",
        "-pix_fmt", "yuv420p",
        "-progress", "pipe:1",
        output_path,
    ]

    if is_landscape_export:
        # LETTERBOX MODE: scale video to fit container width, pad top/bottom with black
        # e.g. 1920x1080 source → scale to container_w wide, then pad to container_w x container_h
        scale_filter = f"scale={container_w}:-2:flags=lanczos"
        pad_filter = f"pad={container_w}:{container_h}:(ow-iw)/2:(oh-ih)/2:black"
        filters_str = f"{scale_filter},{pad_filter},{fade_filters},fps={out_fps}"
        if subtitles_filter:
            filters_str += f",{subtitles_filter}"
        cmd.extend(["-vf", filters_str])
        cmd.extend(encoding_args)

    elif blurred_mode:
        vf_chain_fg = f"{crop_filter},scale={container_w}:{container_h}:flags=lanczos"
        vf_chain_bg = f"scale={container_w}:{container_h}:force_original_aspect_ratio=increase,crop={container_w}:{container_h},boxblur=20:1"

        if subtitles_filter:
            filter_complex = (
                f"[0:v]split=2[fg][bg];"
                f"[bg]{vf_chain_bg}[bg2];"
                f"[fg]{vf_chain_fg}[fg2];"
                f"[bg2][fg2]overlay=(W-w)/2:(H-h)/2[v];"
                f"[v]{subtitles_filter},{fade_filters},fps={out_fps}[vout]"
            )
        else:
            filter_complex = (
                f"[0:v]split=2[fg][bg];"
                f"[bg]{vf_chain_bg}[bg2];"
                f"[fg]{vf_chain_fg}[fg2];"
                f"[bg2][fg2]overlay=(W-w)/2:(H-h)/2[v];"
                f"[v]{fade_filters},fps={out_fps}[vout]"
            )

        cmd.extend(["-filter_complex", filter_complex, "-map", "[vout]", "-map", "0:a?"])
        cmd.extend(encoding_args)
    else:
        # Default: crop → scale → fps → fade → optional subtitles
        filters = [crop_filter, f"scale={container_w}:{container_h}:flags=lanczos", f"fps={out_fps}"]
        filters.append(f"fade=t=in:st=0:d=0.4")
        filters.append(f"fade=t=out:st={max(0, duration - 0.3):.3f}:d=0.3")
        vf_chain = ",".join(filters)
        if subtitles_filter:
            vf_chain += f",{subtitles_filter}"
        cmd.extend(["-vf", vf_chain])
        cmd.extend(encoding_args)

    # Prepend ffmpeg binary path if configured (needed on Windows where it may not be in PATH)
    ffmpeg_bin = "ffmpeg"
    ffmpeg_loc = settings.ffmpeg_location
    if ffmpeg_loc:
        ffmpeg_bin = str(Path(ffmpeg_loc) / "ffmpeg")
    cmd[0] = ffmpeg_bin

    logger.info(f"FFmpeg command: {' '.join(cmd)}")

    if on_progress:
        await on_progress(0.05, "Starting export...")

    loop = asyncio.get_event_loop()
    cancel_event = threading.Event()
    proc_holder: list = [None]

    def _run_ffmpeg():
        creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
        )
        proc_holder[0] = proc

        # Drain stderr concurrently to prevent pipe-buffer deadlock.
        # FFmpeg writes verbose codec/filter logs to stderr (> 64 KB). If we
        # only read stdout, the stderr buffer fills up and ffmpeg blocks.
        stderr_chunks: list[bytes] = []

        def _drain_stderr():
            for chunk in iter(lambda: proc.stderr.read(4096), b""):
                stderr_chunks.append(chunk)

        stderr_drainer = threading.Thread(target=_drain_stderr, daemon=True)
        stderr_drainer.start()

        # Read stdout line-by-line for -progress pipe:1 updates
        for raw_line in proc.stdout:
            if cancel_event.is_set():
                proc.terminate()
                break
            line_str = raw_line.decode("utf-8", errors="replace").strip()
            if line_str.startswith("out_time_us="):
                try:
                    time_us = int(line_str.split("=")[1])
                    time_s = time_us / 1_000_000
                    progress = min(time_s / max(duration, 0.001), 0.95)
                    if on_progress:
                        pct = int(progress * 100)
                        asyncio.run_coroutine_threadsafe(
                            on_progress(progress, f"Rendering... {pct}%"), loop
                        )
                except (ValueError, ZeroDivisionError):
                    pass

        proc.wait()
        stderr_drainer.join(timeout=10)
        proc._stderr_captured = b"".join(stderr_chunks)
        return proc

    try:
        proc = await loop.run_in_executor(None, _run_ffmpeg)

        if cancel_event.is_set():
            raise asyncio.CancelledError()

        if proc.returncode != 0:
            error = getattr(proc, "_stderr_captured", b"").decode("utf-8", errors="replace")
            lines = error.splitlines()
            error_lines = [l for l in lines if l and not l.startswith("  ") and not l.startswith("built with")]
            tail = "\n".join(error_lines[-30:]).strip() or "\n".join(lines[-20:]).strip()
            logger.error(f"FFmpeg export failed:\n{tail[:1200]}")
            raise RuntimeError(f"Export failed: {tail[:800]}")
    except asyncio.CancelledError:
        logger.warning("Export coroutine cancelled; terminating FFmpeg process...")
        cancel_event.set()
        proc = proc_holder[0]
        if proc and proc.returncode is None:
            proc.terminate()
        raise
    finally:
        if _temp_captions and Path(_temp_captions).exists():
            try:
                os.remove(_temp_captions)
            except OSError:
                pass

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
    """Generate a high-quality thumbnail from a video at a specific timestamp."""
    # Use 9:16 crop + scale to 540x960 for sharp vertical preview thumbnails
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(timestamp),
        "-i", video_path,
        "-vframes", "1",
        "-vf", "crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=540:960:flags=lanczos",
        "-q:v", "2",
        output_path,
    ]

    ffmpeg_loc = settings.ffmpeg_location
    if ffmpeg_loc:
        cmd[0] = str(Path(ffmpeg_loc) / "ffmpeg")

    loop = asyncio.get_event_loop()

    def _run():
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )

    await loop.run_in_executor(None, _run)
    return output_path
