"""
Local text-to-speech service using Coqui XTTS-v2.

XTTS-v2 is a voice-cloning TTS model that runs on a single GPU. Give it a
6-30 second reference audio clip + some text + a language code and it
produces speech in that voice.

Voice library lives in `data/voices/*.wav`. Drop any clean, mono, 22-48
kHz WAV/MP3 reference clip there to make it available in the UI.

The model is loaded lazily on first request (~3-5s warm-up after first
download). The model itself is ~2GB and downloads to your TTS cache on
first construction.

Coqui TTS is an optional dependency — the server boots without it. If
`pip install TTS` hasn't been run, synthesise calls will raise a clear
RuntimeError that surfaces in the UI.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import List, Optional

import numpy as np

logger = logging.getLogger("clipforge.tts")

# XTTS-v2 supported language codes (17 langs)
SUPPORTED_LANGS = [
    "en", "es", "fr", "de", "it", "pt", "pl", "tr", "ru", "nl",
    "cs", "ar", "zh-cn", "ja", "hu", "ko", "hi",
]

# Some sensible XTTS defaults — these match the engine's own clamps.
DEFAULT_TEMPERATURE = 0.7   # 0.1-1.0 — higher = more expressive variance
DEFAULT_SPEED = 1.0         # 0.5-2.0
DEFAULT_LANG = "en"

_tts_lock = threading.Lock()
_tts_model = None
_tts_load_error: Optional[str] = None


def _get_tts():
    """Lazy-load the XTTS-v2 model on first use."""
    global _tts_model, _tts_load_error
    if _tts_model is not None:
        return _tts_model
    with _tts_lock:
        if _tts_model is not None:
            return _tts_model
        try:
            from TTS.api import TTS  # type: ignore
        except ImportError as e:
            _tts_load_error = (
                "Coqui TTS is not installed. Run "
                "`pip install TTS` in the server venv, then retry. "
                "First run downloads the XTTS-v2 model (~2GB)."
            )
            raise RuntimeError(_tts_load_error) from e

        try:
            import torch
            gpu = torch.cuda.is_available()
        except Exception:
            gpu = False

        logger.info(f"Loading XTTS-v2 (gpu={gpu})…")
        t0 = time.time()
        try:
            # Setting COQUI_TOS_AGREED bypasses the interactive license prompt
            os.environ.setdefault("COQUI_TOS_AGREED", "1")
            _tts_model = TTS("tts_models/multilingual/multi-dataset/xtts_v2", gpu=gpu)
        except Exception as e:
            _tts_load_error = f"Failed to load XTTS-v2: {e}"
            logger.exception("XTTS-v2 load failed")
            raise RuntimeError(_tts_load_error) from e

        logger.info(f"XTTS-v2 loaded in {time.time() - t0:.1f}s")
        return _tts_model


def voices_dir() -> Path:
    from config import settings
    d = Path(settings.data_dir) / "voices"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_voices() -> List[dict]:
    """Discover reference clips in data/voices/."""
    out: List[dict] = []
    for p in sorted(voices_dir().iterdir()):
        if not p.is_file():
            continue
        if p.suffix.lower() not in (".wav", ".mp3", ".flac", ".m4a", ".ogg"):
            continue
        # Pretty name = filename without extension, underscores→spaces
        name = p.stem.replace("_", " ").strip()
        out.append({
            "id": p.name,
            "name": name,
            "path": str(p),
            "size_kb": p.stat().st_size // 1024,
        })
    return out


def get_voice_path(voice_id: str) -> Optional[Path]:
    p = voices_dir() / voice_id
    if not p.exists() or not p.is_file():
        return None
    return p


def synthesize(
    text: str,
    voice_id: str,
    language: str = DEFAULT_LANG,
    *,
    speed: float = DEFAULT_SPEED,
    temperature: float = DEFAULT_TEMPERATURE,
    output_path: Optional[str] = None,
) -> str:
    """
    Synthesise speech, returning the path to the produced WAV file.

    For long input (>{XTTS_CHUNK_MAX_CHARS} chars) the text is split on
    sentence boundaries into chunks, each chunk is synthesised separately
    with the same voice / lang / speed / temp, and the resulting WAVs are
    concatenated. There is no longer a hard per-request character limit —
    only a safety ceiling at {XTTS_HARD_MAX_CHARS} chars (~50 min of audio)
    to catch accidental paste-the-whole-book bugs.

    Args:
      text: the script to read (any length up to the safety ceiling)
      voice_id: filename in data/voices/ (e.g. 'roger.wav')
      language: ISO code; see SUPPORTED_LANGS
      speed: 0.5-2.0
      temperature: 0.1-1.0 — sampling randomness
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("text is required")
    if len(text) > XTTS_HARD_MAX_CHARS:
        raise ValueError(
            f"text too long ({len(text)} chars; safety ceiling {XTTS_HARD_MAX_CHARS})"
        )
    if language not in SUPPORTED_LANGS:
        raise ValueError(f"unsupported language: {language}. Use one of {SUPPORTED_LANGS}")

    voice_path = get_voice_path(voice_id)
    if voice_path is None:
        raise ValueError(
            f"voice '{voice_id}' not found in {voices_dir()}. "
            f"Available: {[v['id'] for v in list_voices()]}"
        )

    speed = max(0.5, min(2.0, float(speed)))
    temperature = max(0.1, min(1.0, float(temperature)))

    if not output_path:
        from config import settings
        out_dir = Path(settings.data_dir) / "tts_out"
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(out_dir / f"tts_{int(time.time() * 1000)}.wav")

    tts = _get_tts()

    # Single-shot for inputs that already fit XTTS's internal chunker
    # comfortably. Avoids the overhead of splitting + concatenating WAVs.
    if len(text) <= XTTS_CHUNK_MAX_CHARS:
        logger.info(
            f"TTS synth: chars={len(text)} voice={voice_id} lang={language} "
            f"speed={speed} temp={temperature}"
        )
        t0 = time.time()
        tts.tts_to_file(
            text=text,
            file_path=output_path,
            speaker_wav=str(voice_path),
            language=language,
            speed=speed,
            temperature=temperature,
        )
        logger.info(f"TTS done in {time.time() - t0:.1f}s → {output_path}")
        return output_path

    # Long input: chunk on sentence boundaries, synth each, concat WAVs.
    chunks = _split_into_tts_chunks(text, XTTS_CHUNK_MAX_CHARS)
    logger.info(
        f"TTS synth: chars={len(text)} → {len(chunks)} chunks, "
        f"voice={voice_id} lang={language} speed={speed} temp={temperature}"
    )
    t0 = time.time()
    from config import settings
    tmp_dir = Path(settings.data_dir) / "tts_out" / f".chunks_{int(time.time() * 1000)}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    chunk_paths: list[str] = []
    try:
        for i, chunk in enumerate(chunks):
            cp = str(tmp_dir / f"chunk_{i:03d}.wav")
            tts.tts_to_file(
                text=chunk,
                file_path=cp,
                speaker_wav=str(voice_path),
                language=language,
                speed=speed,
                temperature=temperature,
            )
            chunk_paths.append(cp)
            logger.info(f"  chunk {i+1}/{len(chunks)} done ({len(chunk)} chars → {cp})")
        _concat_wavs(chunk_paths, output_path)
    finally:
        # Drop the per-chunk wav scratch dir.
        try:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass
    logger.info(f"TTS done in {time.time() - t0:.1f}s ({len(chunks)} chunks) → {output_path}")
    return output_path


# ── Long-input chunking helpers ───────────────────────────────────────────

# How big each chunk can be before we split it. XTTS' internal sentence
# tokenizer works comfortably below ~1500 chars per pass, so we aim for
# 1200 with headroom for borderline-long sentences.
XTTS_CHUNK_MAX_CHARS = 1200

# Safety ceiling — anything past this is almost certainly an accidental
# paste. ~50 min of audio at typical TTS speed.
XTTS_HARD_MAX_CHARS = 80000


def _split_into_tts_chunks(text: str, max_chars: int) -> list[str]:
    """Pack sentences into chunks, each <= max_chars. Splits on .!?… so the
    chunk boundaries land on natural pauses; a single oversized sentence
    is hard-wrapped as a last resort."""
    import re
    # Split on sentence-ending punctuation followed by whitespace, keeping
    # the punctuation attached to the preceding sentence.
    sentences = re.split(r"(?<=[\.\!\?…])\s+", text.strip())
    out: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        # If a single sentence is bigger than the cap, hard-wrap it.
        if len(s) > max_chars:
            if buf:
                out.append(" ".join(buf))
                buf, buf_len = [], 0
            for i in range(0, len(s), max_chars):
                out.append(s[i : i + max_chars])
            continue
        if buf_len + len(s) + 1 > max_chars and buf:
            out.append(" ".join(buf))
            buf, buf_len = [s], len(s)
        else:
            buf.append(s)
            buf_len += len(s) + 1
    if buf:
        out.append(" ".join(buf))
    return out


def _concat_wavs(paths: list[str], output_path: str) -> None:
    """Stream-concat multiple WAVs of the same format using stdlib `wave`."""
    import wave
    if not paths:
        raise RuntimeError("no chunks to concatenate")
    with wave.open(paths[0], "rb") as w0:
        params = w0.getparams()
        frames = [w0.readframes(w0.getnframes())]
    for p in paths[1:]:
        with wave.open(p, "rb") as wn:
            if wn.getparams()[:3] != params[:3]:
                # Should never happen — same voice/model = same format —
                # but bail loudly if it does so we don't write silent garbage.
                raise RuntimeError(
                    f"chunk {p} has mismatched format {wn.getparams()} vs {params}"
                )
            frames.append(wn.readframes(wn.getnframes()))
    with wave.open(output_path, "wb") as wout:
        wout.setparams(params)
        wout.writeframes(b"".join(frames))


def is_available() -> tuple[bool, Optional[str]]:
    """Cheap probe — does NOT load the model. Returns (installed, error_hint)."""
    try:
        import TTS  # noqa
        return True, None
    except ImportError:
        return False, "Coqui TTS not installed. Run: pip install TTS"
