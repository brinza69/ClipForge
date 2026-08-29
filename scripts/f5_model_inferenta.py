r"""Scoate din checkpointul F5 doar greutatile de care are nevoie inferenta.

DE CE EXISTA ASTA — a costat doua sesiuni de diagnostic gresit.

`model_last.pt` (5,39 GB) e un checkpoint de ANTRENARE: contine
`model_state_dict` (1,35 GB), `ema_model_state_dict` (1,35 GB) SI starea
optimizatorului si a scheduler-ului. Pentru generat voce se foloseste exclusiv
EMA — restul e balast.

Iar `f5_tts/infer/utils_infer.py:207` il citeste asa:

    checkpoint = torch.load(ckpt_path, map_location=device, weights_only=True)

fara `mmap`, si cu `map_location` pe GPU. Adica trage TOT fisierul de 5,39 GB
prin RAM. Pe rigul asta (16 GB, din care ~3 libere) aia nu incape, si esecul se
prezinta in doua feluri care trimit ancheta gresit:
  * "CUDA out of memory" desi nvidia-smi arata 7-8 GB liberi — pentru ca
    alocarile CUDA se sprijina pe commit-ul Windows, nu pe VRAM;
  * SEGFAULT fara traceback la incarcare — care seamana leit a venv stricat.
Nu era nici placa, nici venv-ul, nici fisierul: e de patru ori mai mare decat
trebuie. Verificat — toti cei 366 de tensori se citesc corect cu `mmap=True`,
de doua ori la rand.

Iesirea e `.safetensors` intentionat, nu `.pt`: pe ramura aia utils_infer
foloseste `load_file`, care e memory-mapped — deci nu doar ca fisierul e de
patru ori mai mic, dar nici nu mai trece integral prin RAM. Vocea porneste si
cand ambele placi randeaza.

DE CE SE SCRIE IN FLUX, si nu cu `safetensors.save_file`:
  `save_file` cere dictionarul intreg in memorie, adica 1,35 GB de clone tinute
  deodata. Aici se scrie tensor cu tensor, deci varful e UN tensor — cel mai
  mare are 25 MB. Nu inlocui asta cu `save_file` "ca e mai curat": formatul e
  trivial (antet JSON + date concatenate) si asta e singura varianta care nu
  depinde de cata memorie libera e in momentul rularii.

CAPCANELE TENSORILOR MAPATI, fiecare platita cu segfault fara traceback:
  * `.numpy()` pica — si pe tensorul mapat, si pe o copie a lui. De aia
    octetii se scot cu `ctypes`, direct de la `data_ptr()`. Nu-l inlocui cu
    numpy "ca e mai citet": numpy e 2.4.6, torch 2.5.1 e compilat pentru alta
    versiune, si nepotrivirea nu da eroare, da coruptie de memorie.
  * `.clone()` FARA `.detach()` pica la fel, determinist, pe primul tensor.
  Citirea in sine e sanatoasa: `v.float().sum()` peste tot checkpointul merge
  de doua ori la rand, deci fisierul NU e corupt — verificat cand parea ca e.

    .venv_f5\Scripts\python.exe scripts\f5_model_inferenta.py
"""
import ctypes
import json
import pathlib
import struct
import time

import torch

MODEL = pathlib.Path(__file__).resolve().parents[1] / "data" / "models" / "f5_ro"
SURSA = MODEL / "model_last.pt"
DEST = MODEL / "model_inferenta.safetensors"

# Chei de contabilitate ale EMA, nu greutati. `load_checkpoint` le sare oricum
# (`if k not in ["initted", "step"]`), iar safetensors n-ar accepta scalarii.
NEGREUTATI = ("initted", "step")

DTYPE = {torch.float32: "F32", torch.float16: "F16", torch.bfloat16: "BF16",
         torch.int64: "I64", torch.int32: "I32", torch.uint8: "U8", torch.bool: "BOOL"}


def main():
    if not SURSA.exists():
        raise SystemExit("lipseste " + str(SURSA))
    if DEST.exists():
        raise SystemExit(DEST.name + " exista deja — sterge-l daca vrei sa-l refaci")

    print("citesc %s (%.2f GB) cu mmap" % (SURSA.name, SURSA.stat().st_size / 1e9), flush=True)
    t0 = time.time()
    ck = torch.load(str(SURSA), map_location="cpu", mmap=True, weights_only=False)
    ema = ck.get("ema_model_state_dict")
    if not ema:
        raise SystemExit("checkpointul n-are ema_model_state_dict: " + str(list(ck.keys())))

    nume = [k for k in ema if k not in NEGREUTATI]
    antet, pozitie = {}, 0
    for k in nume:                      # doar metadate — nu se citeste nicio pagina
        v = ema[k]
        if v.dtype not in DTYPE:
            raise SystemExit("tip nesustinut " + str(v.dtype) + " la " + k)
        n = v.numel() * v.element_size()
        antet[k] = {"dtype": DTYPE[v.dtype], "shape": list(v.shape),
                    "data_offsets": [pozitie, pozitie + n]}
        pozitie += n
    brut = json.dumps(antet, separators=(",", ":")).encode("utf-8")
    brut += b" " * (-len(brut) % 8)     # datele incep aliniat la 8 octeti
    print("  %d tensori, %.2f GB de date" % (len(nume), pozitie / 1e9), flush=True)

    partial = DEST.with_suffix(".partial")
    with open(partial, "wb") as f:
        f.write(struct.pack("<Q", len(brut)))
        f.write(brut)
        for k in nume:
            copie = ema[k].detach().clone().contiguous()   # vezi capcanele de mai sus
            n = copie.numel() * copie.element_size()
            f.write(bytes((ctypes.c_char * n).from_address(copie.data_ptr())))
            del copie
    partial.replace(DEST)

    print("scris %s (%.2f GB) in %.0fs — de %.1fx mai mic"
          % (DEST.name, DEST.stat().st_size / 1e9, time.time() - t0,
             SURSA.stat().st_size / DEST.stat().st_size))


if __name__ == "__main__":
    main()
