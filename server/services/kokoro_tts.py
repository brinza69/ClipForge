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
import os
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


# Semne pe care misaki le lasa fara foneme, dar care au un echivalent rostit.
# Inlocuirea NU pune spatii in jur, dinadins — vezi nota despre aliniere.
_SIMBOLURI = {"&": "and", "+": "plus", "=": "equals", "@": "at",
              "×": "times", "÷": "divided by", "<": "less than",
              ">": "greater than", "^": "to the power of"}


def _sanitize(text: str) -> str:
    """Scoate ce nu poate fonetiza misaki, PASTRAND numarul de cuvinte.

    G2P-ul lui Kokoro intoarce `phonemes = None` pentru orice iese din ASCII —
    diacritice, emoji — si pentru `+` / `=`. Apoi `''.join(t.phonemes + ...)`
    crapa cu `NoneType + str`, la 48% din randare, dupa ce s-a platit deja
    transcrierea si stersul. Deci se curata INAINTE, nu se prinde dupa.

    Numarul de tokenuri conteaza: subtitrarile se taie din ACELASI sir, pe
    cuvinte (`_split_into_caption_chunks`), deci daca vocea spune mai putine
    cuvinte decat scrie pe ecran, subtitrarea derivea. De-aia inlocuirile sunt
    1-la-1: un "+" de sine statator devine "plus" (tot un token), iar "3+4"
    devine "3plus4" (tot un token). Diacriticele se aplatizeaza, nu se sterg —
    un cititor englezesc oricum n-ar reda "a", dar numele ramane recognoscibil.

    Ramane o gaura: un emoji izolat intre spatii dispare cu totul si pierde un
    token. In naratiune curatata de LLM nu apar emoji, deci se accepta — o
    derivare de un cuvant e oricum mai buna decat o randare picata.
    """
    import unicodedata
    for s, cuvant in _SIMBOLURI.items():
        text = text.replace(s, cuvant)
    # NFKD desparte litera de semnul diacritic; combining marks se arunca.
    text = "".join(c for c in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(c))
    # Ce a ramas non-ASCII (emoji, alfabete straine) n-are foneme englezesti.
    # Se sterge fara spatiu, ca sa nu rupa in doua cuvantul de care era lipit.
    curatat = "".join(c if ord(c) < 128 else "" for c in text)
    return " ".join(curatat.split())


def _leaga_espeak() -> bool:
    """Arata-i lui phonemizer unde e espeak-ng, ca sa mearga rezerva lui Kokoro.

    Fara asta, orice cuvant din afara vocabularului (nume proprii, mai ales
    straine) primeste `phonemes = None` si randarea crapa cu `NoneType + str`
    abia in etapa de voce — dupa ce s-a platit deja transcrierea si stersul.

    Binarul VINE cu `espeakng_loader`, instalat ca dependenta a lui kokoro. Dar
    `misaki.espeak.set_espeak_library()` cauta doar la calea fixa
    `C:\\Program Files\\eSpeak NG\\libespeak-ng.dll`, care pe rig nu exista —
    deci rezerva ramanea moarta desi fisierul era pe disc.
    """
    try:
        import espeakng_loader
        from phonemizer.backend.espeak.wrapper import EspeakWrapper
        if not EspeakWrapper._ESPEAK_LIBRARY:
            EspeakWrapper.set_library(espeakng_loader.get_library_path())
            os.environ.setdefault("ESPEAK_DATA_PATH", str(espeakng_loader.get_data_path()))
        return True
    except Exception:  # noqa: BLE001
        logger.warning("Kokoro: espeak indisponibil — cuvintele necunoscute vor fi sarite")
        return False


def _pipeline(lang_code: str):
    """Un pipeline pe limba, refolosit — incarcarea modelului e partea scumpa."""
    with _lock:
        if lang_code not in _pipelines:
            import torch
            from kokoro import KPipeline
            _leaga_espeak()          # inainte de KPipeline: el isi face G2P-ul la construire
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

    curat = _sanitize(text)
    if not curat:
        raise RuntimeError("Kokoro: n-a ramas nimic rostibil dupa curatare")
    if curat != text:
        logger.info("Kokoro: text curatat pentru misaki (%d -> %d caractere)",
                    len(text), len(curat))

    pipe = _pipeline(lang_code)
    # speed e acceptat direct de Kokoro; il limitam la ce suna inca natural
    sp = max(0.5, min(2.0, float(speed or 1.0)))
    bucati = [a for _, _, a in pipe(curat, voice=voice or DEFAULT_VOICE, speed=sp)]
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
