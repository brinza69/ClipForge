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
    args = ap.parse_args()

    import cv2

    if args.t1 <= args.t0 or args.step <= 0:
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
        want = args.t0
        while want <= args.t1 + 1e-6:
            cap.set(cv2.CAP_PROP_POS_MSEC, want * 1000.0)
            got_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
            index = cap.get(cv2.CAP_PROP_POS_FRAMES)
            ok, frame = cap.read()
            if not ok or frame is None:
                # Counted, never dropped: a strip one frame short would hide
                # that a moment was never looked at, which is the whole point.
                skipped.append(round(want, 3))
                want += args.step
                continue
            # BOTH TIMES, because seeking is approximate and a strip labelled
            # with what was asked for states a precision the decoder did not
            # deliver — the same rule the caption observations follow.
            cv2.putText(frame, f"{got_ms / 1000.0:.2f}s  f{int(index)}",
                        (4, frame.shape[0] - 6), cv2.FONT_HERSHEY_SIMPLEX,
                        0.42, (255, 255, 255), 1)
            tiles.append(frame)
            want += args.step
    finally:
        cap.release()

    if not tiles:
        print("REFUSED: no frame in that range could be read")
        return 2
    per = COLS * ROWS
    made = 0
    for page in range((len(tiles) + per - 1) // per):
        chunk = tiles[page * per:(page + 1) * per]
        rows = []
        for r in range(0, len(chunk), COLS):
            row = chunk[r:r + COLS]
            while len(row) < COLS:
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
