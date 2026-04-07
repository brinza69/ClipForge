"""
ClipForge — Transcription Service
Uses faster-whisper for GPU-accelerated speech-to-text with word-level timestamps.
"""

import logging
import asyncio
import multiprocessing as mp
import queue as py_queue
import traceback
from typing import Optional, Callable, Awaitable, Dict, Any
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

    Communication protocol:
      - progress_queue: {"kind":"progress","progress":float,"message":str}
      - result_queue: {"ok":bool,"result":dict} OR {"ok":False,"error":str,"traceback":str}
    """
    try:
        model = _get_model()
        segments_iter, info = model.transcribe(
            media_path,
            beam_size=5,
            word_timestamps=True,
            language=language,
            vad_filter=True,
            vad_parameters=dict(
                min_silence_duration_ms=500,
                speech_pad_ms=200,
            ),
        )
    except Exception as e:
        result_queue.put(
            {
                "ok": False,
                "error": f"Transcription failed to initialize: {e}",
                "traceback": traceback.format_exc(),
            }
        )
        return

    detected_language = getattr(info, "language", None) or "unknown"

    segments = []
    full_text_parts = []
    last_update = 0.0
    cancelled = False

    for segment in segments_iter:
        if cancel_event.is_set():
            cancelled = True
            break

        if duration and duration > 0:
            # 5% startup overhead, remaining 95% is segment progress
            p = 0.05 + (0.95 * (float(segment.end) / float(duration)))
            p = min(max(p, 0.03), 0.99)
            now = time.time()
            if now - last_update >= 1.0 or p >= 0.98:
                last_update = now
                msg = f"Transcribing... {segment.end:.1f}s / {duration:.1f}s ({int(p*100)}%)"
                try:
                    progress_queue.put({"kind": "progress", "progress": p, "message": msg})
                except Exception:
                    pass

        words = []
        if getattr(segment, "words", None):
            for w in segment.words:
                words.append(
                    {
                        "word": (w.word or "").strip(),
                        "start": round(float(w.start), 3),
                        "end": round(float(w.end), 3),
                        "probability": round(float(w.probability), 3),
                    }
                )

        seg_data = {
            "start": round(float(segment.start), 3),
            "end": round(float(segment.end), 3),
            "text": (getattr(segment, "text", "") or "").strip(),
            "confidence": round(
                (sum(float(w.probability) for w in segment.words) / max(len(segment.words), 1))
                if segment.words
                else 0.5,
                3,
            ),
            "words": words,
        }
        segments.append(seg_data)
        full_text_parts.append(seg_data["text"])

    full_text = " ".join([p for p in full_text_parts if p]).strip()
    word_count = sum(len(s.get("words", []) or []) or len((s.get("text") or "").split()) for s in segments)

    result_queue.put(
        {
            "ok": True,
            "result": {
                "language": detected_language,
                "segments": segments,
                "full_text": full_text,
                "word_count": word_count,
                "cancelled": cancelled,
            },
        }
    )
