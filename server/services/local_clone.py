"""
Local Romanian (and arbitrary-language) voice-cloning pipeline.

Stage 1 — Piper TTS
  Native Romanian phonemes, ONNX-based, fast on CPU. Produces a WAV in a
  stock Romanian voice (`ro_RO-mihai-medium`).

Stage 2 — OpenVoice v2 Tone Color Converter
  Takes stage-1's WAV + the user's reference clip and warps the timbre to
  sound like the reference. Language-agnostic — it operates on acoustic
  features, not linguistic content. This is why we can clone a voice into
  Romanian even though OpenVoice itself doesn't speak Romanian.

Everything is lazy:
  - First call → downloads the Piper model (~63 MB) into data/models/local_clone/
  - First call → loads OpenVoice checkpoints (~500 MB) from same dir
  - If either dependency is missing, raises with a clear install hint.

The server boots fine without any of this installed; the engine just
reports `ready=False` with a hint until the user runs `pip install piper-tts`
and the OpenVoice install one-liner.
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
import zipfile
from pathlib import Path
from typing import Optional, Tuple
from urllib.request import urlretrieve

import numpy as np

logger = logging.getLogger("clipforge.local_clone")

# ── Where we keep models on disk ──────────────────────────────────────────
def _models_dir() -> Path:
    from config import settings
    d = Path(settings.data_dir) / "models" / "local_clone"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Piper Romanian model (Mihai, medium quality) ──────────────────────────
PIPER_MODEL_NAME = "ro_RO-mihai-medium"
PIPER_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/ro/ro_RO/mihai/medium"
PIPER_MODEL_URL = f"{PIPER_BASE}/{PIPER_MODEL_NAME}.onnx"
PIPER_CONFIG_URL = f"{PIPER_BASE}/{PIPER_MODEL_NAME}.onnx.json"


def _piper_model_path() -> Path:
    return _models_dir() / f"{PIPER_MODEL_NAME}.onnx"


def _piper_config_path() -> Path:
    return _models_dir() / f"{PIPER_MODEL_NAME}.onnx.json"


# ── OpenVoice v2 checkpoints ──────────────────────────────────────────────
OPENVOICE_CKPT_URL = "https://myshell-public-repo-host.s3.amazonaws.com/openvoice/checkpoints_v2_0417.zip"
OPENVOICE_DIR_NAME = "openvoice_v2"


def _openvoice_dir() -> Path:
    return _models_dir() / OPENVOICE_DIR_NAME


def _openvoice_converter_ckpt() -> Path:
    return _openvoice_dir() / "checkpoints_v2" / "converter" / "checkpoint.pth"


def _openvoice_converter_cfg() -> Path:
    return _openvoice_dir() / "checkpoints_v2" / "converter" / "config.json"


# ── Lazy singletons ───────────────────────────────────────────────────────
_piper_lock = threading.Lock()
_piper_voice = None

_openvoice_lock = threading.Lock()
_openvoice_converter = None


# ── Capability probes ─────────────────────────────────────────────────────
def is_piper_installed() -> Tuple[bool, Optional[str]]:
    try:
        import piper  # noqa
        return True, None
    except ImportError:
        return False, "Run: pip install piper-tts"


def is_openvoice_installed() -> Tuple[bool, Optional[str]]:
    try:
        from openvoice.api import ToneColorConverter  # noqa
        return True, None
    except ImportError:
        return False, (
            "Run: pip install git+https://github.com/myshell-ai/OpenVoice.git "
            "(also: pip install wavmark)"
        )


def status() -> dict:
    piper_ok, piper_hint = is_piper_installed()
    openvoice_ok, ov_hint = is_openvoice_installed()
    return {
        "piper_installed": piper_ok,
        "piper_hint": piper_hint,
        "piper_model_downloaded": _piper_model_path().exists(),
        "openvoice_installed": openvoice_ok,
        "openvoice_hint": ov_hint,
        "openvoice_ckpt_downloaded": _openvoice_converter_ckpt().exists(),
        "ready": piper_ok and openvoice_ok,
    }


# ── Model fetching ────────────────────────────────────────────────────────
def _download(url: str, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"Downloading {url} → {dst.name}")
    tmp = dst.with_suffix(dst.suffix + ".part")
    urlretrieve(url, tmp)
    tmp.replace(dst)
    logger.info(f"Saved {dst.name} ({dst.stat().st_size // 1024} KB)")


def _ensure_piper_model() -> None:
    if not _piper_model_path().exists():
        _download(PIPER_MODEL_URL, _piper_model_path())
    if not _piper_config_path().exists():
        _download(PIPER_CONFIG_URL, _piper_config_path())


def _ensure_openvoice_ckpts() -> None:
    if _openvoice_converter_ckpt().exists():
        return
    zip_path = _models_dir() / "openvoice_v2.zip"
    if not zip_path.exists():
        _download(OPENVOICE_CKPT_URL, zip_path)
    logger.info(f"Extracting {zip_path.name} → {_openvoice_dir()}")
    _openvoice_dir().mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(_openvoice_dir())
    # Keep the zip in case the user wants to repair later; comment out to delete.
    if not _openvoice_converter_ckpt().exists():
        raise RuntimeError(
            f"Extraction succeeded but converter checkpoint missing at "
            f"{_openvoice_converter_ckpt()}. The OpenVoice zip layout may have changed."
        )


# ── Stage 1: Piper Romanian TTS ───────────────────────────────────────────
def _get_piper_voice():
    global _piper_voice
    if _piper_voice is not None:
        return _piper_voice
    with _piper_lock:
        if _piper_voice is not None:
            return _piper_voice
        ok, hint = is_piper_installed()
        if not ok:
            raise RuntimeError(hint)
        _ensure_piper_model()
        # piper >= 1.3 exposes PiperVoice
        from piper import PiperVoice
        logger.info(f"Loading Piper voice {PIPER_MODEL_NAME}…")
        _piper_voice = PiperVoice.load(
            str(_piper_model_path()),
            config_path=str(_piper_config_path()),
        )
        logger.info("Piper voice loaded")
        return _piper_voice


def _piper_synth(text: str, output_wav: str) -> str:
    """Generate a WAV at 22.05 kHz mono in Piper's stock RO voice.

    Piper 1.4 changed the API — `synthesize` now returns an iterable of
    AudioChunk; the convenience wrapper `synthesize_wav` writes a full
    wave file (including the format header) for us.
    """
    import wave
    voice = _get_piper_voice()
    Path(output_wav).parent.mkdir(parents=True, exist_ok=True)
    with wave.open(output_wav, "wb") as wf:
        voice.synthesize_wav(text, wf)
    return output_wav


# ── Stage 2: OpenVoice tone color converter ───────────────────────────────
def _get_openvoice_converter():
    global _openvoice_converter
    if _openvoice_converter is not None:
        return _openvoice_converter
    with _openvoice_lock:
        if _openvoice_converter is not None:
            return _openvoice_converter
        ok, hint = is_openvoice_installed()
        if not ok:
            raise RuntimeError(hint)
        _ensure_openvoice_ckpts()
        from openvoice.api import ToneColorConverter
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            device = "cpu"
        logger.info(f"Loading OpenVoice ToneColorConverter on {device}…")
        conv = ToneColorConverter(str(_openvoice_converter_cfg()), device=device)
        conv.load_ckpt(str(_openvoice_converter_ckpt()))
        _openvoice_converter = conv
        logger.info("OpenVoice converter loaded")
        return _openvoice_converter


def _extract_speaker_embedding(audio_path: str):
    """Compute the SE (speaker embedding) for a reference WAV/MP3."""
    from openvoice import se_extractor
    converter = _get_openvoice_converter()
    se, _audio_name = se_extractor.get_se(
        audio_path,
        converter,
        target_dir=str(_models_dir() / "se_cache"),
        vad=True,
    )
    return se


# ── Public API ────────────────────────────────────────────────────────────
def synthesize_cloned(
    text: str,
    reference_audio_path: str,
    output_path: str,
    *,
    base_speaker_embedding_path: Optional[str] = None,
) -> str:
    """
    Romanian text → cloned voice WAV.

    text:                  the Romanian script
    reference_audio_path:  user's voice sample (6-30s, clean, single speaker)
    output_path:           where to write the final cloned WAV

    Falls back to raw Piper output if OpenVoice isn't installed yet, so
    the engine still produces *something* and the user gets a Romanian
    voice (just not the cloned one) until they finish the install.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("text is required")
    if not Path(reference_audio_path).exists():
        raise FileNotFoundError(f"reference clip not found: {reference_audio_path}")

    # Step 1 — Piper Romanian synthesis to a temp WAV
    out_dir = Path(output_path).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    piper_out = str(out_dir / f"_piper_stage_{int(time.time() * 1000)}.wav")
    t0 = time.time()
    _piper_synth(text, piper_out)
    logger.info(f"Piper stage done in {time.time() - t0:.1f}s → {Path(piper_out).name}")

    # Step 2 — OpenVoice tone color conversion (cloning)
    try:
        ov_ok, _ = is_openvoice_installed()
        if not ov_ok:
            logger.warning("OpenVoice not installed; returning raw Piper output (uncloned)")
            shutil.copy(piper_out, output_path)
            Path(piper_out).unlink(missing_ok=True)
            return output_path

        converter = _get_openvoice_converter()

        # Pre-compute / cache the base speaker embedding for Piper's voice.
        # Piper always outputs the same stock voice, so the base SE is fixed —
        # we extract once on first ever run, cache to disk, and reuse.
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
        base_se_path = _models_dir() / "se_cache" / "_piper_base.pt"
        if base_se_path.exists():
            base_se = torch.load(str(base_se_path), map_location=device)
        else:
            base_se_path.parent.mkdir(parents=True, exist_ok=True)
            base_se = _extract_speaker_embedding(piper_out)
            try:
                torch.save(base_se, str(base_se_path))
            except Exception:
                logger.exception("Could not cache base SE — proceeding anyway")

        # Always compute target SE for the user's reference
        target_se = _extract_speaker_embedding(reference_audio_path)

        t1 = time.time()
        converter.convert(
            audio_src_path=piper_out,
            src_se=base_se,
            tgt_se=target_se,
            output_path=output_path,
            message="@ClipForge",
        )
        logger.info(f"OpenVoice convert done in {time.time() - t1:.1f}s → {Path(output_path).name}")
    finally:
        Path(piper_out).unlink(missing_ok=True)

    return output_path
