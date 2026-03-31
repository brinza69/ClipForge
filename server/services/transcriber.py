"""
ClipForge — Transcription Service
Uses faster-whisper for GPU-accelerated speech-to-text with word-level timestamps.
"""

import logging
import asyncio
from typing import Optional, Callable, Awaitable, List, Dict, Any
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

        _model = WhisperModel(
            settings.whisper_model,
            device=device,
            compute_type=compute_type,
        )
        logger.info("Whisper model loaded successfully")

    return _model


async def transcribe(
    media_path: str,
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
    logger.info(f"Starting transcription: {media_path}")

    if on_progress:
        await on_progress(0.05, "Loading transcription model...")

    # Run in thread pool to avoid blocking the event loop
    result = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: _transcribe_sync(media_path, language),
    )

    if on_progress:
        await on_progress(1.0, "Transcription complete")

    logger.info(f"Transcription complete: {result['word_count']} words, {len(result['segments'])} segments")
    return result


def _transcribe_sync(media_path: str, language: Optional[str] = None) -> Dict[str, Any]:
    """Synchronous transcription worker."""
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

    detected_language = info.language
    logger.info(f"Detected language: {detected_language} (probability: {info.language_probability:.2f})")

    segments = []
    full_text_parts = []

    for segment in segments_iter:
        words = []
        if segment.words:
            for w in segment.words:
                words.append({
                    "word": w.word.strip(),
                    "start": round(w.start, 3),
                    "end": round(w.end, 3),
                    "probability": round(w.probability, 3),
                })

        seg_data = {
            "start": round(segment.start, 3),
            "end": round(segment.end, 3),
            "text": segment.text.strip(),
            "confidence": round(
                sum(w.probability for w in segment.words) / max(len(segment.words), 1)
                if segment.words else 0.5,
                3,
            ),
            "words": words,
        }
        segments.append(seg_data)
        full_text_parts.append(segment.text.strip())

    full_text = " ".join(full_text_parts)
    word_count = sum(len(s.get("words", [])) or len(s["text"].split()) for s in segments)

    return {
        "language": detected_language,
        "segments": segments,
        "full_text": full_text,
        "word_count": word_count,
    }
