"""Candidate facecam rects with labels, and what each feature is worth.

Twelve rules over the gradient have failed on this edge, and the last
measurement said why: two of the labelled sources have BORDERLESS facecams, so
there is no edge to write a rule about. What is left is to find the inset from
the FACE rather than from its frame, which means asking which of the things we
can already measure actually separate a real inset from a phantom.

This builds the data for that question and answers it one feature at a time. It
does not train anything — a classifier over features that do not separate is
just a slower way to be wrong.

TWO THINGS ABOUT THE DATA, both of which decide whether any of this is real:

  * Candidates from one source ARE NOT INDEPENDENT. Eight windows of the same
    stream share a layout, a streamer and a camera. Treating them as eight
    samples is how you get a classifier that has learned which source it is
    looking at and a score that means nothing — the exact mistake this codebase
    made by fitting every threshold to one Minecraft co-stream. Everything here
    is grouped by source, and any model trained on it must be validated
    leave-one-source-out.

  * With nine sources the effective sample size is NINE, whatever the row count
    says. That is small enough that a feature separating cleanly here is a
    hypothesis, not a result.
"""
from __future__ import annotations

import glob
import sys
from pathlib import Path

import cv2
import numpy as np

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))
sys.path.insert(0, str(_REPO / "server"))

from measure_inset_border import border_coverage, border_persistence  # noqa: E402
from services.clipper import content_type as ct                       # noqa: E402
from services.clipper.content_geom import (                           # noqa: E402
    corner_proximity,
    make_rect,
    median_rect,
    rect_area_frac,
    rect_centre,
)
from services.clipper.signals import detect_faces                     # noqa: E402

# (project, label, [true facecam centres in proxy px], from_frac, to_frac).
# An empty centre list means the source HAS no facecam, so every candidate from
# it is negative — which is where most of the negative data comes from.
SOURCES = [
    ("39c89ae2e16e", "EARLY STREAM", [(92, 156)], 0.14, 1.0),
    ("2c8af11153a3", "moistcr1tikal", [(42, 151)], 0.0, 1.0),
    ("slice4h00test", "Minecraft 4h", [(61, 34), (401, 36)], 0.15, 1.0),
    ("2d3375ee3420", "Minecraft 12m", [(61, 34), (401, 36)], 0.0, 1.0),
    ("5f4c2770254d", "Jynxzi", [(79, 166)], 0.40, 1.0),
    ("0c9685df852b", "gym (fullscreen)", [], 0.0, 1.0),
    ("f81b86d27877", "go ghost (edited)", [], 0.0, 1.0),
    ("ee0e599b3ecb", "apartament (edited)", [], 0.0, 1.0),
    ("6b3844793c6a", "Jensen Huang (edited)", [], 0.0, 1.0),
]

WINDOWS = 6          # per source, so one bad stretch does not define a source
_MATCH_TOL = 0.10    # frame fractions; a candidate this close to a true centre is it


def _frames(pid: str, lo: float, hi: float):
    files = sorted(glob.glob(str(_REPO / "data" / "clipper" / pid / "frames" / "*.jpg")))
    a, b = int(len(files) * lo), int(len(files) * hi)
    grays = []
    for f in files[a:b]:
        img = cv2.imread(f)
        if img is not None:
            grays.append(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    return grays


def _candidates(grays, faces, fw, fh, cap=0.30):
    """Every rect the capped detector would consider, before any gate."""
    gx, gy = ct._step_profiles(grays)
    out = []
    for group in ct._face_groups(faces, fw, fh):
        hits = len({i for i, _ in group})
        rate = hits / max(1, len(faces))
        if hits < ct._FACECAM_MIN_HITS or rate < ct._FACECAM_MIN_RATE:
            continue
        med = median_rect([b for _, b in group])
        if med is None:
            continue
        cx, cy = rect_centre(med)
        hw, hh = med["w"] / 2.0, med["h"] / 2.0
        ox, oy = min(hw * 8.0, fw * cap), min(hh * 8.0, fh * cap)
        ix, iy = hw * 1.5, hh * 1.5
        rows = slice(max(0, int(cy - hh)), max(1, int(cy + hh)))
        cols = slice(max(0, int(cx - hw)), max(1, int(cx + hw)))
        cp, rp = gx[rows, :].mean(axis=0), gy[:, cols].mean(axis=1)
        x0 = ct._snap_edge(cp, cx, ix, ox, fw, forward=False)
        x1 = ct._snap_edge(cp, cx, ix, ox, fw, forward=True)
        y0 = ct._snap_edge(rp, cy, iy, oy, fh, forward=False)
        y1 = ct._snap_edge(rp, cy, iy, oy, fh, forward=True)
        if x1 - x0 < 10 or y1 - y0 < 10:
            continue
        rect = make_rect(x0, y0, x1 - x0, y1 - y0)

        centres = [rect_centre(b) for _, b in group]
        drift = max(
            (max(c[0] for c in centres) - min(c[0] for c in centres)) / max(1.0, rect["w"]),
            (max(c[1] for c in centres) - min(c[1] for c in centres)) / max(1.0, rect["h"]),
        )
        out.append({
            "rect": rect,
            "face": med,
            # What we can already measure, plus the two from today.
            "hit_rate": rate,
            "drift": min(1.0, drift),
            "area": rect_area_frac(rect, fw, fh),
            "aspect": rect["w"] / max(1.0, rect["h"]),
            "corner": corner_proximity(rect, fw, fh),
            "cover": border_coverage(rect, gx, gy, fw, fh),
            "persist": border_persistence(rect, grays, fw, fh),
            # Found from the FACE rather than the frame, which is the direction
            # the border measurement pointed at: a real facecam frames a head at
            # a fairly consistent scale, a phantom is grown to arbitrary bounds.
            "h_over_face": rect["h"] / max(1.0, med["h"]),
            "w_over_face": rect["w"] / max(1.0, med["w"]),
        })
    return out


def build() -> list[dict]:
    rows: list[dict] = []
    for pid, name, truth, lo, hi in SOURCES:
        grays = _frames(pid, lo, hi)
        if len(grays) < ct._MIN_RANGE_FRAMES * 2:
            print(f"  {name}: too few frames ({len(grays)})", file=sys.stderr)
            continue
        fh, fw = grays[0].shape[:2]
        per = max(ct._MIN_RANGE_FRAMES, len(grays) // WINDOWS)
        for w in range(WINDOWS):
            chunk = grays[w * per:(w + 1) * per]
            if len(chunk) < ct._MIN_RANGE_FRAMES:
                continue
            faces = [[make_rect(*b) for b in detect_faces(g)] for g in chunk]
            for cand in _candidates(chunk, faces, fw, fh):
                cx, cy = rect_centre(cand["rect"])
                cand["label"] = int(any(
                    abs(cx - tx) <= fw * _MATCH_TOL and abs(cy - ty) <= fh * _MATCH_TOL
                    for tx, ty in truth))
                cand["source"] = name
                rows.append(cand)
    return rows


FEATURES = ["hit_rate", "drift", "area", "aspect", "corner", "cover",
            "persist", "h_over_face", "w_over_face"]


def report(rows: list[dict]) -> None:
    pos = [r for r in rows if r["label"] == 1]
    neg = [r for r in rows if r["label"] == 0]
    print(f"\n{len(rows)} candidates from {len({r['source'] for r in rows})} sources: "
          f"{len(pos)} facecam, {len(neg)} phantom\n")

    print(f"{'feature':14s} {'facecam med':>12s} {'phantom med':>12s} "
          f"{'overlap':>8s}  separation")
    for key in FEATURES:
        a = np.array([r[key] for r in pos], dtype=float)
        b = np.array([r[key] for r in neg], dtype=float)
        if a.size == 0 or b.size == 0:
            continue
        # Share of the two ranges that overlap: 0 is a clean split, 1 is useless.
        lo = max(min(a), min(b))
        hi = min(max(a), max(b))
        span = max(max(a), max(b)) - min(min(a), min(b))
        overlap = max(0.0, hi - lo) / span if span > 0 else 1.0
        best = max(
            (np.mean((a >= t) == True) + np.mean((b < t) == True)) / 2
            for t in np.linspace(min(span and lo or 0, min(b)), max(max(a), max(b)), 60)
        )
        print(f"{key:14s} {np.median(a):12.3f} {np.median(b):12.3f} "
              f"{overlap:8.2f}  {best:.0%} correct at its best single threshold")


if __name__ == "__main__":
    data = build()
    report(data)
    out = _REPO / "data" / "facecam_candidates.json"
    import json
    out.write_text(json.dumps(
        [{k: (v if k not in ("rect", "face") else v) for k, v in r.items()} for r in data],
        indent=1), encoding="utf-8")
    print(f"\nwrote {out.relative_to(_REPO)} — grouped by source, for "
          f"leave-one-source-out training")
