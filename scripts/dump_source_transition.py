"""Frames across a moment in the SOURCE, densely, to find where something changes.

    python scripts/dump_source_transition.py pilotf81b 1210.5 1213.5 --step 0.1

WHY IT IS SEPARATE FROM THE CAPTION TOOLS. Those sample per shot of a plan and
run the text detector. This asks a question about the source's own structure —
when does a picture-in-picture insert appear, where does the creator cut to a
close-up — and the answer is a boundary in time, not a box. It runs no detector
at all: nothing here is looking for text.

WHY DENSE. A first positive observation is not a moment of appearance. On
`54a7e6d31dc8` the insert is absent in an inspected frame at 1210.9 s and
present at 1212.7 s; the transition is somewhere between, and reporting 1212.7
as "when it appears" states a precision no observation supports. Sampling
between two known frames is how the interval gets narrowed, and whatever
interval is left stays as uncertainty rather than being rounded to a number.

Writes into `<project>/transitions/`, and touches nothing else.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))

DATA = Path(os.environ.get("CLIPFORGE_DATA_DIR") or (_ROOT / "data")) / "clipper"

COLS, ROWS = 4, 3


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project")
    ap.add_argument("t0", type=float)
    ap.add_argument("t1", type=float)
    ap.add_argument("--step", type=float, default=0.1)
    ap.add_argument("--name", default="t")
    ap.add_argument("--at", default="",
                    help="comma-separated decoded times instead of a range; "
                         "use when the frames to annotate are already known")
    ap.add_argument("--grid", action="store_true",
                    help="overlay a labelled grid every 0.1 of width AND "
                         "height, so a box can be read off both axes")
    ap.add_argument("--scale", type=float, default=1.0,
                    help="magnify each tile by this factor")
    ap.add_argument("--frames", default="",
                    help="comma-separated FRAME indices. Seeking by time lands "
                         "on whatever frame the decoder gives — asking for "
                         "224.3 s returned f2243 where the construction sample "
                         "is f2244 — so an annotation targeting a sample must "
                         "address it by index, not by the time that produced it")
    ap.add_argument("--cols", type=int, default=COLS)
    ap.add_argument("--rows", type=int, default=ROWS)
    args = ap.parse_args()

    import cv2

    by_frame = []
    if args.frames:
        try:
            by_frame = [int(x) for x in args.frames.split(",") if x.strip()]
        except ValueError:
            print("REFUSED: --frames is not a list of integers")
            return 2
        if not by_frame:
            print("REFUSED: --frames is empty")
            return 2
    wanted = []
    if args.at:
        try:
            wanted = [float(x) for x in args.at.split(",") if x.strip()]
        except ValueError:
            print("REFUSED: --at is not a list of seconds")
            return 2
        if not wanted:
            print("REFUSED: --at is empty")
            return 2
    if not wanted and not by_frame and (args.t1 <= args.t0 or args.step <= 0):
        print("REFUSED: t1 must be after t0 and step must be positive")
        return 2
    proxy = DATA / args.project / "proxy" / "proxy.mp4"
    if not proxy.exists():
        print(f"REFUSED: no proxy at {proxy}")
        return 2

    out_dir = DATA / args.project / "transitions"
    out_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(proxy))
    tiles: list[Any] = []
    skipped: list[float] = []
    try:
        frames_todo = list(by_frame) if by_frame else None
        todo = list(wanted) if wanted else None
        want = args.t0 if (todo is None and frames_todo is None) else 0.0
        if todo is not None:
            want = todo[0]
        while (bool(frames_todo) if frames_todo is not None
               else (bool(todo) if todo is not None else want <= args.t1 + 1e-6)):
            if frames_todo is not None:
                cap.set(cv2.CAP_PROP_POS_FRAMES, frames_todo.pop(0))
            else:
                if todo is not None:
                    want = todo.pop(0)
                cap.set(cv2.CAP_PROP_POS_MSEC, want * 1000.0)
            got_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
            index = cap.get(cv2.CAP_PROP_POS_FRAMES)
            ok, frame = cap.read()
            if not ok or frame is None:
                # Counted, never dropped: a strip one frame short would hide
                # that a moment was never looked at, which is the whole point.
                skipped.append(round(want, 3))
                if todo is None and frames_todo is None:
                    want += args.step
                continue
            if args.scale != 1.0:
                frame = cv2.resize(
                    frame, (int(frame.shape[1] * args.scale),
                            int(frame.shape[0] * args.scale)),
                    interpolation=cv2.INTER_CUBIC)
            if args.grid:
                gh, gw = frame.shape[0], frame.shape[1]
                for i in range(1, 10):
                    x, y = int(gw * i / 10.0), int(gh * i / 10.0)
                    cv2.line(frame, (x, 0), (x, gh), (0, 200, 255), 1)
                    cv2.line(frame, (0, y), (gw, y), (0, 200, 255), 1)
                    cv2.putText(frame, f".{i}", (x + 2, 12),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.34, (0, 200, 255), 1)
                    cv2.putText(frame, f".{i}", (2, y - 3),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.34, (0, 200, 255), 1)
            # BOTH TIMES, because seeking is approximate and a strip labelled
            # with what was asked for states a precision the decoder did not
            # deliver — the same rule the caption observations follow.
            cv2.putText(frame, f"{got_ms / 1000.0:.2f}s  f{int(index)}",
                        (4, frame.shape[0] - 6), cv2.FONT_HERSHEY_SIMPLEX,
                        0.42, (255, 255, 255), 1)
            tiles.append(frame)
            if todo is None and frames_todo is None:
                want += args.step
    finally:
        cap.release()

    if not tiles:
        print("REFUSED: no frame in that range could be read")
        return 2
    cols, rows_n = max(1, args.cols), max(1, args.rows)
    per = cols * rows_n
    made = 0
    for page in range((len(tiles) + per - 1) // per):
        chunk = tiles[page * per:(page + 1) * per]
        rows = []
        for r in range(0, len(chunk), cols):
            row = chunk[r:r + cols]
            while len(row) < cols:
                import numpy as np

                row.append(np.zeros_like(chunk[0]))
            rows.append(cv2.hconcat(row))
        path = out_dir / f"{args.name}{args.t0:.1f}-{args.t1:.1f}.{page:02d}.jpg"
        cv2.imwrite(str(path), cv2.vconcat(rows), [cv2.IMWRITE_JPEG_QUALITY, 90])
        print(f"  {path.name}: {len(chunk)} frames")
        made += 1
    print(f"\n{made} sheet(s), {len(tiles)} frames, {len(skipped)} unreadable"
          + (f" at {skipped}" if skipped else ""))
    return 0 if not skipped else 2


if __name__ == "__main__":
    raise SystemExit(main())
