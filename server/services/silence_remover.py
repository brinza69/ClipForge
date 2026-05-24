"""
ClipForge — Silence Remover

Algorithm adapted from NeuralFalcon's HF Space (Remove-Silence-From-Audio):
  - pydub.silence.detect_nonsilent with:
      min_silence_len = 100 ms  (silence run must last this long to count)
      silence_thresh  = -45 dBFS (anything quieter is silence)
      keep_silence    = N ms    (padding kept around each non-silent chunk)
  - If nothing is detected (whole file quieter than -45 dBFS) we retry with
    a dynamic threshold of `audio.dBFS - 16`, matching the upstream fallback.

Two output modes:

  - audio: input is .mp3/.wav/.m4a/etc. We concat the non-silent chunks with
    pydub (matches the HF space exactly) and export to the requested format.

  - video: input is a video. We detect on the extracted audio track, then
    invoke ffmpeg with a `select=between(t,..) + ...` filter to keep only
    the corresponding video + audio time ranges. Audio stays in sync with
    video because the same time map drives both filters.

Both paths return a stats dict: { before_ms, after_ms, removed_ms, removed_pct, segments }
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger("clipforge.silence_remover")

# Defaults match the HF space exactly so behavior is identical out of the box.
DEFAULT_MIN_SILENCE_MS = 100
DEFAULT_SILENCE_THRESH_DB = -45.0
DEFAULT_KEEP_SILENCE_MS = 50  # 0.05s — same as the HF space default

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi"}


def _ffmpeg_bin() -> str:
    try:
        from config import settings
        loc = settings.ffmpeg_location
        if loc:
            exe = Path(loc) / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
            if exe.exists():
                return str(exe)
    except Exception:
        pass
    return shutil.which("ffmpeg") or "ffmpeg"


def _creationflags() -> int:
    return 0x08000000 if os.name == "nt" else 0


def detect_mode(path: str) -> str:
    """Return 'audio' or 'video' from the file extension."""
    ext = Path(path).suffix.lower()
    if ext in VIDEO_EXTS:
        return "video"
    return "audio"


# ── Core silence detection ───────────────────────────────────────────────────


def detect_nonsilent_segments(
    audio_source,  # pydub.AudioSegment
    min_silence_ms: int = DEFAULT_MIN_SILENCE_MS,
    silence_thresh_db: float = DEFAULT_SILENCE_THRESH_DB,
    keep_silence_ms: int = DEFAULT_KEEP_SILENCE_MS,
) -> List[Tuple[int, int]]:
    """
    Returns [(start_ms, end_ms), …] of non-silent chunks, padded by
    keep_silence_ms on each side and merged where padding causes overlap.
    Empty list means nothing detected even with the fallback threshold.
    """
    from pydub.silence import detect_nonsilent

    total_ms = len(audio_source)
    nonsilent = detect_nonsilent(
        audio_source,
        min_silence_len=min_silence_ms,
        silence_thresh=silence_thresh_db,
    )
    if not nonsilent:
        # Same fallback the HF space uses when threshold is too aggressive.
        dynamic = audio_source.dBFS - 16
        logger.info(
            f"No non-silent chunks at {silence_thresh_db} dBFS; retrying at "
            f"{dynamic:.1f} dBFS (audio is overall quiet, dBFS={audio_source.dBFS:.1f})."
        )
        nonsilent = detect_nonsilent(
            audio_source,
            min_silence_len=min_silence_ms,
            silence_thresh=dynamic,
        )
    if not nonsilent:
        return []

    # Apply keep_silence padding on each side, then merge overlaps. Without
    # merging, ffmpeg's select filter would still work but the audio path
    # would double-include the padded overlap.
    padded: List[Tuple[int, int]] = []
    for start, end in nonsilent:
        padded.append((
            max(0, start - keep_silence_ms),
            min(total_ms, end + keep_silence_ms),
        ))
    merged: List[Tuple[int, int]] = []
    for s, e in padded:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


# ── Audio mode (pydub concat — matches HF space exactly) ────────────────────


def _remove_silence_audio(
    input_path: str,
    output_path: str,
    min_silence_ms: int,
    silence_thresh_db: float,
    keep_silence_ms: int,
    on_progress: Optional[Callable[[float, str], None]] = None,
) -> dict:
    from pydub import AudioSegment

    if on_progress:
        on_progress(0.05, "Loading audio…")
    sound = AudioSegment.from_file(input_path)
    before_ms = len(sound)

    if on_progress:
        on_progress(0.20, "Detecting silence…")
    segments = detect_nonsilent_segments(
        sound, min_silence_ms, silence_thresh_db, keep_silence_ms,
    )

    if not segments:
        logger.warning("No non-silent content detected; copying input unchanged.")
        # Fall back to original to avoid producing an empty file.
        sound.export(output_path, format=Path(output_path).suffix.lstrip(".").lower() or "wav")
        after_ms = before_ms
        return _stats(before_ms, after_ms, segments)

    if on_progress:
        on_progress(0.50, f"Joining {len(segments)} segment(s)…")

    combined = AudioSegment.empty()
    for s, e in segments:
        combined += sound[s:e]
    after_ms = len(combined)

    if on_progress:
        on_progress(0.85, "Encoding output…")

    out_ext = Path(output_path).suffix.lstrip(".").lower() or "wav"
    combined.export(output_path, format=out_ext)

    if on_progress:
        on_progress(1.0, "Done")

    return _stats(before_ms, after_ms, segments)


# ── Video mode (ffmpeg select filter, audio + video kept in sync) ───────────


def _ffprobe_duration_ms(path: str) -> int:
    """Return container duration in ms, or 0 on failure."""
    ffmpeg = _ffmpeg_bin()
    ffprobe = ffmpeg.replace("ffmpeg", "ffprobe")
    if not Path(ffprobe).exists() and ffprobe != "ffprobe":
        ffprobe = shutil.which("ffprobe") or "ffprobe"
    try:
        r = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=15,
            creationflags=_creationflags(),
        )
        return int(float(r.stdout.strip()) * 1000)
    except Exception:
        return 0


def _extract_audio_for_detection(video_path: str, temp_wav: str) -> None:
    """Pull a mono 16k wav for silence detection. Matches the HF space's 16k mono prep."""
    ffmpeg = _ffmpeg_bin()
    r = subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-i", video_path,
         "-vn", "-ac", "1", "-ar", "16000", temp_wav],
        capture_output=True, text=True,
        creationflags=_creationflags(),
    )
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg audio-extract failed: {r.stderr[-500:]}")


def _build_select_expr(segments_sec: List[Tuple[float, float]]) -> str:
    """`between(t,s1,e1)+between(t,s2,e2)+...` — frame goes through if any matches."""
    return "+".join(f"between(t,{s:.6f},{e:.6f})" for s, e in segments_sec)


def _remove_silence_video(
    input_path: str,
    output_path: str,
    min_silence_ms: int,
    silence_thresh_db: float,
    keep_silence_ms: int,
    on_progress: Optional[Callable[[float, str], None]] = None,
) -> dict:
    from pydub import AudioSegment

    workdir = Path(output_path).parent
    workdir.mkdir(parents=True, exist_ok=True)
    temp_wav = str(workdir / f".{Path(output_path).stem}_detect.wav")

    if on_progress:
        on_progress(0.05, "Extracting audio…")
    _extract_audio_for_detection(input_path, temp_wav)

    if on_progress:
        on_progress(0.20, "Detecting silence…")
    sound = AudioSegment.from_file(temp_wav)
    before_ms = _ffprobe_duration_ms(input_path) or len(sound)

    segments_ms = detect_nonsilent_segments(
        sound, min_silence_ms, silence_thresh_db, keep_silence_ms,
    )

    try:
        Path(temp_wav).unlink(missing_ok=True)
    except Exception:
        pass

    if not segments_ms:
        logger.warning("No non-silent content detected; copying input unchanged.")
        shutil.copy(input_path, output_path)
        return _stats(before_ms, before_ms, segments_ms)

    segments_sec = [(s / 1000.0, e / 1000.0) for s, e in segments_ms]
    select_expr = _build_select_expr(segments_sec)

    if on_progress:
        on_progress(0.35, f"Cutting {len(segments_sec)} segment(s) with ffmpeg…")

    ffmpeg = _ffmpeg_bin()
    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-i", input_path,
        "-vf", f"select='{select_expr}',setpts=N/FRAME_RATE/TB",
        "-af", f"aselect='{select_expr}',asetpts=N/SR/TB",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        output_path,
    ]
    r = subprocess.run(
        cmd, capture_output=True, text=True, creationflags=_creationflags(),
    )
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg cut failed: {r.stderr[-800:]}")

    after_ms = _ffprobe_duration_ms(output_path) or sum(e - s for s, e in segments_ms)
    if on_progress:
        on_progress(1.0, "Done")
    return _stats(before_ms, after_ms, segments_ms)


# ── Public entrypoint ───────────────────────────────────────────────────────


def _stats(before_ms: int, after_ms: int, segments: List[Tuple[int, int]]) -> dict:
    removed_ms = max(0, before_ms - after_ms)
    pct = (removed_ms / before_ms * 100.0) if before_ms > 0 else 0.0
    return {
        "before_ms": int(before_ms),
        "after_ms": int(after_ms),
        "removed_ms": int(removed_ms),
        "removed_pct": round(pct, 2),
        "segments": len(segments),
    }


def remove_silence(
    input_path: str,
    output_path: str,
    *,
    mode: str = "auto",  # "auto" | "audio" | "video"
    min_silence_ms: int = DEFAULT_MIN_SILENCE_MS,
    silence_thresh_db: float = DEFAULT_SILENCE_THRESH_DB,
    keep_silence_ms: int = DEFAULT_KEEP_SILENCE_MS,
    on_progress: Optional[Callable[[float, str], None]] = None,
) -> dict:
    """
    Strip silence from an audio or video file. Returns stats dict.

    `mode='auto'` infers from extension. `keep_silence_ms` is the padding
    retained on each side of every non-silent chunk (matches HF param).
    """
    if not Path(input_path).exists():
        raise FileNotFoundError(input_path)

    if mode == "auto":
        mode = detect_mode(input_path)
    if mode not in ("audio", "video"):
        raise ValueError(f"mode must be 'audio' or 'video', got {mode!r}")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    runner = _remove_silence_audio if mode == "audio" else _remove_silence_video
    return runner(
        input_path, output_path,
        min_silence_ms, silence_thresh_db, keep_silence_ms,
        on_progress=on_progress,
    )
