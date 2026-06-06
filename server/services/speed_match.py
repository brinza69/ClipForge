"""
ClipForge — Speed-Match Service

The remix pipeline replaces a video's audio with a TTS-generated voice. The
TTS audio rarely matches the source video length, so we time-stretch the
video (without cutting any content) so its duration equals the voice's, or
is at most ~1 second longer when the exact factor would push us into a bad
ffmpeg case.

  factor   = target_video_duration / current_video_duration
  setpts   = "PTS*factor"   # >1 slows down, <1 speeds up

We do NOT use atempo on the original audio because the pipeline discards it —
the new audio track is the TTS voice, muxed in unchanged.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from config import settings

logger = logging.getLogger("clipforge.speed_match")

# Real-world safety bounds on the time-stretch factor. Outside these the
# result looks/feels bad (chipmunks vs. slow-motion). We log a warning but
# still proceed — the user explicitly asked the pipeline to match length.
MIN_FACTOR = 0.4
MAX_FACTOR = 3.0


def _ffmpeg_bin() -> str:
    loc = settings.ffmpeg_location
    if loc:
        exe = Path(loc) / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if exe.exists():
            return str(exe)
    return shutil.which("ffmpeg") or "ffmpeg"


def _ffprobe_bin() -> str:
    f = _ffmpeg_bin()
    p = f.replace("ffmpeg", "ffprobe")
    if Path(p).exists() or p == "ffprobe":
        return p
    return shutil.which("ffprobe") or "ffprobe"


def _creationflags() -> int:
    return 0x08000000 if os.name == "nt" else 0


def probe_duration(path: str) -> float:
    """Container duration in seconds (float). Raises on probe failure."""
    r = subprocess.run(
        [
            _ffprobe_bin(), "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True, text=True, creationflags=_creationflags(),
    )
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"ffprobe duration failed for {path}: {r.stderr[-300:]}")
    return float(r.stdout.strip())


def compute_speed_plan(video_path: str, voice_path: str) -> dict:
    """Compute the time-stretch plan WITHOUT encoding anything.

    Returns the factor, durations, and the ready-to-use video `vfilter`
    string (setpts + optional minterpolate + overhang pad + trim). Callers
    can either feed `vfilter` to their own ffmpeg pass (e.g. the fused
    speed-match + caption burn) or use `match_video_to_voice` to encode a
    standalone speed-matched file.
    """
    if not Path(video_path).exists():
        raise FileNotFoundError(video_path)
    if not Path(voice_path).exists():
        raise FileNotFoundError(voice_path)

    video_dur = probe_duration(video_path)
    voice_dur = probe_duration(voice_path)
    if video_dur <= 0:
        raise RuntimeError(f"video has zero duration: {video_path}")
    if voice_dur <= 0:
        raise RuntimeError(f"voice has zero duration: {voice_path}")

    # Target duration: voice duration. If that requires an unrealistic stretch,
    # we still aim for it but clamp the factor (1-second overhang fallback).
    factor = voice_dur / video_dur
    clamped = False
    if factor < MIN_FACTOR:
        factor = MIN_FACTOR
        clamped = True
    elif factor > MAX_FACTOR:
        factor = MAX_FACTOR
        clamped = True

    stretched_dur = video_dur * factor
    overhang = max(0.0, voice_dur - stretched_dur)

    logger.info(
        f"speed_match: video {video_dur:.2f}s, voice {voice_dur:.2f}s, "
        f"factor={factor:.3f}{' (clamped)' if clamped else ''}, "
        f"stretched_dur={stretched_dur:.2f}s, overhang={overhang:.2f}s"
    )

    # Probe the source framerate so we keep the output at the same fps and
    # decide whether motion-interpolation is worth it.
    try:
        fps_str = subprocess.run(
            [_ffprobe_bin(), "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=avg_frame_rate",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
            capture_output=True, text=True, creationflags=_creationflags(),
        ).stdout.strip()
        num, den = fps_str.split("/", 1) if "/" in fps_str else (fps_str, "1")
        src_fps = (float(num) / max(1.0, float(den))) if den else 30.0
    except Exception:
        src_fps = 30.0
    target_fps = src_fps if 0 < src_fps <= 60 else 30.0

    # setpts stretches/compresses playback time; ffmpeg fills new fps slots by
    # duplicating frames (choppy on slow-downs). For stretch >1.15 we run
    # minterpolate AFTER setpts to synthesize motion-compensated frames.
    vfilter = f"setpts=PTS*{factor:.6f}"
    if factor > 1.15:
        vfilter += f",minterpolate=fps={target_fps:.3f}:mi_mode=blend"
    if overhang > 0.01:
        vfilter += f",tpad=stop_mode=clone:stop_duration={overhang:.3f}"
    # Cap to voice duration in case float rounding pushes slightly over.
    vfilter += f",trim=duration={voice_dur:.6f},setpts=PTS-STARTPTS"

    interp_msg = " + minterpolate" if factor > 1.15 else ""
    logger.info(f"speed_match: src_fps={src_fps:.3f}, target_fps={target_fps:.3f}{interp_msg}")

    return {
        "video_dur": video_dur,
        "voice_dur": voice_dur,
        "factor": round(factor, 6),
        "stretched_dur": round(stretched_dur, 3),
        "overhang_s": round(overhang, 3),
        "clamped": clamped,
        "src_fps": src_fps,
        "target_fps": target_fps,
        "vfilter": vfilter,
    }
