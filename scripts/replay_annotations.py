"""Draw the RECORDED coordinates back over the frame they name.

    python scripts/replay_annotations.py pilotf81b --set construction
    python scripts/replay_annotations.py pilotf81b --set holdout --frames 2308,2360

WHAT THIS IS FOR, in Codex's words: "citeste 5-6, apoi deseneaza coordonatele
salvate inapoi peste fiecare imagine. Verifica astfel ca dreptunghiul inregistrat
ajunge exact unde ai vrut." Reading a box off a ruler and typing it into a dict
are two operations, and only the first one has been checked. Everything the
regions rest on passes through the second.

IT IS THE INSTRUMENT CHECK, NOT ANOTHER READING. Nothing here measures anything:
it renders what is already stored. A box that lands on the speaker's chest tells
you the number is wrong without needing a second opinion about where his chin is.

AND IT CHECKS THE ADDRESSING TOO, which is the half a coordinate check would
miss. `POS_FRAMES` is read BEFORE `read()` — after it, the property names the
NEXT frame — and a decoded index that differs from the one asked for is a
REFUSAL, not a caption. An annotation drawn over the neighbouring frame looks
almost right and is about a different picture; that is exactly how the first
pass of `watch_annotations` went wrong, on frames one apart from the samples.

WHAT THE FIRST RUN FOUND, on all 46 annotated frames of `b23c14c41495`.

Every decoded index matched the one asked for, so the ADDRESSING is sound. The
coordinates are not: the boxes land on the right objects, but their TOPS are
systematically drawn 0.02-0.06 of the frame too high — above the fingers, above
the durag, into sky or building. On a 3x sheet a dark object against a bright
sky bleeds upward, and the reading followed the bleed. Seen on f2296, f2303,
f2307, f2316, f2338, f2355, f2360, f2365, f2372 and f2396; `hand` worst, `face`
on several. Two `face` boxes are also too wide: f2396 and f2398 run to 0.780-0.790
where the head sliver beside the watch ends near 0.750, the rest being bokeh.

WHY THAT MATTERS MORE THAN ITS SIZE SUGGESTS. Five of the six boxes that decide
a verdict in `verify_phase_regions` are among them. The one CLIP the run
reported — the face at f2398, 38.4 px past the screen region — rests entirely on
a right edge the replay shows enclosing background; corrected, the box is inside
the region and the clip does not exist. Four of the five `indeterminate` results
are top margins of `hand` boxes drawn above the hand.

AND WHAT MUST NOT HAPPEN NEXT is the obvious thing. Every one of those
corrections moves a verdict toward `held`, which is exactly the shape of tuning
an annotation until the answer comes out green — however defensible each
individual correction is, and they are defensible: a box enclosing sky is wrong
whatever it does to the verdict. Codex's rule decides it instead. This round
stands as the diagnostic of the CURRENT candidate. Correcting the annotation
makes a new candidate, the regions built from it are different regions, and a
region changed because of what a hold-out reported is no longer verified by that
hold-out. The corrected candidate owes a FRESH set of frames.

Writes into `<project>/annotation_replay/` and touches nothing else.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))

DATA = Path(os.environ.get("CLIPFORGE_DATA_DIR") or (_ROOT / "data")) / "clipper"

#: BGR, one per annotated part. Distinct hues rather than a palette, because
#: the question is "is this box round that thing" and nothing subtler.
COLOURS: dict[str, tuple[int, int, int]] = {
    "watch": (0, 0, 255),
    "screen": (0, 255, 255),
    "hand": (255, 160, 0),
    "face": (0, 255, 0),
}

NO_ANNOTATION = "no_annotation_for_this_frame"
WRONG_FRAME = "the_decoder_returned_a_different_frame"
UNREADABLE = "the_frame_could_not_be_read"


def _annotations():
    spec = importlib.util.spec_from_file_location(
        "wa", str(_ROOT / "scripts" / "watch_annotations.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _grid(frame: Any, cv2: Any) -> None:
    h, w = frame.shape[0], frame.shape[1]
    for i in range(1, 10):
        x, y = int(w * i / 10.0), int(h * i / 10.0)
        cv2.line(frame, (x, 0), (x, h), (0, 200, 255), 1)
        cv2.line(frame, (0, y), (w, y), (0, 200, 255), 1)
        cv2.putText(frame, f".{i}", (x + 2, 12), cv2.FONT_HERSHEY_SIMPLEX,
                    0.34, (0, 200, 255), 1)
        cv2.putText(frame, f".{i}", (2, y - 3), cv2.FONT_HERSHEY_SIMPLEX,
                    0.34, (0, 200, 255), 1)


def _draw(frame: Any, boxes: dict, cv2: Any) -> None:
    """Each recorded box, in its own colour, labelled with its own numbers.

    The label carries the STORED fractions, not the pixels they became, so a
    reader comparing the picture with the file is comparing the same thing.
    """
    h, w = frame.shape[0], frame.shape[1]
    for name, box in sorted(boxes.items()):
        x0, x1, y0, y1 = box
        p0 = (int(round(x0 * w)), int(round(y0 * h)))
        p1 = (int(round(x1 * w)), int(round(y1 * h)))
        colour = COLOURS.get(name, (255, 255, 255))
        cv2.rectangle(frame, p0, p1, colour, 2)
        cv2.putText(frame, f"{name} {x0:.3f}-{x1:.3f} / {y0:.3f}-{y1:.3f}",
                    (p0[0] + 3, max(14, p0[1] + 16)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, colour, 1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project")
    ap.add_argument("--set", dest="which", default="construction",
                    choices=("construction", "holdout"))
    ap.add_argument("--frames", default="",
                    help="comma-separated frame indices; default is every "
                         "annotated frame in the chosen set")
    ap.add_argument("--scale", type=float, default=3.0)
    ap.add_argument("--grid", action="store_true", default=True)
    ap.add_argument("--name", default="")
    args = ap.parse_args()

    import cv2

    wa = _annotations()
    table = wa.WATCH if args.which == "construction" else wa.HOLDOUT
    if args.frames:
        try:
            want = [int(x) for x in args.frames.split(",") if x.strip()]
        except ValueError:
            print("REFUSED: --frames is not a list of integers")
            return 2
    else:
        want = sorted(table)
    if not want:
        print(f"REFUSED: the {args.which} set is empty")
        return 2

    proxy = DATA / args.project / "proxy" / "proxy.mp4"
    if not proxy.exists():
        print(f"REFUSED: no proxy at {proxy}")
        return 2
    out_dir = DATA / args.project / "annotation_replay"
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.name or args.which

    cap = cv2.VideoCapture(str(proxy))
    if not cap.isOpened():
        print(f"REFUSED: {proxy} would not open")
        return 2
    refused: list[tuple[int, str]] = []
    made = 0
    try:
        for index in want:
            boxes = table.get(index)
            if not boxes:
                refused.append((index, NO_ANNOTATION))
                continue
            cap.set(cv2.CAP_PROP_POS_FRAMES, index)
            # The INDEX before the read — after it the property names the NEXT
            # frame, and a check that reads it afterwards is off by one in the
            # direction that always looks fine. The TIME goes after the read,
            # because before it `POS_MSEC` names the PREVIOUS frame; see
            # `source_caption_observation.observe` for the measurement.
            at = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
            ok, frame = cap.read()
            got_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
            if not ok or frame is None:
                refused.append((index, UNREADABLE))
                continue
            if at != index:
                # The annotation would be drawn over a DIFFERENT picture, and
                # the result would look almost right. Refuse instead.
                refused.append((index, f"{WRONG_FRAME}: asked {index}, got {at}"))
                continue
            if args.scale != 1.0:
                frame = cv2.resize(
                    frame, (int(frame.shape[1] * args.scale),
                            int(frame.shape[0] * args.scale)),
                    interpolation=cv2.INTER_CUBIC)
            if args.grid:
                _grid(frame, cv2)
            _draw(frame, boxes, cv2)
            cv2.putText(frame, f"{got_ms / 1000.0:.2f}s  f{at}  "
                              f"{args.which}  {len(boxes)} box(es)",
                        (4, frame.shape[0] - 6), cv2.FONT_HERSHEY_SIMPLEX,
                        0.46, (255, 255, 255), 1)
            path = out_dir / f"{prefix}.f{index}.jpg"
            cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
            made += 1
    finally:
        cap.release()

    # THE DENOMINATOR FIRST, so a run that drew three of twenty cannot read as
    # a clean pass on three.
    print(f"{len(want)} frame(s) asked for, {made} drawn, {len(refused)} refused")
    for index, why in refused:
        print(f"  f{index}: {why}")
    print(f"  in {out_dir}")
    return 0 if not refused else 2


if __name__ == "__main__":
    raise SystemExit(main())
