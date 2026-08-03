"""
ClipForge — AI Stream Clipper, Pass A (the cheap global scan).

This module runs over the WHOLE source, so everything here is chosen to stay
affordable on a 6-hour VOD:

  * audio comes from the 16 kHz mono wav, read in hop-sized blocks with the
    stdlib `wave` module — the file is never loaded into Python memory.
  * scene cuts come from one ffmpeg pass over the 480p proxy.
  * motion and faces sample the proxy at a fixed cadence; the full-resolution
    source is never opened here.

Nothing in Pass A may raise on bad input. A stream with silent audio, an
unreadable proxy or a 3-second duration must still produce a well-formed
(possibly empty) result so the pipeline degrades instead of dying — the later
passes all treat missing signals as "no evidence", never as an error.
"""

from __future__ import annotations

import logging
import math
import re
import wave
from pathlib import Path
from types import ModuleType
from typing import Any, Sequence

import numpy as np

from services.clipper import ANALYSIS_VERSION
from services.clipper.ffmpeg_tools import FFmpegError, ffmpeg_bin, run, video_info

logger = logging.getLogger("clipforge.clipper.signals")

# Loudness is normalised against the 95th percentile rather than the max: a
# single clipped shout must not flatten an entire stream to near-zero.
NORM_PERCENTILE = 95.0

PEAK_RATIO = 1.5          # a peak must beat the local median by this much
PEAK_PERCENTILE = 80.0    # ...and be loud in absolute terms for the whole file
PEAK_WINDOW_S = 4.0       # rolling-median window; wide enough to span a sentence

SILENCE_FLOOR_RATIO = 0.08
MIN_SILENCE_S = 0.35
SPEECH_MERGE_GAP_S = 0.25

MOTION_W, MOTION_H = 64, 36  # 16:9 thumbnail — enough for gross frame differencing

FACE_HOP_S = 2.0
MAX_FACE_SAMPLES = 2000      # caps Haar cost regardless of VOD length
MAX_MOTION_SAMPLES = 200_000  # ~27 h at the default hop; guards a broken decoder

_PTS_RE = re.compile(r"pts_time:([0-9]+\.?[0-9]*)")


# --------------------------------------------------------------------------
# pure helpers (unit-tested — no ffmpeg, no cv2, no disk)
# --------------------------------------------------------------------------

def _normalise(values: np.ndarray, *, percentile: float = NORM_PERCENTILE) -> list[float]:
    """Scale a series to 0..1 against its Nth percentile, clipping the tail."""
    arr = np.asarray(values, dtype=np.float64).ravel()
    if arr.size == 0:
        return []
    ref = float(np.percentile(arr, percentile))
    if ref <= 0:  # >= 95% of the file is digital silence — fall back to the max
        ref = float(arr.max())
    if ref <= 0:
        return [0.0] * int(arr.size)
    return np.round(np.clip(arr / ref, 0.0, 1.0), 4).tolist()


def _rolling_median(values: np.ndarray, window: int) -> np.ndarray:
    """Edge-padded rolling median, same length as the input."""
    arr = np.asarray(values, dtype=np.float64).ravel()
    n = arr.size
    if n == 0:
        return arr
    window = max(1, int(window))
    if window % 2 == 0:
        window += 1
    if window <= 1 or n <= window:
        return np.full(n, float(np.median(arr)))
    pad = window // 2
    view = np.lib.stride_tricks.sliding_window_view(np.pad(arr, pad, mode="edge"), window)
    return np.median(view, axis=-1)


def _bool_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Contiguous True runs as (start, end_exclusive) index pairs."""
    flags = np.asarray(mask, dtype=bool).ravel()
    if flags.size == 0:
        return []
    edges = np.diff(np.concatenate(([False], flags, [False])).astype(np.int8))
    starts = np.flatnonzero(edges == 1).tolist()
    ends = np.flatnonzero(edges == -1).tolist()
    return list(zip(starts, ends))


def _pick_peaks(
    values: np.ndarray,
    hop_s: float,
    *,
    ratio: float = PEAK_RATIO,
    percentile: float = PEAK_PERCENTILE,
    window_s: float = PEAK_WINDOW_S,
) -> list[float]:
    """Local maxima that are both locally and globally loud.

    Two tests, because either alone is wrong: the rolling-median ratio finds
    onsets inside a quiet passage that nobody would call a moment, and a bare
    percentile cut fires all over a sustained loud stretch.
    """
    arr = np.asarray(values, dtype=np.float64).ravel()
    if arr.size < 3 or hop_s <= 0:
        return []
    local = _rolling_median(arr, int(round(window_s / hop_s)))
    floor = float(np.percentile(arr, percentile))

    mid, left, right = arr[1:-1], arr[:-2], arr[2:]
    # `> left` and `>= right` picks exactly the first index of a flat plateau
    # instead of every sample in it.
    is_peak = (mid > left) & (mid >= right) & (mid > floor) & (mid >= local[1:-1] * ratio)
    return [round((int(i) + 1 + 0.5) * hop_s, 3) for i in np.flatnonzero(is_peak)]


def _silence_runs(
    values: np.ndarray,
    hop_s: float,
    *,
    duration: float | None = None,
    floor_ratio: float = SILENCE_FLOOR_RATIO,
    min_len_s: float = MIN_SILENCE_S,
) -> list[list[float]]:
    """Spans quieter than `floor_ratio` of the median level, in seconds."""
    arr = np.asarray(values, dtype=np.float64).ravel()
    if arr.size == 0 or hop_s <= 0:
        return []
    total = float(duration) if duration and duration > 0 else arr.size * hop_s

    ref = float(np.median(arr))
    if ref <= 0:  # more than half the file is digital silence
        ref = float(np.percentile(arr, 75))
    if ref <= 0:
        ref = float(arr.max())
    if ref <= 0:
        return [[0.0, round(total, 3)]]

    min_hops = max(1, int(math.ceil(min_len_s / hop_s - 1e-9)))
    spans: list[list[float]] = []
    for start, end in _bool_runs(arr < ref * floor_ratio):
        if end - start < min_hops:
            continue
        spans.append([round(start * hop_s, 3), round(min(end * hop_s, total), 3)])
    return [s for s in spans if s[1] > s[0]]


def _complement(spans: Sequence[Sequence[float]], duration: float) -> list[list[float]]:
    """Everything in [0, duration] that `spans` (sorted, disjoint) does not cover."""
    if duration <= 0:
        return []
    out: list[list[float]] = []
    cursor = 0.0
    for span in spans:
        start, end = float(span[0]), float(span[1])
        if start > cursor:
            out.append([round(cursor, 3), round(min(start, duration), 3)])
        cursor = max(cursor, end)
        if cursor >= duration:
            break
    if cursor < duration:
        out.append([round(cursor, 3), round(duration, 3)])
    return [s for s in out if s[1] > s[0]]


def _merge_spans(spans: Sequence[Sequence[float]], *, gap_s: float) -> list[list[float]]:
    """Join spans separated by less than `gap_s`.

    With the default constants this is a no-op (every silence is already
    >= MIN_SILENCE_S, which exceeds the gap). It exists so lowering
    MIN_SILENCE_S doesn't start shredding speech into one-hop fragments.
    """
    ordered = sorted(([float(s[0]), float(s[1])] for s in spans), key=lambda s: s[0])
    merged: list[list[float]] = []
    for span in ordered:
        if merged and span[0] - merged[-1][1] < gap_s:
            merged[-1][1] = max(merged[-1][1], span[1])
        else:
            merged.append([span[0], span[1]])
    return [[round(a, 3), round(b, 3)] for a, b in merged if b > a]


# --------------------------------------------------------------------------
# Pass A stages
# --------------------------------------------------------------------------

def _empty_audio(hop_s: float) -> dict[str, Any]:
    return {"hop_s": hop_s, "duration": 0.0, "rms": [], "peaks": [], "silence": [], "speech": []}


def audio_timeline(wav_path: str, *, hop_s: float = 0.25) -> dict[str, Any]:
    """Per-hop loudness, peaks, silence and speech spans for a 16 kHz mono wav.

    Reads the file one hop at a time: a 6-hour VOD is ~700 MB of PCM and must
    never be materialised in Python.
    """
    if hop_s <= 0:
        logger.warning("audio_timeline: hop_s must be > 0, got %s — using 0.25", hop_s)
        hop_s = 0.25
    if not wav_path or not Path(wav_path).exists():
        logger.warning("audio_timeline: missing wav %s", wav_path)
        return _empty_audio(hop_s)

    levels: list[float] = []
    frames_read = 0
    try:
        with wave.open(str(wav_path), "rb") as wf:
            channels = max(1, wf.getnchannels())
            width = wf.getsampwidth()
            rate = wf.getframerate()
            if rate <= 0:
                logger.warning("audio_timeline: %s reports no sample rate", wav_path)
                return _empty_audio(hop_s)
            if width != 2:
                # ingest always writes pcm_s16le; anything else is a bug upstream
                # and not worth a second decode path here.
                logger.warning("audio_timeline: %s is %d-bit, expected 16-bit PCM", wav_path, width * 8)
                return _empty_audio(hop_s)

            block = max(1, int(round(hop_s * rate)))
            frame_bytes = channels * width
            while True:
                raw = wf.readframes(block)
                if not raw:
                    break
                usable = len(raw) - (len(raw) % frame_bytes)  # a truncated file can end mid-frame
                if usable <= 0:
                    break
                samples = np.frombuffer(raw, dtype="<i2", count=usable // width)
                frames_read += usable // frame_bytes
                if channels > 1:
                    samples = samples.reshape(-1, channels).mean(axis=1)
                scaled = samples.astype(np.float32) / 32768.0
                levels.append(float(np.sqrt(np.mean(np.square(scaled)))))
    except (wave.Error, OSError, ValueError, EOFError) as exc:
        logger.warning("audio_timeline: cannot read %s (%s)", wav_path, exc)
        return _empty_audio(hop_s)

    if not levels:
        logger.warning("audio_timeline: %s contains no audio frames", wav_path)
        return _empty_audio(hop_s)

    arr = np.asarray(levels, dtype=np.float64)
    # Duration from what was actually read, not the header: a wav whose length
    # field was never patched would otherwise report hours of nothing.
    duration = round(frames_read / float(rate), 3)
    silence = _silence_runs(arr, hop_s, duration=duration)
    speech = _merge_spans(_complement(silence, duration), gap_s=SPEECH_MERGE_GAP_S)
    return {
        "hop_s": hop_s,
        "duration": duration,
        "rms": _normalise(arr),
        "peaks": _pick_peaks(arr, hop_s),
        "silence": silence,
        "speech": speech,
    }


def scene_timeline(proxy_path: str, *, threshold: float = 0.30) -> list[float]:
    """Scene-change timestamps (seconds) from one ffmpeg pass over the proxy."""
    if not proxy_path or not Path(proxy_path).exists():
        logger.warning("scene_timeline: missing proxy %s", proxy_path)
        return []
    cmd = [
        ffmpeg_bin(), "-hide_banner", "-nostdin",
        "-i", str(proxy_path),
        "-an",
        "-vf", f"select='gt(scene,{threshold:.4f})',metadata=print:file=-",
        "-f", "null", "-",
    ]
    try:
        # The whole proxy still has to be decoded, so the default 600 s ceiling
        # is too tight for a multi-hour VOD.
        out = run(cmd, timeout=3600, what="scene detect")
    except FFmpegError as exc:
        logger.warning("scene_timeline: %s", exc)
        return []

    times = sorted({round(float(m), 3) for m in _PTS_RE.findall(out or "")})
    return [t for t in times if t > 0]


def _cv2() -> ModuleType | None:
    try:
        import cv2  # noqa: PLC0415 — kept local so audio-only callers don't pay for it
    except ImportError:  # pragma: no cover - opencv-python is a hard dependency
        logger.warning("opencv-python unavailable — motion and face signals will be empty")
        return None
    return cv2


def motion_timeline(proxy_path: str, *, hop_s: float = 0.5) -> dict[str, Any]:
    """Frame-differencing motion energy over the proxy, normalised 0..1."""
    if hop_s <= 0:
        logger.warning("motion_timeline: hop_s must be > 0, got %s — using 0.5", hop_s)
        hop_s = 0.5
    result: dict[str, Any] = {"hop_s": hop_s, "motion": []}
    cv2 = _cv2()
    if cv2 is None or not proxy_path or not Path(proxy_path).exists():
        if cv2 is not None:
            logger.warning("motion_timeline: missing proxy %s", proxy_path)
        return result

    cap = cv2.VideoCapture(str(proxy_path))
    try:
        if not cap.isOpened():
            logger.warning("motion_timeline: cannot open %s", proxy_path)
            return result
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        count = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
        limit = (count / fps) if fps > 0 and count > 0 else 0.0

        diffs: list[float] = []
        previous = None
        t = 0.0
        while len(diffs) < MAX_MOTION_SAMPLES:
            if limit and t > limit:
                break
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            small = cv2.resize(frame, (MOTION_W, MOTION_H), interpolation=cv2.INTER_AREA)
            grey = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
            # The first sample has nothing to compare against; 0 keeps the
            # series index-aligned with t = i * hop_s.
            diffs.append(0.0 if previous is None else float(np.mean(np.abs(grey - previous))) / 255.0)
            previous = grey
            t += hop_s
    except cv2.error as exc:
        logger.warning("motion_timeline: decode failed for %s (%s)", proxy_path, exc)
        return result
    finally:
        cap.release()

    result["motion"] = _normalise(np.asarray(diffs, dtype=np.float64)) if diffs else []
    return result


def face_presence(proxy_path: str, times: list[float]) -> list[dict[str, Any]]:
    """Face boxes at each requested timestamp, in PROXY pixel coordinates.

    Always returns one entry per requested time (empty `boxes` when the frame
    is unreadable) so callers can zip it against their own sample grid.
    """
    stamps = [float(t) for t in (times or []) if float(t) >= 0]
    blank = [{"t": round(t, 3), "boxes": []} for t in stamps]
    cv2 = _cv2()
    if cv2 is None or not stamps:
        return blank
    if not proxy_path or not Path(proxy_path).exists():
        logger.warning("face_presence: missing proxy %s", proxy_path)
        return blank

    cascade_file = str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
    cascade = cv2.CascadeClassifier(cascade_file)
    if cascade.empty():
        logger.warning("face_presence: could not load cascade %s", cascade_file)
        return blank

    out: list[dict[str, Any]] = []
    cap = cv2.VideoCapture(str(proxy_path))
    try:
        if not cap.isOpened():
            logger.warning("face_presence: cannot open %s", proxy_path)
            return blank
        for t in stamps:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                out.append({"t": round(t, 3), "boxes": []})
                continue
            grey = cv2.equalizeHist(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
            found = cascade.detectMultiScale(grey, 1.15, 5, minSize=(24, 24))
            boxes = [[int(x), int(y), int(w), int(h)] for x, y, w, h in found]
            out.append({"t": round(t, 3), "boxes": boxes})
    except cv2.error as exc:
        logger.warning("face_presence: detection failed for %s (%s)", proxy_path, exc)
        return blank
    finally:
        cap.release()
    return out


def _face_sample_times(duration: float) -> list[float]:
    """Sample grid for face detection, stretched so long VODs stay bounded."""
    if duration <= 0:
        return []
    hop = max(FACE_HOP_S, duration / MAX_FACE_SAMPLES)
    n = max(1, int(duration // hop))
    return [round(i * hop + hop / 2.0, 3) for i in range(n)]


def build_signals(
    project_id: str,
    proxy_path: str,
    wav_path: str,
    duration: float,
) -> dict[str, Any]:
    """Run all of Pass A, persist analysis/signals.json, return the signals."""
    audio = audio_timeline(wav_path)
    scenes = scene_timeline(proxy_path)
    motion = motion_timeline(proxy_path)

    width = height = 0
    probe_duration = 0.0
    try:
        info = video_info(proxy_path)
        width, height = int(info["width"]), int(info["height"])
        probe_duration = float(info["duration"])
    except (FFmpegError, KeyError, TypeError, ValueError) as exc:
        logger.warning("build_signals: cannot probe proxy %s (%s)", proxy_path, exc)

    total = float(duration or 0.0)
    if total <= 0:
        total = probe_duration or float(audio.get("duration") or 0.0)

    faces = face_presence(proxy_path, _face_sample_times(total))

    signals: dict[str, Any] = {
        "version": ANALYSIS_VERSION,
        "duration": round(total, 3),
        "proxy_width": width,
        "proxy_height": height,
        "audio": audio,
        "scenes": scenes,
        "motion": motion,
        "faces": faces,
        # Shortcuts for the short lists every later pass reaches for. The big
        # per-hop arrays (rms, motion) stay nested so signals.json carries them
        # once rather than twice.
        "peaks": audio["peaks"],
        "silence": audio["silence"],
        "speech": audio["speech"],
    }

    # Imported here so the analysis functions above stay usable (and testable)
    # without the storage layer or its settings.
    try:
        from services.clipper import storage

        storage.write_artifact(project_id, "signals", signals)
        storage.write_artifact(project_id, "faces", faces)
    except Exception as exc:  # noqa: BLE001 — a failed write must not lose the pass
        logger.error("build_signals: could not persist signals for %s (%s)", project_id, exc)

    logger.info(
        "build_signals: %s — %.1fs, %d rms hops, %d peaks, %d scenes, %d motion samples, %d face samples",
        project_id, total, len(audio["rms"]), len(audio["peaks"]),
        len(scenes), len(motion["motion"]), len(faces),
    )
    return signals
