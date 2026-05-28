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


def match_video_to_voice(
    video_path: str,
    voice_path: str,
    output_path: str,
    *,
    max_overhang_s: float = 1.0,
    audio_bitrate: str = "192k",
) -> dict:
    """
    Speed/slow the video so its duration equals the voice's (allowing up to
    `max_overhang_s` of trailing freeze-frame slack when exact match would
    require an extreme stretch factor). Then drop the source video's audio
    and mux the TTS voice in unchanged.

    Returns a stats dict with the durations and the factor we applied.
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
    # we still aim for it but clamp the factor (the user explicitly opted into
    # 1-second overhang as fallback in the spec).
    target = voice_dur
    factor = target / video_dur

    clamped = False
    if factor < MIN_FACTOR:
        # Voice is far shorter than video → speeding up too aggressively.
        # Allow at most MIN_FACTOR; resulting video will overrun the voice
        # by (video_dur * MIN_FACTOR) - voice_dur. We trim back to voice_dur.
        factor = MIN_FACTOR
        clamped = True
    elif factor > MAX_FACTOR:
        # Voice much longer than video — let the video freeze on its last
        # frame for the overhang; trying to slow video > 3× looks awful.
        factor = MAX_FACTOR
        clamped = True

    stretched_dur = video_dur * factor
    overhang = max(0.0, voice_dur - stretched_dur)

    logger.info(
        f"speed_match: video {video_dur:.2f}s, voice {voice_dur:.2f}s, "
        f"factor={factor:.3f}{' (clamped)' if clamped else ''}, "
        f"stretched_dur={stretched_dur:.2f}s, overhang={overhang:.2f}s"
    )

    ffmpeg = _ffmpeg_bin()

    # Probe the source framerate so we can keep the output at the same fps
    # and decide whether motion-interpolation is worth it.
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

    # Build the filtergraph. setpts stretches/compresses the playback time
    # but ffmpeg fills the new fps slots by duplicating frames — that's
    # what makes a 1.3× slow-down look choppy. When the stretch is >1.15
    # we run minterpolate AFTER setpts to synthesize real intermediate
    # frames with motion compensation. Skipped when speeding the video up
    # (factor < 1) because frame-dropping is naturally smooth.
    vfilter = f"setpts=PTS*{factor:.6f}"
    if factor > 1.15:
        # `mi_mode=blend` blends each output frame from the two surrounding
        # source frames. ~3-5× realtime on 1080p, vs `mci` which is ~50×
        # slower but only marginally smoother. Looks like a soft motion-blur
        # smear instead of choppy frame duplicates — far easier on the eye.
        vfilter += f",minterpolate=fps={target_fps:.3f}:mi_mode=blend"
    if overhang > 0.01:
        # +overhang seconds of duplicated last frame (safe — it's a freeze).
        vfilter += f",tpad=stop_mode=clone:stop_duration={overhang:.3f}"
    # Cap to voice duration in case of float rounding pushing us slightly over.
    vfilter += f",trim=duration={voice_dur:.6f},setpts=PTS-STARTPTS"

    interp_msg = " + minterpolate" if factor > 1.15 else ""
    logger.info(f"speed_match: src_fps={src_fps:.3f}, target_fps={target_fps:.3f}{interp_msg}")

    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-i", str(video_path),
        "-i", str(voice_path),
        "-map", "0:v:0",      # video from input 0
        "-map", "1:a:0",      # audio from input 1 (the voice)
        "-filter:v", vfilter,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", audio_bitrate,
        "-shortest",          # belt-and-suspenders: stop at shorter stream
        "-movflags", "+faststart",
        str(output_path),
    ]

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        cmd, capture_output=True, text=True, creationflags=_creationflags(),
    )
    if r.returncode != 0:
        tail = "\n".join((r.stderr or "").strip().splitlines()[-10:])
        raise RuntimeError(f"speed-match ffmpeg failed (rc={r.returncode}): {tail}")

    out_dur = probe_duration(output_path)
    logger.info(
        f"speed_match done: out={out_dur:.2f}s (target {voice_dur:.2f}s, "
        f"delta {out_dur - voice_dur:+.3f}s)"
    )

    return {
        "input_video_dur": video_dur,
        "voice_dur": voice_dur,
        "factor": round(factor, 6),
        "stretched_dur": round(stretched_dur, 3),
        "overhang_s": round(overhang, 3),
        "output_dur": round(out_dur, 3),
        "delta_s": round(out_dur - voice_dur, 3),
        "clamped": clamped,
    }
