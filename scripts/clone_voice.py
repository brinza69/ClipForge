"""Clonare de voce in ROMANA — se ruleaza in venv-ul izolat .venv_clone.

De ce un venv separat: OpenVoice cere Python <=3.11 si un `av` vechi care nu se
compileaza pe 3.13. Rigul principal ruleaza pe 3.13 si produce bani — nu se
atinge. Aici e izolat complet.

Lantul:
  1. Piper (`ro_RO-mihai-medium`) scoate textul romanesc intr-o voce stoc.
     XTTS NU e o optiune: nu vorbeste romana.
  2. OpenVoice v2 ia acel WAV + clipul tau de referinta si muta TIMBRUL spre
     vocea ta. Lucreaza pe trasaturi acustice, nu pe limba — de aia poate clona
     intr-o limba pe care el n-o cunoaste.

    .venv_clone\\Scripts\\python.exe scripts\\clone_voice.py ^
        --referinta "C:\\...\\voce.wav" --text "Text romanesc." --iesire out.wav
"""
import argparse
import pathlib
import sys
import time

RADACINA = pathlib.Path(__file__).resolve().parents[1]
MODELE = RADACINA / "data" / "models" / "local_clone"
CONV_DIR = MODELE / "openvoice_v2" / "checkpoints_v2" / "converter"
PIPER_MODEL = MODELE / "ro_RO-mihai-medium.onnx"


def piper_wav(text: str, out: pathlib.Path) -> pathlib.Path:
    """Etapa 1 — fonemele romanesti, in vocea stoc."""
    import wave
    from piper import PiperVoice
    v = PiperVoice.load(str(PIPER_MODEL), config_path=str(PIPER_MODEL) + ".json")
    with wave.open(str(out), "wb") as w:
        v.synthesize_wav(text, w)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--referinta", required=True, help="clip cu vocea de clonat")
    ap.add_argument("--text", required=True)
    ap.add_argument("--iesire", required=True)
    ap.add_argument("--tau", type=float, default=0.3,
                    help="cat de tare se aplica timbrul (0.1-0.5); prea mare = artefacte")
    a = ap.parse_args()

    import torch
    from openvoice import se_extractor
    from openvoice.api import ToneColorConverter

    iesire = pathlib.Path(a.iesire)
    iesire.parent.mkdir(parents=True, exist_ok=True)
    tmp = iesire.with_suffix(".piper.wav")

    t0 = time.time()
    piper_wav(a.text, tmp)
    print(f"  1/3 Piper (romana): {time.time() - t0:.1f}s", flush=True)

    conv = ToneColorConverter(str(CONV_DIR / "config.json"), device="cpu")
    conv.load_ckpt(str(CONV_DIR / "checkpoint.pth"))

    t0 = time.time()
    # Embedding-ul vocii TINTA (a ta), din clipul de referinta.
    se_tinta, _ = se_extractor.get_se(a.referinta, conv,
                                      target_dir=str(MODELE / "se_cache"), vad=True)
    print(f"  2/3 amprenta vocii tale: {time.time() - t0:.1f}s", flush=True)

    # Embedding-ul vocii SURSA (Piper) — fara el, convertorul n-are de la ce pleca.
    se_sursa, _ = se_extractor.get_se(str(tmp), conv,
                                      target_dir=str(MODELE / "se_cache"), vad=True)

    t0 = time.time()
    conv.convert(audio_src_path=str(tmp), src_se=se_sursa, tgt_se=se_tinta,
                 output_path=str(iesire), tau=a.tau)
    print(f"  3/3 conversie timbru: {time.time() - t0:.1f}s", flush=True)
    print(f"gata: {iesire}")
    tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
