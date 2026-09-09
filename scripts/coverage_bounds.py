"""Pass 1: a CONSERVATIVE limit for every part on every relevant frame.

    python scripts/coverage_bounds.py pilotf81b b23c14c41495
    python scripts/coverage_bounds.py pilotf81b b23c14c41495 --extremes

WHY TWO PASSES. Codex, after the calibration showed the proxy annotation was
displaced by up to six times its own declared tolerance: "toate cadrele
relevante trebuie reinspectate, dar nu cer peste 1.000 de citiri precise." So:

  1. THIS FILE — every relevant frame looked at again on the SOURCE, whole
     objects visible and the candidate region nowhere on screen, recording a
     bound that is certainly true rather than a precise position. "The right
     margin is before 0.70" is a measurement; inventing 0.643 to go with it is
     not. Codex: "Limita trebuie confirmata pe sursa, nu mostenita din cutia
     gresita."
  2. `edge_calibration.py` — precise INTERVALS, only for the margins that turn
     out to set the extremes of the union, and for the ones that are unclear.
     "Ce nu poate influenta reuniunea, precizia suplimentara nu cumpara nimic."

THE BOUNDS POINT OUTWARD, because that is the direction a region has to be
generous in. `left` and `top` are lower bounds — the object does not extend
past them toward the frame's origin — and `right` and `bottom` are upper ones.
A conservative box is (left, right, top, bottom) read that way, and the union of
them over a phase's frames is a region that contains every position the
observations permit.

WHICH IS NOT THE SAME AS KEEPING THE SUBJECT, and Codex was explicit about it:
"Promisiunea ramane conditionata: «contine toate pozitiile permise de
observatiile inregistrate». Nu inseamna inca «pastreaza subiectul pe intreaga
durata»." Frames nobody looked at are not covered by a union of the ones who
were, and `--extremes` names the margins the answer actually rests on.

A PART WITH NO BOUND IS NOT A PART WITH NO EXTENT. Every frame/part the phase
declares it keeps must appear here or in `NOT_VISIBLE`, and the run refuses
otherwise — a frame nobody looked at must never read as a frame with nothing in
it.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))

DATA = Path(os.environ.get("CLIPFORGE_DATA_DIR") or (_ROOT / "data")) / "clipper"

#: `{(proxy frame, part): (left, right, top, bottom)}` — conservative limits in
#: fractions of the frame, read on the 2560x1440 source with the candidate
#: hidden. NOT positions: each is a bound the object is known not to pass.
BOUNDS: dict[tuple[int, str], tuple[float, float, float, float]] = {
    # --- `screen` phase, read whole on the source with no boxes drawn -------
    # THE HEAD'S RIGHT EDGE IS THE STORY HERE. Every close-up puts it between
    # 0.785 and 0.81 while the candidate's right edge is 0.755, and the first
    # proxy annotation had it at 0.790 — closer than the [0.730, 0.750] this
    # session then "corrected" it to off a crop too narrow to show the contour.
    (2395, "watch"): (0.425, 0.675, 0.150, 1.000),
    (2395, "screen"): (0.450, 0.655, 0.295, 0.655),
    (2395, "hand"): (0.230, 0.710, 0.165, 1.000),
    (2395, "face"): (0.590, 0.790, 0.130, 1.000),

    (2398, "watch"): (0.480, 0.730, 0.140, 1.000),
    (2398, "screen"): (0.500, 0.710, 0.270, 0.695),
    (2398, "hand"): (0.285, 0.755, 0.180, 1.000),
    (2398, "face"): (0.615, 0.810, 0.155, 1.000),

    (2400, "watch"): (0.460, 0.690, 0.140, 1.000),
    (2400, "screen"): (0.485, 0.670, 0.230, 0.640),
    (2400, "hand"): (0.270, 0.720, 0.105, 1.000),
    (2400, "face"): (0.600, 0.800, 0.145, 1.000),

    (2401, "watch"): (0.450, 0.685, 0.140, 1.000),
    (2401, "screen"): (0.470, 0.665, 0.285, 0.675),
    (2401, "hand"): (0.240, 0.720, 0.150, 1.000),
    (2401, "face"): (0.600, 0.800, 0.140, 1.000),

    (2408, "watch"): (0.425, 0.665, 0.095, 1.000),
    (2408, "screen"): (0.440, 0.645, 0.230, 0.645),
    (2408, "hand"): (0.215, 0.695, 0.100, 1.000),
    (2408, "face"): (0.595, 0.785, 0.110, 1.000),

    (2413, "watch"): (0.410, 0.645, 0.110, 1.000),
    (2413, "screen"): (0.430, 0.625, 0.240, 0.595),
    (2413, "hand"): (0.205, 0.675, 0.115, 1.000),
    (2413, "face"): (0.575, 0.785, 0.115, 1.000),

    # The withdrawal. The watch is leaving to the left and the face is fully
    # back and central, so this frame sets the phase's LEFT extreme and none of
    # its right one.
    (2424, "watch"): (0.100, 0.370, 0.465, 0.800),
    (2424, "hand"): (0.000, 0.410, 0.545, 1.000),
    (2424, "face"): (0.430, 0.645, 0.210, 0.810),
}

#: `{(proxy frame, part)}` looked at and not present in the picture. Distinct
#: from absent from `BOUNDS`, which means nobody looked.
NOT_VISIBLE: set[tuple[int, str]] = {
    # The display is smeared by the withdrawal movement; a box round an
    # illegible screen would be a guess, and a guess is not a bound.
    (2424, "screen"),
}

#: Frames excluded from the pass entirely, with the reason. `2425` is past the
#: clip's end; a bound read there would constrain a region for a picture no
#: viewer sees.
EXCLUDED: dict[int, str] = {2425: "outside_the_clip"}

MISSING = "no_bound_recorded_and_no_declared_absence"


def _module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _relevant(wa, bpr, clip: str) -> list[tuple[int, str, str]]:
    """`[(frame, part, phase)]` for every part a phase declares it keeps."""
    windows = [(n, float(a), float(b))
               for n, a, b, _, _ in (bpr.PHASES.get(clip) or [])]
    out: list[tuple[int, str, str]] = []
    for table in (wa.WATCH, wa.HOLDOUT, wa.FRESH):
        for frame in sorted(table):
            if frame in EXCLUDED:
                continue
            t = frame / bpr.PROXY_FPS
            phase = next((n for n, a, b in windows if a <= t < b), None)
            keeps = bpr.PHASE_KEEPS.get(phase) if phase else None
            if not keeps:
                continue
            for part in keeps:
                if (frame, part) not in out:
                    out.append((frame, part, phase))
    return out


def _union(rows: list[tuple[float, float, float, float]]):
    return (min(r[0] for r in rows), max(r[1] for r in rows),
            min(r[2] for r in rows), max(r[3] for r in rows))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project")
    ap.add_argument("clip")
    ap.add_argument("--extremes", action="store_true",
                    help="name the margins that SET each phase's union, which "
                         "are the only ones a precise interval would change")
    args = ap.parse_args()

    wa = _module("wa", _ROOT / "scripts" / "watch_annotations.py")
    bpr = _module("bpr", _ROOT / "scripts" / "build_phase_regions.py")

    want = _relevant(wa, bpr, args.clip)
    have = [(f, p, ph) for f, p, ph in want if (f, p) in BOUNDS]
    absent = [(f, p, ph) for f, p, ph in want if (f, p) in NOT_VISIBLE]
    missing = [(f, p, ph) for f, p, ph in want
               if (f, p) not in BOUNDS and (f, p) not in NOT_VISIBLE]

    # THE DENOMINATOR FIRST. A coverage pass that has read nine parts of nine
    # hundred must not print a result that reads like a finished one.
    print(f"{args.clip}  {len(want)} frame/part pairs the phases declare they "
          f"keep, over {len({f for f, _, _ in want})} frames")
    print(f"  {len(have)} bounded, {len(absent)} looked at and not present, "
          f"{len(missing)} NOT LOOKED AT")
    if missing:
        by_phase: dict[str, int] = {}
        for _, _, ph in missing:
            by_phase[ph] = by_phase.get(ph, 0) + 1
        print(f"  {MISSING}: {by_phase}")
        print(f"  first few: {missing[:8]}")

    per_phase: dict[str, dict[str, list]] = {}
    for frame, part, phase in have:
        per_phase.setdefault(phase, {}).setdefault(part, []).append(
            (frame, BOUNDS[(frame, part)]))

    out: dict[str, Any] = {"schema": "clipper_coverage_bounds_v1",
                           "clip": args.clip, "declared": len(want),
                           "bounded": len(have), "not_present": len(absent),
                           "not_looked_at": len(missing), "phases": {}}
    for phase, parts in sorted(per_phase.items()):
        boxes = [b for rows in parts.values() for _, b in rows]
        u = _union(boxes)
        out["phases"][phase] = {
            "union": {"left": u[0], "right": u[1], "top": u[2], "bottom": u[3]},
            "parts": {p: len(rows) for p, rows in sorted(parts.items())}}
        print(f"  {phase:<11} union L{u[0]:.3f} R{u[1]:.3f} "
              f"T{u[2]:.3f} B{u[3]:.3f}  from "
              + ", ".join(f"{p} x{len(rows)}" for p, rows in sorted(parts.items())))
        if not args.extremes:
            continue
        for side, idx, pick in (("left", 0, min), ("right", 1, max),
                                ("top", 2, min), ("bottom", 3, max)):
            best = pick(((b[idx], f, p) for p, rows in parts.items()
                         for f, b in rows), key=lambda r: r[0])
            print(f"    {side:<7} set by f{best[1]} {best[2]} at {best[0]:.3f}")
            out["phases"][phase].setdefault("extremes", {})[side] = {
                "frame": best[1], "part": best[2], "at": best[0]}

    dest = (DATA / args.project / "phase_regions"
            / f"{args.clip}.coverage_bounds.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"  written to {dest}")
    # An incomplete pass is not a smaller complete one.
    return 0 if not missing else 2


if __name__ == "__main__":
    raise SystemExit(main())
