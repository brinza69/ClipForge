"""
T20 eraser verification harness (throwaway — not part of the app).

Proves the three things that matter after an erase:
  1. NO LEAK   — re-OCR the OUTPUT band; any text left = a leak.
  2. TIGHTNESS — report erased-pixel proxy (not measured here; eyeball the
                 dumped frames + the mask the pipeline logs).
  3. SCENE-TEXT SAFE — pass a band; text OUTSIDE it should remain.

Usage (run in WSL, the venv is Linux):
    server/.venv/bin/python server/scripts/verify_eraser.py OUTPUT.mp4 X Y W H
where X Y W H is the caption band (the auto-located ROI the pipeline logged,
or the drawn rect). Omit X Y W H to scan the whole frame.

Exit code 0 = zero leaks, 1 = leaks found.
"""

import sys
from pathlib import Path

# Make `from services...` importable when run from repo root or server/.
_SERVER = Path(__file__).resolve().parent.parent
if str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))


def main() -> int:
    import cv2

    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    out_path = sys.argv[1]
    band = None
    if len(sys.argv) >= 6:
        band = tuple(int(v) for v in sys.argv[2:6])  # x, y, w, h

    from services.caption_detector import _get_reader
    reader = _get_reader()

    cap = cv2.VideoCapture(out_path)
    if not cap.isOpened():
        print(f"Could not open {out_path}")
        return 2
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    vw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    vh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if band is None:
        band = (0, 0, vw, vh)
    bx, by, bw, bh = band
    bx2, by2 = min(vw, bx + bw), min(vh, by + bh)

    dump_dir = Path("/tmp/clipforge_eraser_check")
    dump_dir.mkdir(parents=True, exist_ok=True)

    step = max(1, int(round(fps / 5)))  # 5 fps
    i = checked = leaks = 0
    print(f"Scanning {out_path} band={band} at ~5fps…")
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        if i % step == 0:
            checked += 1
            crop = fr[by:by2, bx:bx2]
            if crop.size:
                for _b, text, conf in reader.readtext(crop):
                    if conf and conf > 0.3 and text and text.strip():
                        leaks += 1
                        p = dump_dir / f"leak_{i:06d}.png"
                        cv2.imwrite(str(p), fr)
                        print(f"  LEAK @frame {i} t={i/fps:.2f}s: {text!r} "
                              f"conf={conf:.2f}  → {p}")
                        break
        i += 1
    cap.release()

    print(f"\nframes checked: {checked} | LEAKS: {leaks}  (target 0)")
    if leaks:
        print(f"leak frames dumped to {dump_dir}/ — eyeball them too "
              f"(a faint ghost can pass OCR but a human sees it).")
    print("TIGHTNESS: open the pipeline's logged mask overlay / dumped frames "
          "and confirm the erase hugs the glyphs (or box), not the whole band.")
    return 0 if leaks == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
