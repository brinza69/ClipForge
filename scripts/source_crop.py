"""The ambiguous edges, at the source's own resolution — no interpolation.

    python scripts/source_crop.py pilotf81b --set fresh --frames 2362,2400
    python scripts/source_crop.py pilotf81b --set fresh --frames 2400 --part face

WHY THIS EXISTS. Every annotation in `watch_annotations.py` was read off the
PROXY at `--scale 3.0`, and Codex named the problem in one sentence: "mărirea
aceluiași proxy nu adaugă detaliu." The proxy is 480x270. Magnifying it to 1440
interpolates; it does not resolve. The source is 2560x1440 — 5.33x the proxy's
linear resolution — and the edges the fresh lot could not decide are exactly the
ones where that difference is the whole question: the top of a white strap
against pale sky, the right edge of a head sliver against bokeh.

THE ADDRESSING IS STILL THE PROXY'S, because everything else in this corpus is.
The proxy runs at 10 fps and the source at 30, and the mapping is by TIME rather
than by any assumed ratio: proxy frame n is at n/10 s, and the source frame at
that instant is round(n/10 * 30). Verified on this pair — proxy f2362 at 236.20 s
maps to source f7086, decoded index 7086, decoded time 236.200. A decoded index
other than the one computed is a REFUSAL, not a caption.

THE GRID IS LABELLED IN FRACTIONS OF THE WHOLE FRAME, never of the crop. This is
not a stylistic choice: reading a crop against a ruler that restarts at its own
left edge is precisely the mistake that destroyed the first version of
`watch_annotations` — every right-hand tile came out displaced by 0.26-0.30 —
and a crop is a tile with the same trap. Every label here is an absolute
position in the source frame, so a number read off it can be typed into the
annotation unchanged.

AND IT NEVER UPSCALES. If a crop comes out small, that is the resolution the
source has, and inventing pixels is how the last two rounds of this went wrong.
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

PROXY_FPS = 10.0

#: How much context to keep around the annotated box, as a fraction of the
#: frame. Enough to see what the edge is against — sky, bokeh, a shoulder —
#: because "where does this end" is unanswerable without it.
MARGIN = 0.06

COLOURS: dict[str, tuple[int, int, int]] = {
    "watch": (0, 0, 255), "screen": (0, 255, 255),
    "hand": (255, 160, 0), "face": (0, 255, 0),
}

WRONG_FRAME = "the_decoder_returned_a_different_source_frame"
NO_ANNOTATION = "no_annotation_for_this_frame"
NO_PART = "the_frame_carries_no_box_for_that_part"


def _annotations():
    spec = importlib.util.spec_from_file_location(
        "wa", str(_ROOT / "scripts" / "watch_annotations.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _grid(img: Any, x0: int, y0: int, w: int, h: int, cv2: Any,
          step: float = 0.02) -> None:
    """Absolute-position rulers, every `step` of the FULL frame.

    `x0, y0` are the crop's origin in source pixels and `w, h` the full frame's
    size, so every label is where it is in the SOURCE — not in the crop.
    """
    ch, cw = img.shape[0], img.shape[1]
    first = int((x0 / w) / step) + 1
    for i in range(first, int(((x0 + cw) / w) / step) + 1):
        x = int(i * step * w) - x0
        if 0 <= x < cw:
            heavy = abs(i * step * 10 - round(i * step * 10)) < 1e-9
            if heavy:
                cv2.line(img, (x, 0), (x, ch), (0, 200, 255), 1)
            else:
                cv2.line(img, (x, 0), (x, 12), (0, 200, 255), 1)
                cv2.line(img, (x, ch - 12), (x, ch), (0, 200, 255), 1)
            cv2.putText(img, f"{i * step:.2f}", (x + 2, 14),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 200, 255), 1)
    first = int((y0 / h) / step) + 1
    for i in range(first, int(((y0 + ch) / h) / step) + 1):
        y = int(i * step * h) - y0
        if 0 <= y < ch:
            heavy = abs(i * step * 10 - round(i * step * 10)) < 1e-9
            if heavy:
                cv2.line(img, (0, y), (cw, y), (0, 200, 255), 1)
            else:
                cv2.line(img, (0, y), (12, y), (0, 200, 255), 1)
                cv2.line(img, (cw - 12, y), (cw, y), (0, 200, 255), 1)
            cv2.putText(img, f"{i * step:.2f}", (2, y - 3),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 200, 255), 1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project")
    ap.add_argument("--set", dest="which", default="fresh",
                    choices=("construction", "holdout", "fresh"))
    ap.add_argument("--frames", required=True,
                    help="comma-separated PROXY frame indices — the same "
                         "addressing the annotations use")
    ap.add_argument("--part", default="",
                    help="crop around one annotated part; default is the union "
                         "of every part on the frame")
    ap.add_argument("--edge", default="",
                    help="crop a BAND around one margin of --part "
                         "(top|bottom|left|right) instead of the whole box. "
                         "This is the calibration view: the question is where "
                         "ONE edge lies, and a crop of the whole object spends "
                         "its pixels on the parts nobody is arguing about")
    ap.add_argument("--band", type=float, default=0.05,
                    help="half-width of the --edge band, as a fraction of the "
                         "frame")
    ap.add_argument("--step", type=float, default=0.02,
                    help="grid spacing as a fraction of the FULL frame; ticks "
                         "at every step, full lines every 0.1")
    ap.add_argument("--bare", action="store_true",
                    help="draw the grid and NO annotation boxes. This is the "
                         "coverage pass's view: Codex, on re-reading limits, "
                         "\"limita trebuie confirmata pe sursa, nu mostenita "
                         "din cutia gresita\" — and a wrong box drawn over the "
                         "frame is exactly what a fresh reading would anchor on")
    ap.add_argument("--full", action="store_true",
                    help="the whole source frame instead of a crop, for when "
                         "the question is where something is rather than "
                         "exactly where it ends")
    args = ap.parse_args()

    import cv2

    wa = _annotations()
    table = {"construction": wa.WATCH, "holdout": wa.HOLDOUT,
             "fresh": wa.FRESH}[args.which]
    try:
        want = [int(x) for x in args.frames.split(",") if x.strip()]
    except ValueError:
        print("REFUSED: --frames is not a list of integers")
        return 2

    source = DATA / args.project / "source" / "source.mp4"
    if not source.exists():
        print(f"REFUSED: no source at {source}")
        return 2
    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        print(f"REFUSED: {source} would not open")
        return 2
    fps = cap.get(cv2.CAP_PROP_FPS)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if not fps or fps <= 0 or w < 1 or h < 1:
        print("REFUSED: the source reports no usable geometry")
        cap.release()
        return 2

    out_dir = DATA / args.project / "source_crops"
    out_dir.mkdir(parents=True, exist_ok=True)
    refused: list[tuple[int, str]] = []
    made = 0
    print(f"source {w}x{h} @ {fps} fps, proxy addressing at {PROXY_FPS} fps")
    try:
        for pf in want:
            boxes = table.get(pf)
            if not boxes:
                refused.append((pf, NO_ANNOTATION))
                continue
            if args.part and args.part not in boxes:
                refused.append((pf, f"{NO_PART}: {args.part}"))
                continue
            t = pf / PROXY_FPS
            sf = int(round(t * fps))
            cap.set(cv2.CAP_PROP_POS_FRAMES, sf)
            at = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
            ok, frame = cap.read()
            if not ok or frame is None:
                refused.append((pf, "the_source_frame_could_not_be_read"))
                continue
            if at != sf:
                refused.append((pf, f"{WRONG_FRAME}: wanted {sf}, got {at}"))
                continue

            use = {args.part: boxes[args.part]} if args.part else boxes
            if args.edge and not args.part:
                refused.append((pf, "an_edge_needs_a_part"))
                continue
            if args.full:
                x0, y0, x1, y1 = 0, 0, w, h
            elif args.edge:
                bx0, bx1, by0, by1 = boxes[args.part]
                # NOT `at`: that name holds the decoded SOURCE FRAME INDEX
                # and is stamped on the image. Shadowing it printed the edge
                # position where the frame number belongs — a mislabelled
                # frame, which is the failure this whole file exists to avoid.
                edge_at = None
                if args.edge in ("top", "bottom"):
                    edge_at = by0 if args.edge == "top" else by1
                    fx0, fx1 = bx0 - MARGIN, bx1 + MARGIN
                    fy0, fy1 = edge_at - args.band, edge_at + args.band
                elif args.edge in ("left", "right"):
                    edge_at = bx0 if args.edge == "left" else bx1
                    fx0, fx1 = edge_at - args.band, edge_at + args.band
                    fy0, fy1 = by0 - MARGIN, by1 + MARGIN
                else:
                    refused.append((pf, f"unknown_edge: {args.edge}"))
                    continue
                x0, x1 = max(0, int(fx0 * w)), min(w, int(fx1 * w))
                y0, y1 = max(0, int(fy0 * h)), min(h, int(fy1 * h))
            else:
                fx0 = min(b[0] for b in use.values()) - MARGIN
                fx1 = max(b[1] for b in use.values()) + MARGIN
                fy0 = min(b[2] for b in use.values()) - MARGIN
                fy1 = max(b[3] for b in use.values()) + MARGIN
                x0, x1 = max(0, int(fx0 * w)), min(w, int(fx1 * w))
                y0, y1 = max(0, int(fy0 * h)), min(h, int(fy1 * h))
            crop = frame[y0:y1, x0:x1].copy()
            if crop.size == 0:
                refused.append((pf, "the_crop_is_empty"))
                continue

            _grid(crop, x0, y0, w, h, cv2, args.step)
            for name, box in sorted(({} if args.bare else use).items()):
                bx0 = int(box[0] * w) - x0
                bx1 = int(box[1] * w) - x0
                by0 = int(box[2] * h) - y0
                by1 = int(box[3] * h) - y0
                cv2.rectangle(crop, (bx0, by0), (bx1, by1),
                              COLOURS.get(name, (255, 255, 255)), 2)
                cv2.putText(crop, name, (bx0 + 4, max(16, by0 + 20)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                            COLOURS.get(name, (255, 255, 255)), 1)
            asked = ""
            if args.edge:
                which_i = {"left": 0, "right": 1, "top": 2, "bottom": 3}
                asked = (f"  EDGE {args.part}.{args.edge} annotated at "
                         f"{boxes[args.part][which_i[args.edge]]:.3f}")
            cv2.putText(crop, f"proxy f{pf}  {t:.2f}s  source f{at}  "
                              f"crop x{x0}-{x1} y{y0}-{y1}  NATIVE{asked}",
                        (4, crop.shape[0] - 8), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, (255, 255, 255), 1)
            name = (f"{args.which}.f{pf}"
                    + (f".{args.part}" if args.part else "")
                    + (f".{args.edge}" if args.edge else ""))
            path = out_dir / f"{name}.png"
            # PNG, not JPEG: the question is where a soft edge ends, and a
            # lossy codec invents exactly the kind of gradient being read.
            cv2.imwrite(str(path), crop)
            made += 1
            print(f"  proxy f{pf} -> source f{at}  {crop.shape[1]}x"
                  f"{crop.shape[0]} px  {path.name}")
    finally:
        cap.release()

    print(f"\n{len(want)} asked for, {made} written, {len(refused)} refused")
    for pf, why in refused:
        print(f"  f{pf}: {why}")
    return 0 if not refused else 2


if __name__ == "__main__":
    raise SystemExit(main())
