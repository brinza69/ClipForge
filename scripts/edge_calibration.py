"""What the proxy reading of a margin was worth, measured against the source.

    python scripts/edge_calibration.py pilotf81b b23c14c41495

WHAT THIS IS. Codex, after the fresh lot reported four clips that the source
then failed to confirm: "Urmatorul lot de lucru trebuie sa fie mic si de
calibrare a adnotarii. Remasoara cele patru cadre semnalate si inca 4-6 exemple
reprezentative de muchii ambigue, inclusiv unele care au primit `held`." This is
that batch — ten margins, re-measured on the 2560x1440 source with
`source_crop.py --edge`, with the crop framed on the margin alone and the
region's own edge nowhere on screen.

AND AN INTERVAL IS ONLY A MEASUREMENT IF IT COVERS THE WHOLE CONTOUR. Codex,
reading the saved crop for f2400 after this file first claimed [0.730, 0.750]:
"nota justifica intervalul prin conturul vizibil la inaltimea 0.20-0.35. In
cadrul salvat, capul se bombeaza mai la dreapta in jurul inaltimii 0.43, aparent
dincolo de 0.750. Intervalul unei portiuni de contur nu delimiteaza automat
marginea dreapta a intregului cap." That is the same failure the corpus keeps
finding in new places: a partial measurement wearing the whole answer's name.
The three `face.right` rows were each justified from the one height band where
the silhouette is legible, and the head's widest point is not in that band. They
now carry `whole_contour: False`, their verdict is `unmeasured` rather than
`held`, and the exit code says so.

EACH MARGIN IS AN INTERVAL, NOT A POINT. Codex again: "Pentru fiecare margine
dificila, inregistreaza intervalul in care poate fi localizata." A defocused
knuckle or a white strap against a pale wall does not have a pixel where it
stops, and writing one down invents a precision the picture does not carry. The
interval is what was seen; the verdict follows from whether it CROSSES the
region's edge, which is a fact about this observation and not a new global
tolerance. "O clasificare pe tipuri de muchii poate ajuta organizarea — dar nu
justifica singura o noua toleranta numerica. Nu inlocui global +/-0,01 cu
+/-0,05 doar fiindca a doua valoare acopera discrepantele constatate."

WHAT IT FOUND. Eight of the ten margins change verdict, and three of those
change to `unmeasured` because they were never measurements at all. The proxy readings are
displaced from the source intervals by 10 to 154 source pixels against a
declared tolerance of +/-25.6 px horizontally and +/-14.4 vertically — six
times the tolerance at worst — and in BOTH DIRECTIONS. f2357's hand was
annotated 0.045 too LOW, cutting off the top of the hand the box was drawn to
contain; f2362's was 0.045 too high, into sky. A one-directional error would
have been a bias to subtract. This is not that: it is noise wider than the
tolerance, and it is why four "clips" and several "indeterminate" verdicts were
neither.

AND IT IS NOT A LICENCE TO CALL THE REGIONS GOOD. Five of the ten come out
`held`, two straddle and three measure nothing, and five held margins on five
frames is not a verification. What it establishes is that the INSTRUMENT was wrong, so every
verdict computed with it — in both directions — has to be redone.
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

#: `{(proxy frame, part, side): row}`. `proxy` is what the annotation holds and
#: `source` is the interval the margin was located in on the 2560x1440 frame.
#: `against` says what the edge is set against, because that — not the part — is
#: what makes a margin hard.
CALIBRATION: dict[tuple[int, str, str], dict[str, Any]] = {
    # --- the four the fresh lot called clips -------------------------------
    (2362, "hand", "top"): {
        "proxy": 0.030, "source": (0.072, 0.080),
        "against": "bright_sky", "kind": "defocused_knuckle",
        "note": "the annotated line is in open sky; the first hand pixel is a "
                "red knuckle whose top is itself smeared over ~12 source px"},
    (2367, "hand", "top"): {
        "proxy": 0.045, "source": (0.072, 0.082),
        "against": "bright_sky", "kind": "defocused_knuckle",
        "note": "two knuckles at different heights, both soft; Codex read the "
                "same frame and also declined to give it a point"},
    (2400, "face", "right"): {
        "proxy": 0.790, "source": (0.730, 0.750), "whole_contour": False,
        "against": "bokeh_highlights", "kind": "head_silhouette",
        "note": "the interval is the silhouette at y 0.20-0.35 ONLY. Codex found "
                "the head bulging further right around y 0.43, apparently past "
                "0.750, and below y 0.5 the boundary is inside the bokeh. This "
                "does not delimit the right margin of the whole head"},
    (2403, "face", "right"): {
        "proxy": 0.775, "source": (0.730, 0.750), "whole_contour": False,
        "against": "bokeh_highlights", "kind": "head_silhouette",
        "note": "same structure as f2400 a tenth of a second later, and read the "
                "same partial way — the band where the silhouette is legible, "
                "not the widest point of the head"},
    # --- ambiguous margins that were NOT called clips ----------------------
    (2414, "watch", "top"): {
        "proxy": 0.075, "source": (0.110, 0.130),
        "against": "pale_building_wall", "kind": "white_strap",
        "note": "the hard case: a white strap against a pale wall, not against "
                "sky. The annotated line is on the WALL"},
    (2411, "watch", "top"): {
        "proxy": 0.085, "source": (0.110, 0.130),
        "against": "pale_building_wall", "kind": "white_strap",
        "note": "same edge three tenths earlier, same displacement"},
    (2279, "face", "top"): {
        "proxy": 0.130, "source": (0.142, 0.158),
        "against": "bright_sky", "kind": "black_durag",
        "note": "the easiest of the hard ones — black on blue — and still 0.012 "
                "to 0.028 out"},
    (2405, "face", "right"): {
        "proxy": 0.755, "source": (0.725, 0.745), "whole_contour": False,
        "against": "bokeh_highlights", "kind": "head_silhouette",
        "note": "sat at exactly the region edge on the proxy reading, which is "
                "the position most likely to be an artefact of it; read from "
                "the same partial band as f2400 and f2403"},
    # --- controls: margins the tolerance verdict called `held` -------------
    (2357, "hand", "top"): {
        "proxy": 0.235, "source": (0.185, 0.200),
        "against": "bright_sky", "kind": "defocused_knuckle",
        "note": "THE ERROR RUNS THE OTHER WAY HERE. The box was drawn 0.045 "
                "BELOW the top of the hand, cutting off what it was drawn to "
                "contain. Held either way, and the reason this is not a bias "
                "that could simply be subtracted"},
    (2251, "face", "top"): {
        "proxy": 0.155, "source": (0.162, 0.178),
        "against": "bright_sky", "kind": "black_durag",
        "note": "an easy edge on a wide shot, and the closest agreement in the "
                "batch at 0.007-0.023"},
}

#: The regions' own edges are read from the frozen candidate rather than typed
#: here, so this file cannot drift from the thing it is judging.
SIDE_AXIS = {"left": "x", "right": "x", "top": "y", "bottom": "y"}


def _annotations():
    spec = importlib.util.spec_from_file_location(
        "wa", str(_ROOT / "scripts" / "watch_annotations.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _region_edge(region: dict, side: str, sw: int, sh: int) -> float:
    """The region's own boundary on that side, as a fraction of the frame."""
    if side == "left":
        return region["x"] / sw
    if side == "right":
        return (region["x"] + region["w"]) / sw
    if side == "top":
        return region["y"] / sh
    return (region["y"] + region["h"]) / sh


#: A margin whose interval was read from part of the contour only.
UNMEASURED = "unmeasured"


def verdict(interval: tuple[float, float], edge: float, side: str,
            whole_contour: bool = True) -> str:
    """`held`, `clipped` or `indeterminate` from an interval and a boundary.

    No tolerance appears here. The uncertainty IS the interval, measured on the
    frame it belongs to, and the verdict is whether the region's edge falls
    inside it — which is the honest shape of the question. A margin whose whole
    interval is on the safe side is held; one whose whole interval is outside is
    clipped; one the boundary passes through is neither, and says so.
    """
    if not whole_contour:
        # The extreme of the object may be somewhere this reading never looked.
        # `held` here would be a claim about the whole head from a measurement
        # of one band of it.
        return UNMEASURED
    lo, hi = interval
    outside_is_less = side in ("left", "top")
    if outside_is_less:
        if lo >= edge:
            return "held"
        if hi < edge:
            return "clipped"
    else:
        if hi <= edge:
            return "held"
        if lo > edge:
            return "clipped"
    return "indeterminate"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project")
    ap.add_argument("clip")
    args = ap.parse_args()

    wa = _annotations()
    path = (DATA / args.project / "phase_regions"
            / f"{args.clip}.regions.json")
    if not path.exists():
        print(f"REFUSED: no frozen candidate at {path}")
        return 2
    blob = json.loads(path.read_text(encoding="utf-8"))
    sw, sh = int(blob["src_w"]), int(blob["src_h"])
    regions = {p["phase"]: p.get("region") for p in blob["phases"]}
    windows = [(p["phase"], float(p["t0"]), float(p["t1"]))
               for p in blob["phases"]]

    rows: list[dict] = []
    print(f"{'frame':>7} {'margin':<12} {'proxy':>7} {'source interval':>17} "
          f"{'displaced':>18} {'region':>7}  {'tolerance':<14} interval")
    for (frame, part, side), row in sorted(CALIBRATION.items()):
        t = frame / 10.0
        phase = next((n for n, a, b in windows if a <= t < b), None)
        region = regions.get(phase) if phase else None
        if region is None:
            print(f"  f{frame}: REFUSED — no region for {phase}")
            return 2
        edge = _region_edge(region, side, sw, sh)
        span = sw if SIDE_AXIS[side] == "x" else sh
        lo, hi = row["source"]
        # What the tolerance-based rule said, recomputed here from the same
        # numbers rather than quoted from the other script's output.
        tol = wa.MARGIN_UNCERTAINTY * span
        p = row["proxy"]
        over = (edge - p) if side in ("left", "top") else (p - edge)
        was = ("clipped" if over * span - tol > 1e-6
               else "indeterminate" if over * span + tol > 1e-6 else "held")
        whole = bool(row.get("whole_contour", True))
        now = verdict((lo, hi), edge, side, whole)
        d_lo, d_hi = (lo - p) * span, (hi - p) * span
        rows.append({"frame": frame, "part": part, "side": side, "phase": phase,
                     "proxy": p, "source": [lo, hi], "region_edge": edge,
                     "whole_contour": whole,
                     "tolerance_verdict": was, "interval_verdict": now,
                     "displacement_px": [round(d_lo, 1), round(d_hi, 1)],
                     "against": row["against"], "kind": row["kind"]})
        print(f"{'f' + str(frame):>7} {part + '.' + side:<12} {p:7.3f} "
              f"{f'[{lo:.3f}, {hi:.3f}]':>17} "
              f"{f'{d_lo:+.1f}..{d_hi:+.1f}px':>18} {edge:7.3f}  "
              f"{was:<14} {now}")

    changed = [r for r in rows if r["tolerance_verdict"] != r["interval_verdict"]]
    partial = [r for r in rows if not r["whole_contour"]]
    worst = max(rows, key=lambda r: max(abs(v) for v in r["displacement_px"]))
    print(f"\n  {len(rows)} margins re-measured on the source, "
          f"{len(changed)} change verdict")
    for r in changed:
        print(f"    f{r['frame']} {r['part']}.{r['side']:<7} "
              f"{r['tolerance_verdict']} -> {r['interval_verdict']}")
    print(f"  worst displacement f{worst['frame']} {worst['part']}."
          f"{worst['side']}: {worst['displacement_px']} source px against a "
          f"tolerance of +/-{wa.MARGIN_UNCERTAINTY * sw:.1f}px horizontally, "
          f"+/-{wa.MARGIN_UNCERTAINTY * sh:.1f} vertically")
    signs = {("under" if r["displacement_px"][0] > 0 else "over") for r in rows}
    print(f"  displacement directions present: {sorted(signs)}"
          + ("  — BOTH, so it is noise and not a bias to subtract"
             if len(signs) > 1 else ""))

    if partial:
        names = [f"f{r['frame']} {r['part']}.{r['side']}" for r in partial]
        print(f"  {len(partial)} margin(s) were read from PART of the contour "
              f"and measure nothing about the object's extreme: {names}")
    out = {"schema": "clipper_edge_calibration_v1", "clip": args.clip,
           "margins": rows, "changed": len(changed),
           "partial_contour": len(partial)}
    dest = (DATA / args.project / "phase_regions"
            / f"{args.clip}.edge_calibration.json")
    dest.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"  written to {dest}")
    # A calibration that changes verdicts is not a pass: it says the verdicts
    # computed with the old instrument have to be redone.
    return 0 if not (changed or partial) else 2


if __name__ == "__main__":
    raise SystemExit(main())
