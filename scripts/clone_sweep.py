"""Genereaza acelasi text la mai multe intensitati de timbru, ca sa se poata alege.

Amprenta vocii tinta se extrage O SINGURA DATA si se refoloseste — e partea
lenta. Doar conversia se repeta per `tau`.

`tau` = cat de tare se impune timbrul tintei peste vocea Piper. Mai mare =
seamana mai bine, dar peste un punct apar artefacte metalice.

    .venv_clone\Scripts\python.exe scripts\clone_sweep.py --referinta X.wav --text "..."
"""
import argparse, pathlib, time

RAD = pathlib.Path(__file__).resolve().parents[1]
MODELE = RAD / "data" / "models" / "local_clone"
CONV = MODELE / "openvoice_v2" / "checkpoints_v2" / "converter"
PIPER = MODELE / "ro_RO-mihai-medium.onnx"

ap = argparse.ArgumentParser()
ap.add_argument("--referinta", required=True)
ap.add_argument("--text", required=True)
ap.add_argument("--tau", default="0.3,0.4,0.5")
ap.add_argument("--prefix", default="clona")
a = ap.parse_args()

import wave
from piper import PiperVoice
from openvoice import se_extractor
from openvoice.api import ToneColorConverter

ies = RAD / "data" / "clone_variante"
ies.mkdir(parents=True, exist_ok=True)
baza = ies / f"{a.prefix}_piper.wav"

t0 = time.time()
v = PiperVoice.load(str(PIPER), config_path=str(PIPER) + ".json")
with wave.open(str(baza), "wb") as w:
    v.synthesize_wav(a.text, w)
print(f"Piper: {time.time()-t0:.1f}s -> {baza.name}", flush=True)

conv = ToneColorConverter(str(CONV / "config.json"), device="cpu")
conv.load_ckpt(str(CONV / "checkpoint.pth"))

t0 = time.time()
se_tinta, _ = se_extractor.get_se(a.referinta, conv, target_dir=str(MODELE / "se_cache"), vad=True)
se_sursa, _ = se_extractor.get_se(str(baza), conv, target_dir=str(MODELE / "se_cache"), vad=True)
print(f"amprente: {time.time()-t0:.1f}s", flush=True)

for tau in [float(x) for x in a.tau.split(",")]:
    out = ies / f"{a.prefix}_tau{tau:g}.wav"
    t0 = time.time()
    conv.convert(audio_src_path=str(baza), src_se=se_sursa, tgt_se=se_tinta,
                 output_path=str(out), tau=tau)
    print(f"  tau={tau:g}: {time.time()-t0:.1f}s -> {out.name}", flush=True)
print(f"\nvariante in: {ies}")
