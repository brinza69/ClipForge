"""The caption band alone, magnified, with a ruler — for annotating the LINE.

    python scripts/dump_caption_band.py pilotf81b 6053a598cf06

WHY THE CONTACT SHEETS ARE NOT ENOUGH. `dump_caption_frames` shows whole frames
at 480x270, which settles whether a box is a subtitle or a watermark and settles
nothing about where the line ENDS. §A needs the geometric extent of the complete
line, including the words this source displays dimmed before they are spoken,
and that cannot be read off a thumbnail.

So this crops the band, magnifies it, and puts a scale on it: ticks every 0.05
of the FRAME's width with the fraction printed, so a margin can be read off the
image in the same units `caption_labels` and `caption_region` use. The
detector's own boxes are drawn in green underneath, which is what makes the
under-coverage visible rather than inferred — on this source the union of them
is a median 1.04x and up to 1.37x narrower than a looser threshold finds, and
the dim tail of a line is usually outside all of them.

THE ANNOTATION IS STILL A PERSON'S OR AN AGENT'S. Nothing here measures the
line; it makes the line measurable. The loose threshold and the 1.37 ratio are
diagnostics and neither is the geometric truth.

Rows are stacked in time order and each is stamped with its DECODED time,
because that is what an annotation targets.
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

#: The slice of the frame to show. Generous below the band the detector reports
#: (0.91-0.97 on this source) so a line that sits lower is not cropped out of
#: the very image meant to reveal it.
BAND_Y0, BAND_Y1 = 0.83, 1.0
#: Magnified to this width. Wide enough to read a caption at 480-pixel source.
OUT_W = 1600
#: Rows per sheet. Eight keeps a sheet under 2000 px tall and lets the eye
#: compare left and right margins down a column, which is how "the same line at
#: the same position" gets confirmed across frames rather than assumed.
ROWS = 8


def _ruler(cv2: Any, np: Any, width: int, height: int = 26) -> Any:
    """A strip of ticks every 0.05 of the frame width, labelled."""
    strip = np.zeros((height, width, 3), dtype="uint8")
    for i in range(21):
        frac = i / 20.0
        x = min(width - 1, int(frac * width))
        tall = (i % 2 == 0)
        cv2.line(strip, (x, height - 1), (x, height - (12 if tall else 6)),
                 (255, 255, 255), 1)
        if tall:
            cv2.putText(strip, f"{frac:.2f}", (max(0, x - 14), 11),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.32, (200, 255, 200), 1)
    return strip


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project")
    ap.add_argument("clip")
    ap.add_argument("--from", dest="start_at", type=float, default=None,
                    help="only frames at or after this decoded time")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after this many frames (0 = all)")
    ap.add_argument("--y0", type=float, default=BAND_Y0,
                    help="top of the crop, as a fraction of frame height")
    ap.add_argument("--y1", type=float, default=BAND_Y1)
    ap.add_argument("--width", type=int, default=OUT_W,
                    help="magnify to this width; use 3200 to judge whether a "
                         "descender crosses the detector's box")
    ap.add_argument("--rows", type=int, default=ROWS)
    args = ap.parse_args()

    import cv2
    import numpy as np

    frames_dir = DATA / args.project / "caption_frames"
    samples_path = frames_dir / f"{args.clip}.samples.json"
    if not samples_path.exists():
        print(f"REFUSED: no samples at {samples_path}; run dump_caption_frames")
        return 2
    blob = json.loads(samples_path.read_text(encoding="utf-8"))
    rows = [s for s in blob["observation"]["samples"]
            if s.get("frame") is not None]
    if args.start_at is not None:
        rows = [s for s in rows if (s.get("t_decoded") or 0) >= args.start_at]
    if args.limit:
        rows = rows[:args.limit]
    if not rows:
        print("REFUSED: no samples left after filtering")
        return 2

    proxy = DATA / args.project / "proxy" / "proxy.mp4"
    cap = cv2.VideoCapture(str(proxy))
    tiles: list[Any] = []
    skipped = 0
    try:
        for sample in rows:
            cap.set(cv2.CAP_PROP_POS_FRAMES, sample["frame"])
            ok, frame = cap.read()
            if not ok or frame is None:
                # Counted, not silently dropped: a sheet one row short would
                # hide that a sample was never looked at.
                skipped += 1
                continue
            h, w = frame.shape[0], frame.shape[1]
            y0, y1 = int(args.y0 * h), min(h, int(args.y1 * h))
            band = frame[y0:y1, :]
            scale = args.width / float(w)
            band = cv2.resize(band, (args.width, int(band.shape[0] * scale)),
                              interpolation=cv2.INTER_CUBIC)
            for box in sample.get("boxes") or []:
                bx0, bx1 = int(box["x0"] * args.width), int(box["x1"] * args.width)
                by0 = int((box["y0"] * h - y0) * scale)
                by1 = int((box["y1"] * h - y0) * scale)
                cv2.rectangle(band, (bx0, by0), (bx1, by1), (0, 255, 0), 1)
            cv2.putText(band, f"t={sample.get('t_decoded')}", (6, 16),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
            tiles.append(cv2.vconcat([band, _ruler(cv2, np, args.width)]))
    finally:
        cap.release()

    if not tiles:
        print("REFUSED: no frame could be re-read")
        return 2
    made = []
    rows_per = max(1, args.rows)
    for page in range((len(tiles) + rows_per - 1) // rows_per):
        chunk = tiles[page * rows_per:(page + 1) * rows_per]
        sheet = cv2.vconcat(chunk)
        path = frames_dir / f"{args.clip}.band{args.width}.{page:02d}.jpg"
        cv2.imwrite(str(path), sheet, [cv2.IMWRITE_JPEG_QUALITY, 92])
        made.append(path.name)
        print(f"  {path.name}: {len(chunk)} rows")
    print(f"\n{len(made)} band sheet(s), {len(tiles)} rows, {skipped} skipped")
    return 0 if not skipped else 2


if __name__ == "__main__":
    raise SystemExit(main())
