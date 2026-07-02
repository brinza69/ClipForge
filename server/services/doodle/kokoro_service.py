"""
ClipForge — Auto Story Doodle: local Kokoro TTS voiceover generation.

100% local, no paid TTS fallback. Synthesizes per-scene narration with the
Kokoro-82M model (via the `kokoro` package) and writes 24 kHz mono WAV files.
KPipeline instances are heavy (they load model weights) so they are created
lazily, once per language code, and cached at module scope.

Voices are English-only (US: lang_code "a", UK: lang_code "b"). Kokoro's
English G2P (misaki) can fall back to espeak-ng for out-of-dictionary words;
we point phonemizer at the `espeakng-loader` bundled DLL/data so no system
MSI install is required. If that setup fails for any reason, `is_available()`
reports it with an actionable message (install the espeak-ng MSI) rather than
silently degrading quality.

No network TTS calls anywhere. Failures raise RuntimeError with a clear,
actionable message — there is no fallback provider.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("clipforge.doodle.kokoro")

VOICES = [
    {"id": "am_michael", "label": "Michael (US male, warm)", "lang": "a"},
    {"id": "am_fenrir", "label": "Fenrir (US male, deep)", "lang": "a"},
    {"id": "bm_fable", "label": "Fable (UK male, cozy)", "lang": "b"},
    {"id": "bm_george", "label": "George (UK male, calm)", "lang": "b"},
]

_VOICE_LANG = {v["id"]: v["lang"] for v in VOICES}
_SAMPLE_RATE = 24000

# Lazily-created KPipeline singletons, one per lang_code ("a"=American, "b"=British).
_pipelines: dict[str, object] = {}
_espeak_ready = False
_espeak_error: Optional[str] = None
_device: Optional[str] = None


def _voice_lang_code(voice: str) -> str:
    lang = _VOICE_LANG.get(voice)
    if lang is None:
        # Fall back to Kokoro's own convention: first letter of the voice id
        # (e.g. "am_michael" -> "a", "bm_fable" -> "b").
        lang = voice[0] if voice else "a"
    return lang


def _configure_espeak() -> None:
    """Point phonemizer/misaki at the espeakng-loader bundled library.

    misaki's espeak backend only auto-detects the official MSI install path
    (`C:\\Program Files\\eSpeak NG\\libespeak-ng.dll` on Windows). We set the
    library explicitly from `espeakng_loader` so the bundled wheel works with
    no system-wide install. Safe to call more than once.
    """
    global _espeak_ready, _espeak_error
    if _espeak_ready or _espeak_error is not None:
        return
    try:
        import espeakng_loader
        from phonemizer.backend.espeak.wrapper import EspeakWrapper

        lib_path = espeakng_loader.get_library_path()
        data_path = espeakng_loader.get_data_path()
        if not EspeakWrapper._ESPEAK_LIBRARY:
            EspeakWrapper.set_library(lib_path)
        os.environ.setdefault("ESPEAK_DATA_PATH", data_path)
        os.environ.setdefault("PHONEMIZER_ESPEAK_LIBRARY", lib_path)
        os.environ.setdefault("PHONEMIZER_ESPEAK_PATH", lib_path)
        _espeak_ready = True
        logger.info(f"kokoro: espeak-ng configured from espeakng_loader ({lib_path})")
    except Exception as exc:  # pragma: no cover - environment dependent
        _espeak_error = str(exc)
        logger.warning(f"kokoro: espeakng_loader setup failed: {exc}")


def _pick_device() -> str:
    global _device
    if _device is not None:
        return _device
    try:
        import torch

        _device = "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        _device = "cpu"
    return _device


def is_available() -> tuple[bool, str]:
    """Best-effort check that Kokoro TTS can actually run. Returns (ok, reason).

    `reason` is empty when ok. This never raises — it's meant for a status
    banner in the UI (GET /api/doodle/voices).
    """
    try:
        import kokoro  # noqa: F401
    except Exception as exc:
        return False, (
            "Kokoro TTS is not installed in the backend venv. Run: "
            "server/.venv/Scripts/python.exe -m pip install kokoro soundfile "
            f"espeakng-loader (import failed: {exc})"
        )

    _configure_espeak()
    if _espeak_error is not None:
        return False, (
            "Kokoro TTS is installed but espeak-ng (used for phonemization of "
            "out-of-dictionary words) could not be configured automatically "
            "(espeakng-loader failed: " + _espeak_error + "). Install the "
            "espeak-ng MSI from https://github.com/espeak-ng/espeak-ng/releases "
            "and restart the backend."
        )

    return True, ""


def get_audio_duration(path: str | Path) -> float:
    """Real duration in seconds via ffprobe. Raises RuntimeError on failure."""
    ffprobe_bin = _ffprobe_bin()
    creationflags = 0x08000000 if os.name == "nt" else 0
    try:
        result = subprocess.run(
            [
                ffprobe_bin, "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True, text=True, timeout=60, creationflags=creationflags,
        )
    except Exception as exc:
        raise RuntimeError(f"ffprobe failed to run for {path}: {exc}") from exc

    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(
            f"ffprobe could not read duration for {path}: {result.stderr[-300:]}"
        )
    try:
        return float(result.stdout.strip())
    except ValueError as exc:
        raise RuntimeError(f"ffprobe returned a non-numeric duration for {path}") from exc


def _ffprobe_bin() -> str:
    try:
        from config import settings

        loc = settings.ffmpeg_location
        if loc:
            exe = Path(loc) / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
            if exe.exists():
                return str(exe)
    except Exception:
        pass
    return shutil.which("ffprobe") or "ffprobe"


def _get_pipeline(lang_code: str):
    """Lazy module-level KPipeline singleton per lang_code."""
    pipeline = _pipelines.get(lang_code)
    if pipeline is not None:
        return pipeline

    ok, reason = is_available()
    if not ok:
        raise RuntimeError(reason)

    from kokoro import KPipeline

    device = _pick_device()
    logger.info(f"kokoro: creating KPipeline(lang_code={lang_code!r}, device={device!r})")
    try:
        pipeline = KPipeline(lang_code=lang_code, device=device)
    except TypeError:
        # Older/newer kokoro builds may not accept `device`; fall back to CPU default.
        pipeline = KPipeline(lang_code=lang_code)
    except Exception as exc:
        raise RuntimeError(f"Failed to initialize Kokoro pipeline for lang '{lang_code}': {exc}") from exc

    _pipelines[lang_code] = pipeline
    return pipeline


def _synthesize_sync(text: str, voice: str, speed: float, output_path: str | Path) -> float:
    """Blocking synthesis of one scene. Runs on a worker thread via asyncio.to_thread."""
    import numpy as np
    import soundfile as sf

    text = (text or "").strip()
    if not text:
        raise RuntimeError("Cannot synthesize empty narration text")

    lang_code = _voice_lang_code(voice)
    pipeline = _get_pipeline(lang_code)

    chunks: list["np.ndarray"] = []
    try:
        generator = pipeline(text, voice=voice, speed=speed)
        for result in generator:
            audio = result.audio
            if audio is None:
                continue
            if hasattr(audio, "detach"):
                audio = audio.detach().cpu().numpy()
            chunks.append(audio)
    except Exception as exc:
        raise RuntimeError(f"Kokoro synthesis failed for voice '{voice}': {exc}") from exc

    if not chunks:
        raise RuntimeError(
            f"Kokoro produced no audio for voice '{voice}' (text: {text[:60]!r}...)"
        )

    full_audio = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + f".tmp{os.getpid()}")
    try:
        sf.write(str(tmp_path), full_audio, _SAMPLE_RATE, subtype="PCM_16", format="WAV")
        tmp_path.replace(out_path)
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError(f"Failed to write WAV to {out_path}: {exc}") from exc

    return get_audio_duration(out_path)


async def generate_scene_audio(
    text: str, voice: str, speed: float, output_path: str | Path
) -> float:
    """Synthesizes ONE scene to a 24kHz mono WAV. Returns the REAL duration (ffprobe)."""
    return await asyncio.to_thread(_synthesize_sync, text, voice, speed, output_path)


async def generate_all_scene_audio(
    scenes: list[dict],
    voice: str,
    speed: float,
    audio_dir: str | Path,
    progress_cb: Optional[Callable[[float, str], "asyncio.Future | None"]] = None,
) -> list[dict]:
    """Synthesizes every scene's narration. Mutates and returns `scenes` with
    `audio_path` (relative, e.g. "audio/scene_000.wav") and `audio_duration`
    (real seconds) set on each scene dict.
    """
    audio_dir = Path(audio_dir)
    audio_dir.mkdir(parents=True, exist_ok=True)
    total = len(scenes)

    for i, scene in enumerate(scenes):
        index = int(scene.get("index", i))
        filename = f"scene_{index:03d}.wav"
        out_path = audio_dir / filename
        narration = scene.get("narration") or ""

        duration = await generate_scene_audio(narration, voice, speed, out_path)

        scene["audio_path"] = f"audio/{filename}"
        scene["audio_duration"] = duration

        if progress_cb is not None:
            message = f"Voicing scene {i + 1}/{total}"
            maybe_awaitable = progress_cb((i + 1) / total if total else 1.0, message)
            if asyncio.iscoroutine(maybe_awaitable):
                await maybe_awaitable

    return scenes


def concatenate_audio_files(files: list[str | Path], output_path: str | Path) -> float:
    """Concatenates WAV files (in order) into one file via ffmpeg's concat
    demuxer. Returns the resulting duration (ffprobe). Raises RuntimeError on
    failure or if `files` is empty.
    """
    files = [Path(f) for f in files]
    if not files:
        raise RuntimeError("concatenate_audio_files called with no input files")

    for f in files:
        if not f.exists():
            raise RuntimeError(f"concatenate_audio_files: missing input file {f}")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if len(files) == 1:
        shutil.copyfile(files[0], output_path)
        return get_audio_duration(output_path)

    ffmpeg_bin = _ffmpeg_bin()
    creationflags = 0x08000000 if os.name == "nt" else 0

    list_path = output_path.parent / f"_concat_{os.getpid()}.txt"
    try:
        with open(list_path, "w", encoding="utf-8") as fh:
            for f in files:
                escaped = str(f.resolve()).replace("'", "'\\''")
                fh.write(f"file '{escaped}'\n")

        cmd = [
            ffmpeg_bin, "-y", "-f", "concat", "-safe", "0",
            "-i", str(list_path),
            "-ar", str(_SAMPLE_RATE), "-ac", "1",
            str(output_path),
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600, creationflags=creationflags,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg concat failed (code {result.returncode}): {result.stderr[-400:]}"
            )
    finally:
        list_path.unlink(missing_ok=True)

    return get_audio_duration(output_path)


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
