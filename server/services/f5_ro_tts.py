"""TTS in ROMANA cu clonare zero-shot, local pe GPU — zero credite ElevenLabs.

Modelul e `cdorob/f5-tts-romanian` (MIT, antrenat pe 173h: Common Voice 17 RO +
TTS-Romanian). Spre deosebire de `local_clone` (Piper + OpenVoice, care doar
coloreaza timbrul peste o voce stoc), asta e clonare adevarata: primeste un clip
de referinta scurt si citeste text nou in vocea aia. Masurat pe rig cu
faster-whisper `medium`: WER 2.0%, si singura nepotrivire a fost Whisper scriind
"30" in loc de "treizeci" — pronuntia a iesit fara nicio greseala.

De ce subprocess si nu import direct:
  f5-tts cere torch 2.5.1+cu121. Venv-ul de productie (`server/.venv`) ruleaza
  pipeline-urile care produc bani si are propriul torch — nu se atinge. F5 sta
  izolat in `.venv_f5` (Python 3.11) si se apeleaza prin CLI-ul lui.

CRITIC: CLI-ul e vorbaret (bare de progres tqdm). Iesirea merge intr-un FISIER,
niciodata in stderr=PIPE — bufferul de 64KB s-ar umple si procesul copil ar
bloca la infinit. Vezi aceeasi capcana la realesrgan in CLAUDE.md.

Vocea de referinta:
  `data/voices/<id>.wav` + transcriptul ei in `data/voices/<id>.txt`.
  Daca .txt lipseste, se transcrie o data cu faster-whisper si se pune in cache
  acolo — F5 are nevoie de textul referintei, nu doar de audio.
"""
from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger("clipforge.f5_ro")

MODEL_NAME = "F5TTS_v1_Base"
SAMPLE_RATE = 24000
# Referintele bune stau sub 15s; peste, F5 pierde din fidelitate.
REF_MAX_SECONDS = 15.0
# Plafon de siguranta — peste asta e aproape sigur un paste accidental.
HARD_MAX_CHARS = 20000

_transcribe_lock = threading.Lock()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _venv_python() -> Path:
    return _repo_root() / ".venv_f5" / "Scripts" / "python.exe"


def _model_dir() -> Path:
    """Modelul, cautat intai in data dir-ul backendului si apoi in `data/`.

    Rigul are DOUA backenduri cu date separate: A pe `data/`, B pe `data_b/`.
    Modelul (1,35 GB) e descarcat o singura data, in `data/models/f5_ro`, deci
    pentru backendul B calea din `settings.data_dir` nu exista. Fara cautarea
    asta, `is_available()` intoarce False pe B si vocea gratuita nu porneste
    acolo niciodata — exact ce s-a intamplat pe 29-30 aug, cand povestitorul
    romanesc a randat pe B si a mers pe ElevenLabs fara sa se planga nimeni.
    Modelul e read-only, deci partajarea lui intre backenduri e sigura.
    """
    from config import settings
    al_meu = Path(settings.data_dir) / "models" / "f5_ro"
    if (al_meu / "vocab.txt").exists():
        return al_meu
    comun = _repo_root() / "data" / "models" / "f5_ro"
    return comun if (comun / "vocab.txt").exists() else al_meu


def _ckpt() -> Path:
    """Fisierul de inferenta cand exista, altfel checkpointul de antrenare.

    `model_inferenta.safetensors` (1,35 GB) tine doar greutatile EMA si se
    citeste memory-mapped; `model_last.pt` (5,39 GB) e checkpointul de
    antrenare intreg, si `utils_infer` il trage tot prin RAM — pe rigul asta
    aia inseamna OOM sau segfault cand mai randeaza ceva. Vezi
    `scripts/f5_model_inferenta.py`, care il produce.
    """
    d = _model_dir()
    subtire = d / "model_inferenta.safetensors"
    return subtire if subtire.exists() else d / "model_last.pt"


def _vocab() -> Path:
    return _model_dir() / "vocab.txt"


def _voices_dir() -> Path:
    """Clipurile de referinta, cu aceeasi cautare in doua locuri ca modelul:
    sunt puse o singura data, in `data/voices`, iar backendul B nu le are."""
    from config import settings
    d = Path(settings.data_dir) / "voices"
    d.mkdir(parents=True, exist_ok=True)
    if any(d.iterdir()):
        return d
    comun = _repo_root() / "data" / "voices"
    return comun if comun.is_dir() and any(comun.iterdir()) else d


# ── Sonde ─────────────────────────────────────────────────────────────────
def is_available() -> bool:
    return _venv_python().exists() and _ckpt().exists() and _vocab().exists()


def status() -> dict:
    py, ck, vo = _venv_python(), _ckpt(), _vocab()
    hints = []
    if not py.exists():
        hints.append(f"lipseste venv-ul {py.parent.parent.name} — vezi docs/voce-locala.md")
    if not ck.exists():
        hints.append("lipsesc si model_inferenta.safetensors, si model_last.pt "
                     "din data/models/f5_ro/")
    if not vo.exists():
        hints.append("lipseste vocab.txt din data/models/f5_ro/")
    return {
        "venv": py.exists(),
        "checkpoint": ck.exists(),
        "vocab": vo.exists(),
        "ready": is_available(),
        "hint": " | ".join(hints) or None,
    }


def list_voices() -> list:
    """Clipurile de referinta din data/voices/, cu marcaj daca au transcript."""
    out = []
    for p in sorted(_voices_dir().iterdir()):
        if p.suffix.lower() not in (".wav", ".mp3", ".flac", ".m4a", ".ogg"):
            continue
        out.append({
            "id": p.name,
            "name": p.stem.replace("_", " ").strip(),
            "has_transcript": p.with_suffix(".txt").exists(),
        })
    return out


# ── Transcriptul referintei ───────────────────────────────────────────────
def _ref_text(ref_path: Path) -> str:
    """Textul clipului de referinta. Se transcrie o singura data si se cacheaza."""
    txt = ref_path.with_suffix(".txt")
    if txt.exists():
        s = txt.read_text(encoding="utf-8").strip()
        if s:
            return s

    with _transcribe_lock:
        if txt.exists():                       # alt thread a ajuns primul
            s = txt.read_text(encoding="utf-8").strip()
            if s:
                return s
        logger.info("F5: transcriu referinta %s (o singura data)", ref_path.name)
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise RuntimeError(
                f"Referinta '{ref_path.name}' nu are transcript si faster-whisper "
                f"nu e instalat. Scrie textul rostit in {txt.name} si reia."
            ) from e
        model = WhisperModel("medium", device="cuda", compute_type="float16")
        segs, _ = model.transcribe(str(ref_path), language="ro", beam_size=5)
        s = " ".join(x.text.strip() for x in segs).strip()
        if not s:
            raise RuntimeError(f"nu am putut transcrie {ref_path.name} — pune textul in {txt.name}")
        txt.write_text(s, encoding="utf-8")
        logger.info("F5: transcript salvat in %s", txt.name)
        return s


def _ref_duration(p: Path) -> Optional[float]:
    try:
        import wave
        with wave.open(str(p), "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:                          # mp3/m4a — nu merita ffprobe aici
        return None


def get_voice_path(voice_id: str) -> Optional[Path]:
    p = _voices_dir() / voice_id
    return p if p.is_file() else None


# ── Sinteza ───────────────────────────────────────────────────────────────
def synthesize(
    text: str,
    output_path: str,
    voice_id: str,
    *,
    language: str = "ro",
    speed: float = 1.0,
    timeout: int = 1800,
) -> str:
    """Text romanesc -> WAV in vocea de referinta. Intoarce calea scrisa."""
    text = (text or "").strip()
    if not text:
        raise RuntimeError("F5: text gol")
    if len(text) > HARD_MAX_CHARS:
        raise RuntimeError(f"F5: text prea lung ({len(text)} caractere, plafon {HARD_MAX_CHARS})")

    lang = (language or "ro").lower().split("-")[0]
    if lang != "ro":
        raise RuntimeError(
            f"F5-TTS e antrenat pe romana; '{language}' n-are ce cauta aici. "
            f"Pentru engleza foloseste kokoro.")

    st = status()
    if not st["ready"]:
        raise RuntimeError(f"F5 nu e gata: {st['hint']}")

    ref = get_voice_path(voice_id)
    if ref is None:
        disponibile = [v["id"] for v in list_voices()]
        raise RuntimeError(f"vocea de referinta '{voice_id}' nu e in data/voices/. Am: {disponibile}")

    dur = _ref_duration(ref)
    if dur and dur > REF_MAX_SECONDS:
        logger.warning("F5: referinta %s are %.1fs (>%.0fs) — fidelitatea scade",
                       ref.name, dur, REF_MAX_SECONDS)

    ref_text = _ref_text(ref)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    # CLI-ul isi scrie propriile bare de progres — le trimitem intr-un fisier,
    # NU intr-un pipe (vezi nota din capul fisierului).
    log_path = out.with_suffix(".f5.log")

    cmd = [
        str(_venv_python()), "-m", "f5_tts.infer.infer_cli",
        "--model", MODEL_NAME,
        "--ckpt_file", str(_ckpt()),
        "--vocab_file", str(_vocab()),
        "--ref_audio", str(ref),
        "--ref_text", ref_text,
        "--gen_text", text,
        "--output_dir", str(out.parent),
        "--output_file", out.name,
        "--speed", f"{max(0.5, min(2.0, float(speed or 1.0))):.3f}",
        "--remove_silence",
    ]

    logger.info("F5: sintetizez %d caractere, voce=%s speed=%.2f", len(text), voice_id, speed)
    t0 = time.time()
    with open(log_path, "wb") as log:
        r = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT,
                           cwd=str(_repo_root()), timeout=timeout)

    if r.returncode != 0 or not out.exists():
        tail = ""
        try:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-800:]
        except Exception:
            pass
        raise RuntimeError(f"F5 a esuat (cod {r.returncode}). Ultimele linii:\n{tail}")

    logger.info("F5: gata in %.1fs -> %s", time.time() - t0, out.name)
    log_path.unlink(missing_ok=True)
    return str(out)


if __name__ == "__main__":
    import json
    print(json.dumps(status(), indent=2, ensure_ascii=False))
    print("voci:", json.dumps(list_voices(), ensure_ascii=False))
