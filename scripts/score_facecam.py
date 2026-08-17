"""Score facecam detection against docs/source-labels.md.

Six approaches to this edge have already failed. The only way not to be the
seventh is to score every candidate against the labelled sources before
believing it, which is what session 5 built the labels for.

Frames on disk are sampled across the WHOLE source, so each entry carries the
fraction of the file where its truth is stable — a source whose camera changes
mid-stream has no single correct answer for the whole file.
"""
from __future__ import annotations

import glob
import sys

import cv2

sys.path.insert(0, r"F:\ClipForge\server")

from services.clipper import content_type as ct                    # noqa: E402
from services.clipper.content_geom import make_rect                # noqa: E402
from services.clipper.signals import detect_faces                  # noqa: E402

# (project, label, want, from_frac, to_frac) — ranges out of source-labels.md.
SOURCES = [
    ("39c89ae2e16e", "IShowSpeed EARLY, gaming part", 1, 0.14, 1.0),
    ("2c8af11153a3", "moistcr1tikal, left edge mid-height", 1, 0.0, 1.0),
    ("slice4h00test", "Minecraft 4h, after the gym", 2, 0.15, 1.0),
    ("2d3375ee3420", "Minecraft 12m", 2, 0.0, 1.0),
    ("0c9685df852b", "gym 12m, fullscreen", 0, 0.0, 1.0),
    ("f81b86d27877", "go ghost, edited", 0, 0.0, 1.0),
    ("ee0e599b3ecb", "apartament, edited", 0, 0.0, 1.0),
    ("6b3844793c6a", "Jensen Huang, edited", 0, 0.0, 1.0),
    ("5f4c2770254d", "Jynxzi, bottom-left", 1, 0.40, 1.0),
]

_CACHE: dict[str, tuple] = {}


def load(pid: str, lo: float, hi: float):
    """(grays, faces) for one stretch. Face detection is the slow part, so the
    whole file is detected once and sliced per range."""
    if pid not in _CACHE:
        files = sorted(glob.glob(fr"F:\ClipForge\data\clipper\{pid}\frames\*.jpg"))
        grays = []
        for f in files:
            img = cv2.imread(f)
            if img is not None:
                grays.append(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
        faces = [[make_rect(*b) for b in detect_faces(g)] for g in grays]
        _CACHE[pid] = (grays, faces)
    grays, faces = _CACHE[pid]
    a, b = int(len(grays) * lo), int(len(grays) * hi)
    return grays[a:b], faces[a:b]


def score(label: str = "") -> tuple[int, int]:
    right = 0
    for pid, name, want, lo, hi in SOURCES:
        grays, faces = load(pid, lo, hi)
        if len(grays) < ct._MIN_RANGE_FRAMES:
            print(f"  {name:36s} SKIP (only {len(grays)} frames)")
            continue
        fh, fw = grays[0].shape[:2]
        cams, _ = ct._find_webcams(grays, faces, fw, fh)
        got = len(cams)
        ok = got == want
        right += ok
        mark = "OK " if ok else "XX "
        rects = ", ".join(f"{c['w']}x{c['h']}@{c['x']},{c['y']}" for c in cams[:2])
        print(f"  {mark}{name:36s} want {want} got {got}   {rects}")
    print(f"  --> {right}/{len(SOURCES)}  {label}")
    return right, len(SOURCES)


if __name__ == "__main__":
    print("BASELINE (_FACECAM_OUTER = %.1f)" % ct._FACECAM_OUTER)
    score("baseline")
