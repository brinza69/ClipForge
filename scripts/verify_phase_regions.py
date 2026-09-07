"""Do the FROZEN regions hold what they never saw.

    python scripts/verify_phase_regions.py pilotf81b 6053a598cf06

THE ONLY NON-CIRCULAR TEST. `build_phase_regions` makes a region from the
confirmed observations of a phase, so of course it contains them. This takes
frames that took NO part in that — chosen at the midpoints between construction
samples, addressed by frame index, and shown to be disjoint on the decoded index
rather than on the times that produced it — and asks whether the frozen
rectangle still holds them.

AND IT COMPARES AGAINST THE COMPLETE LINES, not the detector's boxes. Codex:
"otherwise even a 100% result could mean only that we kept all the fragments the
detector managed to see". The hold-out frames are annotated the same way the
construction frames were, off the same magnified band sheets with the same
ruler, which is why this costs a second round of looking rather than a second
call.

WHAT IT CANNOT DO. It cannot be re-run after the region is adjusted and still
be a hold-out: the moment a region is changed because of what this reported,
these frames become construction data and the next verification owes new ones.
Nothing in code can enforce that; `held_out` names the set so a reader can see
which frames a claim rests on.
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

#: The same pad the regions were built with — measured, not chosen: on 52
#: annotated frames the text never reaches above the detector's box and reaches
#: below it on 11, by at most 0.6 proxy pixels.
Y_PAD_PROXY_PX = 0.6
PROXY_H = 270.0
LINE_Y0, LINE_Y1 = 0.9037, 0.9704


def _annotations():
    spec = importlib.util.spec_from_file_location(
        "ann", str(_ROOT / "scripts" / "apply_line_annotations.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project")
    ap.add_argument("clip")
    ap.add_argument("--phase", default="", help="one phase, or all of them")
    args = ap.parse_args()

    from services.clipper import caption_region as cr
    from services.clipper import source_caption_observation as sco

    frames_dir = DATA / args.project / "caption_frames"
    regions_path = (DATA / args.project / "phase_regions"
                    / f"{args.clip}.regions.json")
    if not regions_path.exists():
        print(f"REFUSED: no frozen regions at {regions_path}")
        return 2
    blob = json.loads(regions_path.read_text(encoding="utf-8"))
    sw, sh = int(blob["src_w"]), int(blob["src_h"])
    wanted = [p for p in blob["phases"] if p.get("region")
              and (not args.phase or p["phase"] == args.phase)]
    if not wanted:
        print(f"REFUSED: no built region for {args.phase or 'any phase'}")
        return 2

    key = f"{args.clip}.holdout"
    ann = _annotations()
    lines = ann.LINES.get(key)
    if not lines:
        print(f"REFUSED: no annotated hold-out lines for {key}")
        return 2

    build = frames_dir / f"{args.clip}.samples.json"
    held = frames_dir / f"{args.clip}.holdout.samples.json"
    for path in (build, held):
        if not path.exists():
            print(f"REFUSED: {path} is not there")
            return 2
    a = json.loads(build.read_text(encoding="utf-8"))["observation"]
    b = json.loads(held.read_text(encoding="utf-8"))["observation"]
    apart = sco.disjoint(a, b)
    if apart["disjoint"] is not True:
        # A hold-out that shares frames with the construction set reports the
        # circularity as escaped, which is worse than not running at all.
        print(f"REFUSED: the hold-out is not disjoint — {apart}")
        return 2

    # EVERY hold-out sample must be accounted for, as an annotated line or as a
    # frame confirmed to carry none. A frame nobody annotated is not a frame the
    # region held.
    no_line = set(ann.NO_LINE.get(key, ()))
    times = [s["t_decoded"] for s in b["samples"] if s.get("t_decoded") is not None]
    missing = [t for t in times if t not in lines and t not in no_line]
    if missing:
        print(f"REFUSED: {len(missing)} hold-out frames are not annotated: "
              f"{missing[:8]}")
        return 2

    pad = Y_PAD_PROXY_PX / PROXY_H
    print(f"{args.clip}  hold-out {apart['holdout']} frames, "
          f"{apart['shared']} shared with construction, "
          f"disjoint={apart['disjoint']}, {len(no_line)} carry no line")
    rows = []
    clipped_total = 0
    for phase in wanted:
        t0, t1 = float(phase["t0"]), float(phase["t1"])
        inside = {t: xx for t, xx in lines.items() if t0 <= t < t1}
        boxes = [{"x0": x0, "x1": x1, "y0": LINE_Y0,
                  "y1": min(1.0, LINE_Y1 + pad)} for x0, x1 in inside.values()]
        got = cr.verify_frozen(phase["region"], boxes, sw, sh)
        clipped_total += got["clipped"] or 0
        # THE SUBJECT HALF IS NOT VERIFIED HERE. A watch phase's region has to
        # hold the watch as well as the line, and the hold-out frames carry no
        # watch annotation — so reporting only the lines and calling the phase
        # verified would be a partial check wearing a complete answer's name.
        subject_verified = phase.get("subject_source") == "face"
        rows.append({"phase": phase["phase"], "lines": got,
                     "subject_verified": subject_verified})
        print(f"  {phase['phase']:<12} LINES {got['held']} held, "
              f"{got['clipped']} clipped of {got['held_out']}; worst overflow "
              f"{got['worst_overflow_px']} source px"
              + ("" if subject_verified
                 else "   [SUBJECT NOT VERIFIED: the hold-out frames carry no "
                      "watch annotation]"))

    out = {"clip": args.clip, "disjoint": apart, "phases": rows,
           "held_out_frames": [s.get("frame") for s in b["samples"]],
           "verdict": None}
    (DATA / args.project / "phase_regions"
     / f"{args.clip}.verification.json").write_text(
        json.dumps(out, indent=1, default=str), encoding="utf-8")
    unverified = [r["phase"] for r in rows if not r["subject_verified"]]
    if unverified:
        print(f"  {len(unverified)} phase(s) have their SUBJECT unverified: "
              f"{unverified}")
    # A clipped line is a real finding, and so is a phase whose subject nobody
    # checked: neither may leave a clean exit code behind.
    return 0 if not clipped_total and not unverified else 2


if __name__ == "__main__":
    raise SystemExit(main())
