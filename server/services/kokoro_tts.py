"""TTS local pe GPU, cu Kokoro — zero credite ElevenLabs.

Kokoro e un model mic (82M) care ruleaza pe placa video si scoate voci
englezesti bune. NU cloneaza voci: are un set fix de voci. Pentru clonare e
nevoie de alt motor (vezi `services/tts.py` — XTTS).

De ce conteaza distinctia: pentru canalul de YouTube in engleza, Kokoro e
suficient si gratuit. Pentru romana NU e o optiune — modelul nu are voci
romanesti, iar `lang_code` nu include ro.

Modelul se incarca o singura data si ramane in memorie: prima sinteza costa
~18 secunde de incarcare, urmatoarele sunt aproape instant.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger("clipforge.kokoro")

# lang_code -> prefixul vocilor. 'a' = engleza americana, 'b' = britanica.
LANG_CODES = {"en": "a", "en-us": "a", "en-gb": "b"}
DEFAULT_VOICE = "af_heart"
SAMPLE_RATE = 24000

_pipelines: dict = {}
_lock = threading.Lock()


def is_available() -> bool:
    try:
        import kokoro  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _pipeline(lang_code: str):
    """Un pipeline pe limba, refolosit — incarcarea modelului e partea scumpa."""
    with _lock:
        if lang_code not in _pipelines:
            import torch
            from kokoro import KPipeline
            dev = "cuda" if torch.cuda.is_available() else "cpu"
            logger.info("Kokoro: incarc modelul (lang=%s) pe %s", lang_code, dev)
            _pipelines[lang_code] = KPipeline(lang_code=lang_code, device=dev)
        return _pipelines[lang_code]


def synthesize(text: str, output_path: str, voice: Optional[str] = None,
               language: str = "en", speed: float = 1.0) -> str:
    """Scrie un WAV la `output_path` si intoarce calea."""
    if not is_available():
        raise RuntimeError(
            "Kokoro nu e instalat. Ruleaza: server/.venv/Scripts/pip install kokoro")
    if not (text or "").strip():
        raise RuntimeError("Kokoro: text gol")

    import numpy as np
    import soundfile as sf

    lang_code = LANG_CODES.get((language or "en").lower(), "a")
    if (language or "en").lower().split("-")[0] != "en":
        raise RuntimeError(
            f"Kokoro nu are voci pentru '{language}' — merge doar engleza. "
            f"Pentru alte limbi foloseste elevenlabs sau local_clone.")

    pipe = _pipeline(lang_code)
    # speed e acceptat direct de Kokoro; il limitam la ce suna inca natural
    sp = max(0.5, min(2.0, float(speed or 1.0)))
    bucati = [a for _, _, a in pipe(text, voice=voice or DEFAULT_VOICE, speed=sp)]
    if not bucati:
        raise RuntimeError("Kokoro nu a produs audio")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    sf.write(output_path, np.concatenate(bucati), SAMPLE_RATE)
    return output_path


def list_voices() -> list:
    """Vocile din pachet. Prefixele: af/am = american female/male, bf/bm = british."""
    return ["af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky",
            "am_adam", "am_michael", "bf_emma", "bf_isabella",
            "bm_george", "bm_lewis"]
