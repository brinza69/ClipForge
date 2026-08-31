"""Would a narrower `fit` lose anything — measured on the frames it actually shows.

`fit` exists to keep the whole frame. The question is whether the whole frame is
where the CONTENT is: if a source pillarboxes its material onto black, a 4:5
window loses nothing and the 3.16x junction drops to about 1.4x. If the material
runs edge to edge, a narrower window destroys exactly what `fit` is for.

So: sample frames inside real `fit` spans, find the bounding box of the
non-black pixels, and report what fraction of the width it needs.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

DATA = Path("data/clipper")
#: Below this a pixel is background rather than content. Generous on purpose —
#: a compressed black bar is not exactly zero.
DARK = 18


def frame_at(mp4: Path, t: float, w: int, h: int):
    out = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{t:.2f}", "-i", str(mp4),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True)
    if out.returncode != 0 or len(out.stdout) < w * h:
        return None
    return np.frombuffer(out.stdout[:w * h], dtype=np.uint8).reshape(h, w)


def content_width(img) -> float | None:
    """Fraction of the width between the first and last column with content."""
    cols = (img > DARK).sum(axis=0)
    live = np.nonzero(cols > img.shape[0] * 0.01)[0]
    if live.size == 0:
        return None
    return float(live[-1] - live[0] + 1) / img.shape[1]


def main() -> int:
    rows = []
    for path in sorted(DATA.glob("pilot*/exports/*.json")):
        mp4 = path.with_suffix(".mp4")
        if not mp4.exists():
            continue
        d = json.loads(path.read_text(encoding="utf-8"))
        pl = d.get("dynamic_plan") or {}
        shots = [s for s in (pl.get("shots") or [])
                 if isinstance(s, dict) and s.get("composition") == "fit"]
        if not shots:
            continue
        # The delivered frame is 1080x1920; inside it the source band is
        # 1080 wide. Measuring the OUTPUT means measuring what a viewer sees.
        for shot in shots[:2]:
            t = (float(shot["t0"]) + float(shot["t1"])) / 2.0
            img = frame_at(mp4, t, 1080, 1920)
            if img is None:
                continue
            band = img[656:656 + 608, :]      # the 16:9 source band, centred
            got = content_width(band)
            if got is not None:
                rows.append((path.parent.parent.name, path.stem, round(got, 3)))
    if not rows:
        print("FAIL: no fit frames measured")
        return 1

    print(f"cadre `fit` masurate: {len(rows)}  pe {len({(p, c) for p, c, _ in rows})} clipuri")
    print()
    by = {}
    for project, _clip, got in rows:
        by.setdefault(project, []).append(got)
    for project, got in sorted(by.items()):
        got = sorted(got)
        print(f"  {project}: median {got[len(got) // 2]:.2f} "
              f"min {got[0]:.2f} max {got[-1]:.2f}   n={len(got)}")
    print()
    every = sorted(g for _p, _c, g in rows)
    for cut in (0.80, 0.75, 0.70, 0.60):
        n = sum(1 for g in every if g <= cut)
        print(f"  cadre care incap intr-o fereastra de {cut:.0%} din latime: "
              f"{n:3}/{len(every)}  ({100 * n / len(every):3.0f}%)")
    print()
    print("o fereastra 4:5 pastreaza 864/1920 = 45% din latimea sursei")
    return 0


if __name__ == "__main__":
    sys.exit(main())
