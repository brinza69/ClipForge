"""
ClipForge — Transcription Service
Uses faster-whisper for GPU-accelerated speech-to-text with word-level timestamps.
"""

import logging
import asyncio
import multiprocessing as mp
import queue as py_queue
import os
import shutil
import subprocess
import tempfile
import traceback
from typing import Optional, Callable, Awaitable, Dict, Any, List, Tuple
import time
from pathlib import Path

from config import settings

logger = logging.getLogger("clipforge.transcriber")

# Lazy-load model to avoid startup cost
_model = None


def _get_model():
    """Lazy-load the faster-whisper model."""
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        device = settings.whisper_device
        if device == "auto":
            try:
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                device = "cpu"

        compute_type = settings.whisper_compute_type
        if device == "cpu":
            compute_type = "int8"  # float16 not supported on CPU

        logger.info(
            f"Loading Whisper model: {settings.whisper_model} "
            f"(device={device}, compute_type={compute_type})"
        )

        try:
            _model = WhisperModel(
                settings.whisper_model,
                device=device,
                compute_type=compute_type,
            )
        except Exception as e:
            if device == "cuda":
                logger.warning(f"Failed to load Whisper on CUDA: {e}. Falling back to CPU...")
                _model = WhisperModel(
                    settings.whisper_model,
                    device="cpu",
                    compute_type="int8",
                )
            else:
                raise
        logger.info("Whisper model loaded successfully")

    return _model


async def transcribe(
    media_path: str,
    duration: float = 0.0,
    is_cancelled: Callable[[], bool] = lambda: False,
    on_progress: Optional[Callable[[float, str], Awaitable[None]]] = None,
    language: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Transcribe audio/video file to text with word-level timestamps.

    Returns dict with:
        language: str
        segments: list of segment dicts
        full_text: str
        word_count: int
    """
    logger.info(f"Starting transcription (killable worker): {media_path}")

    if on_progress:
        await on_progress(0.03, "Starting transcription...")

    # We run faster-whisper in a separate process so we can hard-terminate it on:
    # - user cancellation
    # - timeouts / watchdog
    ctx = mp.get_context("spawn")
    cancel_event = ctx.Event()
    progress_queue: mp.Queue = ctx.Queue()
    result_queue: mp.Queue = ctx.Queue()

    worker = ctx.Process(
        target=_transcribe_worker,
        args=(media_path, duration, cancel_event, progress_queue, result_queue, language),
        daemon=True,
    )
    worker.start()

    start_time = time.time()
    last_progress_at = start_time
    last_progress_seen = 0.03

    # Heartbeat so the UI never looks "stuck" even if word iteration is slow.
    heartbeat_s = 10.0
    watchdog_stall_s = 300.0  # no progress updates for 5 minutes

    outcome = None

    try:
        while True:
            # External cancellation request from the job queue.
            if is_cancelled():
                cancel_event.set()

            # Drain progress queue
            while True:
                try:
                    evt = progress_queue.get_nowait()
                except py_queue.Empty:
                    break
                if not evt:
                    continue
                kind = evt.get("kind")
                if kind == "progress":
                    last_progress_at = time.time()
                    last_progress_seen = float(evt.get("progress", last_progress_seen))
                    if on_progress:
                        await on_progress(last_progress_seen, evt.get("message", "Transcribing..."))
                elif kind == "log":
                    logger.info(evt.get("message", "transcriber log"))

            # CRITICAL: Drain result_queue in the loop to prevent deadlock.
            # multiprocessing.Queue uses a pipe with limited buffer. If the result
            # dict is very large (long videos produce thousands of segments+words),
            # the worker's put() blocks until the supervisor reads from the queue.
            # If we only read after the worker exits, we deadlock.
            if outcome is None:
                try:
                    outcome = result_queue.get_nowait()
                except py_queue.Empty:
                    pass

            # Heartbeat
            now = time.time()
            if on_progress and (now - last_progress_at) >= heartbeat_s and worker.is_alive():
                # Send a heartbeat with the last known progress — don't cap or overwrite it.
                await on_progress(last_progress_seen, "Transcribing... still running (please wait)")
                last_progress_at = now

            # Watchdog: no progress for too long
            if worker.is_alive() and (time.time() - last_progress_at) >= watchdog_stall_s:
                logger.error(
                    f"Transcription watchdog stalled >{watchdog_stall_s}s. Terminating worker."
                )
                worker.terminate()
                worker.join(timeout=5)
                raise TimeoutError(f"Transcription stalled for {int(watchdog_stall_s)}s")

            # Completion: worker exited OR we already got the result
            if not worker.is_alive():
                # Drain any remaining result after worker exits
                if outcome is None:
                    try:
                        outcome = result_queue.get(timeout=5)
                    except py_queue.Empty:
                        pass
                break

            # If we got the result but worker is still alive (cleanup), wait briefly
            if outcome is not None:
                worker.join(timeout=5)
                break

            await asyncio.sleep(0.2)

        if outcome is None:
            exit_code = worker.exitcode
            raise RuntimeError(
                f"Transcription worker exited without returning a result "
                f"(exit code: {exit_code}). "
                "This usually means the worker ran out of memory or crashed "
                "loading the model. Try a smaller model or free up RAM."
            )

        if not outcome.get("ok"):
            err = outcome.get("error", "Unknown transcription error")
            tb = outcome.get("traceback")
            logger.error(f"Transcription worker error: {err}\n{tb or ''}")
            raise RuntimeError(f"Transcription failed: {err}")

        result = outcome.get("result") or {}

        if on_progress and not result.get("cancelled"):
            await on_progress(1.0, "Transcription complete")

        logger.info(
            "Transcription finished%s: %s words, %s segments",
            " (cancelled)" if result.get("cancelled") else "",
            result.get("word_count"),
            len(result.get("segments", []) or []),
        )
        return result

    except asyncio.CancelledError:
        # Kill worker process to avoid zombie CPU usage.
        logger.warning("Transcription coroutine cancelled; terminating worker process...")
        cancel_event.set()
        if worker.is_alive():
            worker.terminate()
            worker.join(timeout=10)
        raise


def _split_audio_to_chunks(
    media_path: str,
    chunk_duration_s: float,
    out_dir: Path,
) -> List[Path]:
    """
    Split a media file into fixed-duration mono 16kHz PCM wav chunks using
    ffmpeg's segment muxer. This bounds the memory faster-whisper needs to
    decode any single chunk (important for long videos on low-RAM hosts).

    Returns the list of chunk paths in order.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(out_dir / "chunk_%04d.wav")
    cmd = [
        "ffmpeg",
        "-y",
        "-nostdin",
        "-loglevel", "error",
        "-i", media_path,
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "pcm_s16le",
        "-f", "segment",
        "-segment_time", str(int(chunk_duration_s)),
        "-reset_timestamps", "1",
        pattern,
    ]
    creationflags = (
        subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    )
    subprocess.run(
        cmd,
        check=True,
        capture_output=True,
        creationflags=creationflags,
    )
    return sorted(out_dir.glob("chunk_*.wav"))


def _transcribe_one(
    model,
    audio_path: str,
    language: Optional[str],
) -> Tuple[Any, Any]:
    """Invoke faster-whisper once with the project's standard parameters."""
    return model.transcribe(
        audio_path,
        beam_size=5,
        word_timestamps=True,
        language=language,
        # VAD helps skip non-speech audio but must be tuned:
        # - 500ms min silence avoids over-segmenting fast speech
        # - 400ms speech pad preserves word beginnings/endings
        # - threshold 0.3 is permissive enough for accented speech
        vad_filter=True,
        vad_parameters=dict(
            min_silence_duration_ms=500,
            speech_pad_ms=400,
            threshold=0.3,
        ),
        # Compression ratio filter: reject hallucinated/looping segments
        compression_ratio_threshold=2.4,
        # Log probability threshold for confident detection
        log_prob_threshold=-1.0,
        # Avoid hallucinations on non-speech sections
        no_speech_threshold=0.6,
    )


def _transcribe_worker(
    media_path: str,
    duration: float,
    cancel_event,
    progress_queue,
    result_queue,
    language: Optional[str],
) -> None:
    """
    Killable sync transcription worker executed in a separate process.

    Strategy: when the input is longer than `whisper_chunk_min_duration_s`,
    pre-split the audio into fixed-length PCM wav chunks on disk and feed
    each chunk to faster-whisper independently. Peak RAM is bounded by the
    largest chunk (~10 min) regardless of total media length. Segment and
    word timestamps are re-offset to the global timeline before being
    returned, so downstream code sees a single unified transcript.

    Communication protocol:
      - progress_queue: {"kind":"progress","progress":float,"message":str}
      - result_queue: {"ok":bool,"result":dict} OR {"ok":False,"error":str,"traceback":str}
    """
    tmp_chunks_dir: Optional[Path] = None
    try:
        try:
            model = _get_model()
        except Exception as e:
            result_queue.put(
                {
                    "ok": False,
                    "error": f"Transcription failed to initialize: {e}",
                    "traceback": traceback.format_exc(),
                }
            )
            return

        # Decide chunking policy
        chunk_duration = float(settings.whisper_chunk_duration_s or 0.0)
        chunk_min = float(settings.whisper_chunk_min_duration_s or 0.0)
        use_chunking = (
            chunk_duration > 0
            and duration
            and duration > 0
            and duration >= chunk_min
        )

        chunk_jobs: List[Tuple[str, float]] = []  # (audio_path, global_offset_s)

        if use_chunking:
            try:
                tmp_chunks_dir = Path(
                    tempfile.mkdtemp(prefix="clipforge_wx_", dir=str(settings.temp_dir))
                )
            except Exception:
                tmp_chunks_dir = Path(tempfile.mkdtemp(prefix="clipforge_wx_"))

            try:
                progress_queue.put(
                    {
                        "kind": "progress",
                        "progress": 0.02,
                        "message": (
                            f"Splitting audio into ~{int(chunk_duration)}s chunks..."
                        ),
                    }
                )
            except Exception:
                pass

            try:
                chunk_paths = _split_audio_to_chunks(
                    media_path, chunk_duration, tmp_chunks_dir
                )
            except subprocess.CalledProcessError as e:
                stderr = (e.stderr or b"").decode("utf-8", errors="replace")
                result_queue.put(
                    {
                        "ok": False,
                        "error": f"Audio chunk split failed: {stderr or e}",
                        "traceback": traceback.format_exc(),
                    }
                )
                return

            if not chunk_paths:
                # Fallback: no chunks produced, run on the original file.
                chunk_jobs = [(media_path, 0.0)]
                use_chunking = False
            else:
                for i, p in enumerate(chunk_paths):
                    chunk_jobs.append((str(p), i * chunk_duration))
        else:
            chunk_jobs = [(media_path, 0.0)]

        segments: List[Dict[str, Any]] = []
        full_text_parts: List[str] = []
        last_update = 0.0
        cancelled = False
        detected_language: Optional[str] = None

        total_chunks = len(chunk_jobs)

        for chunk_index, (chunk_path, offset_s) in enumerate(chunk_jobs):
            if cancel_event.is_set():
                cancelled = True
                break

            try:
                segments_iter, info = _transcribe_one(model, chunk_path, language)
            except Exception as e:
                result_queue.put(
                    {
                        "ok": False,
                        "error": (
                            f"Transcription failed on chunk "
                            f"{chunk_index + 1}/{total_chunks}: {e}"
                        ),
                        "traceback": traceback.format_exc(),
                    }
                )
                return

            if detected_language is None:
                detected_language = getattr(info, "language", None) or "unknown"

            for segment in segments_iter:
                if cancel_event.is_set():
                    cancelled = True
                    break

                seg_start = float(segment.start) + offset_s
                seg_end = float(segment.end) + offset_s

                if duration and duration > 0:
                    # 5% startup overhead, remaining 95% is segment progress
                    p = 0.05 + (0.95 * (seg_end / float(duration)))
                    p = min(max(p, 0.03), 0.99)
                    now = time.time()
                    if now - last_update >= 1.0 or p >= 0.98:
                        last_update = now
                        msg = (
                            f"Transcribing... {seg_end:.1f}s / {duration:.1f}s "
                            f"({int(p * 100)}%)"
                            + (
                                f" [chunk {chunk_index + 1}/{total_chunks}]"
                                if total_chunks > 1
                                else ""
                            )
                        )
                        try:
                            progress_queue.put(
                                {"kind": "progress", "progress": p, "message": msg}
                            )
                        except Exception:
                            pass

                words = []
                if getattr(segment, "words", None):
                    for w in segment.words:
                        word_text = (w.word or "").strip()
                        if not word_text:
                            continue
                        words.append(
                            {
                                "word": word_text,
                                "start": round(float(w.start) + offset_s, 3),
                                "end": round(float(w.end) + offset_s, 3),
                                "probability": round(float(w.probability), 3),
                            }
                        )

                seg_data = {
                    "start": round(seg_start, 3),
                    "end": round(seg_end, 3),
                    "text": (getattr(segment, "text", "") or "").strip(),
                    "confidence": round(
                        (
                            sum(float(w.probability) for w in segment.words)
                            / max(len(segment.words), 1)
                        )
                        if segment.words
                        else 0.5,
                        3,
                    ),
                    "words": words,
                }
                segments.append(seg_data)
                full_text_parts.append(seg_data["text"])

            # Free chunk file as soon as we're done with it so disk usage
            # doesn't grow unboundedly on very long videos.
            if use_chunking:
                try:
                    os.remove(chunk_path)
                except OSError:
                    pass

            if cancelled:
                break

        full_text = " ".join([p for p in full_text_parts if p]).strip()
        # Count words: prefer word-level timestamps (more accurate);
        # fall back to text split.
        word_count = sum(
            len(s["words"]) if s.get("words") else len((s.get("text") or "").split())
            for s in segments
        )

        result_queue.put(
            {
                "ok": True,
                "result": {
                    "language": detected_language or "unknown",
                    "segments": segments,
                    "full_text": full_text,
                    "word_count": word_count,
                    "cancelled": cancelled,
                },
            }
        )
    finally:
        if tmp_chunks_dir is not None:
            shutil.rmtree(tmp_chunks_dir, ignore_errors=True)
