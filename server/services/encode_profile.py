"""Profilul de encodare al fisierului livrat — "ca la CapCut".

De ce exista un singur loc pentru asta: fisierul care ajunge pe canal trece prin
pana la TREI encodari, iar ultima o rescrie pe prima:

  1. `remix_pipeline._stage_match_and_caption` — speed-match + subtitrari
  2. `commentator_overlay.composite_commentator` — compune avatarul
  3. `parallel_pipeline._split_video`            — taie in parti

Presetele cu avatar si cu `split_into_parts` (narator, comentator, povestitor,
victoria — adica toate cele folosite) trec prin toate trei. Daca setam bitrate-ul
doar in prima, taierea in parti il arunca. Deci toate trei cer aceiasi parametri.

Ce imitam din exportul CapCut:
  - H.264 **profil Main** (x264 ar alege High pentru yuv420p 8-bit)
  - ~14.1 Mbps, VBR plafonat
  - GOP **fix** la 2.5 s. `sc_threshold 0` e partea care conteaza: fara el x264
    mai pune cadre cheie la taieturile de scena si intervalul nu mai e constant.

Se suprascriu din mediu, ca sa nu ceara editare de cod:
    CLIPFORGE_X264_PROFILE   (implicit "main")
    CLIPFORGE_VIDEO_BITRATE  (implicit "14100k")
    CLIPFORGE_KEYINT_SEC     (implicit "2.5")
Un bitrate gol ("") readuce comportamentul vechi, pe CRF.
"""
from __future__ import annotations

import os
from typing import List

PROFILE = os.environ.get("CLIPFORGE_X264_PROFILE", "main").strip()
BITRATE = os.environ.get("CLIPFORGE_VIDEO_BITRATE", "14100k").strip()
KEYINT_SEC = float(os.environ.get("CLIPFORGE_KEYINT_SEC", "2.5"))


def _kbps(v: str) -> int:
    v = v.lower().rstrip("k")
    return int(float(v))


def video_args(fps: int, crf_fallback: str = "18") -> List[str]:
    """Argumentele de codec video, identice in toate cele trei encodari.

    `crf_fallback` se foloseste doar cand bitrate-ul e golit din mediu — atunci
    fiecare apelant isi pastreaza calitatea pe care o avea inainte.
    """
    args: List[str] = ["-c:v", "libx264"]
    if PROFILE:
        args += ["-profile:v", PROFILE]

    if BITRATE:
        b = _kbps(BITRATE)
        # Plafon cu putina marja + buffer de doua secunde de bitrate: destul cat
        # sa nu tocheze scenele agitate, dar fara varfuri care sperie platformele.
        args += ["-b:v", f"{b}k", "-maxrate", f"{int(b * 1.07)}k", "-bufsize", f"{b * 2}k"]
    else:
        args += ["-crf", crf_fallback]

    if KEYINT_SEC > 0:
        g = max(1, round(fps * KEYINT_SEC))
        args += ["-g", str(g), "-keyint_min", str(g), "-sc_threshold", "0"]
    return args


def describe() -> str:
    return (f"h264 profil={PROFILE or 'auto'} bitrate={BITRATE or 'CRF'} "
            f"keyint={KEYINT_SEC}s")
