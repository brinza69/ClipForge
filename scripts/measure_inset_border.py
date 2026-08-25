"""Does an inset's BORDER separate it from a phantom rect? Measure first.

The gate is not built yet and must not be until this says there is something to
gate on. Eleven approaches have failed on this edge; the ones that failed
fastest were the ones that reasoned about a signal instead of looking at it.

Two candidate measures, both about the border being a real composited boundary
rather than a coincidence of texture:

  coverage    - what FRACTION of the rect's perimeter actually has a gradient
                ridge on it. An inset's border runs all the way round; a rect
                grown around a face stops wherever something happened to be.
  persistence - is the ridge at the SAME pixel in every frame. A composited
                border is pixel-locked; a scene edge drifts.

Printed per source next to the truth, so the question "do these separate" is
answered by looking at two columns rather than by an argument.
"""
from __future__ import annotations

import sys

import numpy as np

from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))
sys.path.insert(0, str(_REPO / "server"))

import score_facecam as F                                          # noqa: E402
from services.clipper import content_type as ct                    # noqa: E402
from services.clipper.content_geom import median_rect, rect_centre  # noqa: E402


def edge_line(profile: np.ndarray, at: int, span: int = 3) -> float:
    """How much the step at `at` stands out from its immediate neighbourhood."""
    lo, hi = max(0, at - span), min(profile.shape[0], at + span + 1)
    win = profile[lo:hi]
    if win.size < 3:
        return 0.0
    med = float(np.median(win))
    return float(profile[min(at, profile.shape[0] - 1)]) / max(med, 1e-6)


def border_coverage(rect, gx, gy, fw, fh, bar=1.5, samples=24):
    """Fraction of the rect's perimeter that sits on a gradient ridge.

    Each side is sampled at `samples` points ALONG it; at every point the
    profile perpendicular to that side is checked for a step. A side that runs
    off the frame counts as covered — the frame edge is a real boundary.
    """
    x0, y0 = int(rect["x"]), int(rect["y"])
    x1, y1 = x0 + int(rect["w"]), y0 + int(rect["h"])
    hits = total = 0

    for x_at in (x0, x1):                       # vertical sides: read gx columns
        if x_at <= 1 or x_at >= fw - 2:
            hits += samples
            total += samples
            continue
        for k in range(samples):
            row = int(y0 + (y1 - y0) * (k + 0.5) / samples)
            if not (0 <= row < gx.shape[0]):
                continue
            total += 1
            hits += edge_line(gx[row, :], x_at) >= bar

    for y_at in (y0, y1):                       # horizontal sides: gy rows
        if y_at <= 1 or y_at >= fh - 2:
            hits += samples
            total += samples
            continue
        for k in range(samples):
            col = int(x0 + (x1 - x0) * (k + 0.5) / samples)
            if not (0 <= col < gy.shape[1]):
                continue
            total += 1
            hits += edge_line(gy[:, col], y_at) >= bar

    return hits / max(1, total)


def border_persistence(rect, grays, fw, fh, bar=1.5):
    """Share of FRAMES in which the rect's vertical sides are a step.

    A composited border is at the same pixel every frame. A scene edge that
    happens to sit there in the averaged profile need not be there in any
    particular one.
    """
    x0 = int(rect["x"])
    x1 = x0 + int(rect["w"])
    inside = [x for x in (x0, x1) if 2 < x < fw - 3]
    if not inside:
        return 1.0                              # both sides are the frame edge
    seen = 0
    for gray in grays:
        col = np.abs(np.diff(gray.astype(np.float32), axis=1)).mean(axis=0)
        if any(edge_line(col, x) >= bar for x in inside):
            seen += 1
    return seen / max(1, len(grays))


if __name__ == "__main__":
    print(f"{'source':34s} {'want':>4s} {'rect':>16s} {'cover':>6s} {'persist':>8s}")
    for pid, name, want, lo, hi in F.SOURCES:
        grays, faces = F.load(pid, lo, hi)
        if len(grays) < ct._MIN_RANGE_FRAMES:
            continue
        fh, fw = grays[0].shape[:2]
        gx, gy = ct._step_profiles(grays)

        # The rects the CAPPED reach produces — the ones a fixed detector would
        # be judging, not the runaway ones today's gates throw away.
        for group in ct._face_groups(faces, fw, fh):
            hits = len({i for i, _ in group})
            if hits < ct._FACECAM_MIN_HITS or hits / len(faces) < ct._FACECAM_MIN_RATE:
                continue
            med = median_rect([b for _, b in group])
            if med is None:
                continue
            cx, cy = rect_centre(med)
            hw, hh = med["w"] / 2.0, med["h"] / 2.0
            ox = min(hw * ct._FACECAM_OUTER * 2, fw * 0.30)
            oy = min(hh * ct._FACECAM_OUTER * 2, fh * 0.30)
            ix, iy = hw * ct._FACECAM_INNER * 2, hh * ct._FACECAM_INNER * 2
            rows = slice(max(0, int(cy - hh)), max(1, int(cy + hh)))
            cols = slice(max(0, int(cx - hw)), max(1, int(cx + hw)))
            cp = gx[rows, :].mean(axis=0)
            rp = gy[:, cols].mean(axis=1)
            r = {
                "x": ct._snap_edge(cp, cx, ix, ox, fw, forward=False),
                "y": ct._snap_edge(rp, cy, iy, oy, fh, forward=False),
            }
            r["w"] = ct._snap_edge(cp, cx, ix, ox, fw, forward=True) - r["x"]
            r["h"] = ct._snap_edge(rp, cy, iy, oy, fh, forward=True) - r["y"]
            if r["w"] < 8 or r["h"] < 8:
                continue
            cov = border_coverage(r, gx, gy, fw, fh)
            per = border_persistence(r, grays, fw, fh)
            shape = "{}x{}@{},{}".format(r["w"], r["h"], r["x"], r["y"])
            print(f"{name[:34]:34s} {want:4d} {shape:>16s} {cov:6.2f} {per:8.2f}")
