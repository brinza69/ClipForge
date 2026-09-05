"""The agent's visually confirmed FULL-LINE extents, folded into the observation.

    python scripts/apply_line_annotations.py pilotf81b 6053a598cf06

WHAT THIS IS. §A's third condition: every extent used to build a region must be
confirmed VISUALLY, including the words this source displays dimmed before they
are spoken. The transcript may propose the text; it says nothing about where the
line ends, how it is split, or which upcoming words are already on screen. The
loose detector threshold and the 1.37x ratio it produces are diagnostics and
neither is the geometric truth.

HOW THESE WERE OBTAINED. `scripts/dump_caption_band.py` crops the caption band,
magnifies it to 1600 px and prints a ruler in fractions of the FRAME's width.
All 56 construction frames of `6053a598cf06` and all 62 of `b23c14c41495`
were read off those fifteen sheets
and the left and right margins of each complete line taken from the ruler. (One
time label was transcribed wrong on the first pass — 1186.2 for 1185.2 — and the
script caught it by refusing: a frame with no annotation is listed under
`not_annotated` and the exit code is non-zero, so an incomplete set cannot
quietly become a region.) The
detector's own boxes are drawn on the same image, which is what makes the
under-coverage visible rather than inferred.

WHAT IS CONFIRMED AND WHAT IS NOT. The x margins are the agent's, read from the
image. The y extent is the DETECTOR's, unchanged: nothing here measured the
line's height more precisely than it did, and claiming otherwise would put an
unmeasured number under a confirmed label. `_Y_FROM_DETECTOR` says so on every
annotation.

AND THE DETECTOR'S BOXES SURVIVE. `source_caption_observation.correct` keeps
them in `detector_boxes` and marks the sample `provenance: corrected`, so the
record never claims the detector saw what an agent supplied, and a later pass
measuring its recall still has what it returned.

A CORRECTION TO AN EARLIER CLAIM, kept here because it was published. I reported
that "whole lines are missed — at 1161.1 s a caption is plainly on screen and no
box was returned". That is FALSE. The detector returned one box at 1161.1 s
(0.3375-0.7271); I misread a 480x270 thumbnail. Across all 56 sampled frames of
this clip exactly four returned nothing — 1150.7, 1153.6, 1164.6 and 1188.0 —
and the band sheets show all four are genuine gaps between lines, with no text
on screen. On this clip the detector missed no line at all. What it does miss is
the ENDS of lines, which is a different and smaller claim.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))

DATA = Path(os.environ.get("CLIPFORGE_DATA_DIR") or (_ROOT / "data")) / "clipper"

_Y_FROM_DETECTOR = ("x_margins_read_off_the_band_sheet_by_the_agent_y_extent_"
                    "is_the_detectors_own_and_was_not_re_measured")

#: `{decoded time: (x0, x1)}` in fractions of the frame's width, read off
#: `<clip>.band*.jpg`. A time absent from this map is a frame with no text on
#: screen; those are listed in `NO_TEXT` so "not annotated" and "nothing there"
#: cannot be confused.
LINES: dict[str, dict[float, tuple[float, float]]] = {
    "6053a598cf06": {
        1148.3: (0.295, 0.705), 1148.9: (0.300, 0.705), 1150.0: (0.295, 0.705),
        1151.8: (0.272, 0.732), 1152.4: (0.272, 0.732), 1154.2: (0.272, 0.735),
        1155.4: (0.268, 0.728), 1156.1: (0.270, 0.732), 1157.5: (0.270, 0.730),
        1158.3: (0.272, 0.727), 1159.3: (0.272, 0.727), 1159.5: (0.272, 0.727),
        1160.4: (0.271, 0.725), 1161.1: (0.272, 0.727),
        1163.2: (0.383, 0.618), 1166.4: (0.440, 0.560), 1166.8: (0.392, 0.608),
        1167.9: (0.392, 0.608), 1168.4: (0.392, 0.608), 1169.9: (0.338, 0.660),
        1170.7: (0.338, 0.660),
        1172.1: (0.335, 0.663), 1172.7: (0.338, 0.663), 1173.7: (0.338, 0.663),
        1174.1: (0.340, 0.660), 1175.4: (0.352, 0.647), 1176.3: (0.352, 0.647),
        1178.0: (0.390, 0.610), 1178.8: (0.390, 0.610),
        1180.1: (0.345, 0.660), 1180.7: (0.340, 0.660), 1182.0: (0.378, 0.622),
        1182.8: (0.378, 0.622), 1184.4: (0.380, 0.620), 1185.2: (0.368, 0.632),
        1187.0: (0.403, 0.597),
        1189.6: (0.328, 0.672), 1190.2: (0.328, 0.677), 1191.4: (0.257, 0.743),
        1192.0: (0.257, 0.743), 1193.1: (0.257, 0.743), 1193.6: (0.257, 0.743),
        1194.8: (0.303, 0.697), 1195.5: (0.303, 0.697),
        1196.5: (0.263, 0.735), 1196.8: (0.263, 0.740), 1197.9: (0.262, 0.740),
        1198.9: (0.327, 0.672), 1200.4: (0.327, 0.672), 1200.9: (0.327, 0.672),
        1202.5: (0.293, 0.707), 1203.5: (0.293, 0.707),
    },
    "b23c14c41495": {
        184.1: (0.393, 0.607), 184.6: (0.393, 0.607), 186.5: (0.365, 0.635),
        187.7: (0.345, 0.657), 188.1: (0.345, 0.653), 189.1: (0.358, 0.640),
        189.6: (0.360, 0.640),
        191.0: (0.313, 0.700), 191.9: (0.313, 0.697), 193.3: (0.333, 0.672),
        193.9: (0.333, 0.672), 195.3: (0.275, 0.727), 196.1: (0.275, 0.727),
        197.3: (0.275, 0.727), 197.7: (0.470, 0.532),
        198.6: (0.272, 0.732), 199.2: (0.272, 0.727), 200.3: (0.343, 0.652),
        200.7: (0.345, 0.655), 202.9: (0.293, 0.707), 203.5: (0.290, 0.707),
        205.0: (0.330, 0.670), 207.5: (0.352, 0.648), 208.2: (0.352, 0.648),
        209.3: (0.393, 0.607), 209.7: (0.393, 0.607), 210.6: (0.355, 0.645),
        211.2: (0.355, 0.645),
        212.4: (0.300, 0.700), 213.0: (0.303, 0.700), 214.5: (0.355, 0.648),
        215.6: (0.352, 0.650), 216.2: (0.352, 0.650), 217.6: (0.292, 0.712),
        218.3: (0.292, 0.710),
        219.7: (0.290, 0.710), 220.4: (0.293, 0.707), 221.9: (0.325, 0.675),
        222.7: (0.355, 0.648), 224.3: (0.457, 0.547), 225.2: (0.307, 0.693),
        226.7: (0.310, 0.693), 227.4: (0.310, 0.693),
        228.8: (0.303, 0.693), 229.5: (0.303, 0.693), 231.5: (0.303, 0.697),
        232.9: (0.300, 0.700), 235.4: (0.283, 0.717), 236.4: (0.283, 0.717),
        237.8: (0.303, 0.697), 238.3: (0.303, 0.697), 241.2: (0.405, 0.593),
        241.8: (0.405, 0.593),
    },
}

#: Frames confirmed to carry NO SUBTITLE LINE, from the same sheets. Listed
#: rather than merely absent, because "the agent did not annotate this" and
#: "there was no line to annotate" are the two answers this batch exists to keep
#: apart, and an omission cannot say which one it is.
NO_LINE: dict[str, tuple[float, ...]] = {
    "6053a598cf06": (1150.7, 1153.6, 1164.6, 1188.0),
    "b23c14c41495": (185.8, 201.5, 201.9, 205.8, 214.1, 230.9, 233.7, 239.4,
                     240.0),
}

#: Of those, the frames that carry OTHER text — not a subtitle line, and not an
#: empty frame either. `b23c14c41495` at 240.0 s is the Apple Watch face the
#: speaker holds to the lens, which `caption_labels` already marks
#: `non_dialogue` and which that label does not licence cropping. Kept apart
#: because "no subtitle line" and "nothing on screen" are two answers and only
#: one of them is true here.
OTHER_TEXT_ONLY: dict[str, tuple[float, ...]] = {
    "6053a598cf06": (),
    "b23c14c41495": (240.0,),
}


def _y_of(sample: dict) -> tuple[float, float] | None:
    """The line's vertical extent, from the DETECTOR's boxes for that frame."""
    boxes = sample.get("boxes") or []
    ys = [(float(b["y0"]), float(b["y1"])) for b in boxes
          if isinstance(b, dict) and "y0" in b and "y1" in b]
    if not ys:
        return None
    return min(y[0] for y in ys), max(y[1] for y in ys)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project")
    ap.add_argument("clip")
    args = ap.parse_args()

    from services.clipper import source_caption_observation as sco

    lines = LINES.get(args.clip)
    if lines is None:
        # A clip nobody annotated may not borrow another clip's margins.
        print(f"REFUSED: no confirmed line extents for {args.clip}")
        return 2
    frames_dir = DATA / args.project / "caption_frames"
    path = frames_dir / f"{args.clip}.samples.json"
    if not path.exists():
        print(f"REFUSED: no samples at {path}")
        return 2
    blob = json.loads(path.read_text(encoding="utf-8"))
    obs = blob["observation"]

    corrections: list[dict] = []
    missing: list[float] = []
    for sample in obs["samples"]:
        at = sample.get("t_decoded")
        if at is None:
            continue
        if at in NO_LINE.get(args.clip, ()):  # confirmed empty
            continue
        want = lines.get(at)
        if want is None:
            missing.append(at)
            continue
        y = _y_of(sample)
        if y is None:
            # The agent read x off the sheet; y comes from the detector and
            # there is none. Refused rather than invented — a caption band's
            # height guessed from the source's other frames is not this frame.
            missing.append(at)
            continue
        corrections.append({"at": at, "by": sco.AGENT, "why": _Y_FROM_DETECTOR,
                            "boxes": [{"x0": want[0], "x1": want[1],
                                       "y0": y[0], "y1": y[1]}]})

    fixed = sco.correct(obs, corrections)
    out = {"clip": args.clip, "annotated": len(corrections),
           "confirmed_no_line": list(NO_LINE.get(args.clip, ())),
           "of_which_other_text": list(OTHER_TEXT_ONLY.get(args.clip, ())),
           "not_annotated": missing, "corrections": fixed["corrections"],
           "refusals": fixed["correction_refusals"], "observation": fixed}
    (frames_dir / f"{args.clip}.annotated.json").write_text(
        json.dumps(out, indent=1, default=str), encoding="utf-8")

    total = len(obs["samples"])
    print(f"{args.clip}: {total} sampled frames")
    print(f"  {len(corrections)} annotated with a confirmed full line")
    other = OTHER_TEXT_ONLY.get(args.clip, ())
    print(f"  {len(NO_LINE.get(args.clip, ()))} confirmed to have NO subtitle "
          f"line, of which {len(other)} carry other text" + (f" at {list(other)}"
          if other else ""))
    print(f"  {len(missing)} NOT annotated" + (f" — {missing}" if missing else ""))
    print(f"  {fixed['corrections']} folded in, "
          f"{len(fixed['correction_refusals'])} refused")

    widths = sorted((b - a) for a, b in lines.values())
    sw = 2560
    print(f"\n  line width, confirmed: {widths[0] * sw:.0f}..{widths[-1] * sw:.0f} "
          f"source px, median {widths[len(widths) // 2] * sw:.0f}")
    fits = sum(1 for w in widths if w * sw <= 810)
    print(f"  fits an 810 px 9:16 window: {fits} of {len(widths)} frames")
    if missing:
        # An incomplete annotation may not be used to build a region.
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
