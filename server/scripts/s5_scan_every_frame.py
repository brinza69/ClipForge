"""Scan EVERY frame's caption band with OCR — relics that flash for 2-3
frames at display transitions slip through a 5fps verify scan.

Usage: server/.venv/bin/python server/scripts/s5_scan_every_frame.py X Y W H VIDEO [VIDEO...]
"""

import sys
from pathlib import Path

_SERVER = Path(__file__).resolve().parent.parent
if str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))

DUMP = _SERVER.parent / "data" / "temp" / "s5_test"


def main() -> int:
    import cv2
    from services.caption_detector import _get_reader
    reader = _get_reader()

    bx, by, bw, bh = (int(v) for v in sys.argv[1:5])
    rc = 0
    for path in sys.argv[5:]:
        tag = Path(path).parent.name + "_" + Path(path).stem
        cap = cv2.VideoCapture(path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        i = leaks = 0
        print(f"=== {path} (every frame) ===", flush=True)
        while True:
            ok, fr = cap.read()
            if not ok:
                break
            crop = fr[by:by + bh, bx:bx + bw]
            if crop.size:
                for _b, text, conf in reader.readtext(crop):
                    if conf and conf > 0.3 and text and text.strip():
                        leaks += 1
                        cv2.imwrite(str(DUMP / f"ef_{tag}_{i:06d}.png"), fr)
                        print(f"  LEAK @f{i} t={i/fps:.2f}s: {text!r} conf={conf:.2f}",
                              flush=True)
                        break
            i += 1
        cap.release()
        print(f"  {i} frames, {leaks} leaks", flush=True)
        if leaks:
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
