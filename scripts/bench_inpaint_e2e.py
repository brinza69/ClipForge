"""
End-to-end inpaint benchmark on a real video.

Calls services.inpaint.inpaint_region() directly to measure full pipeline
(ffmpeg decode -> LaMa GPU -> ffmpeg encode with audio) on a real clip.
"""
import asyncio
import sys
import time
from pathlib import Path

# Add server/ to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

from services.inpaint import inpaint_region  # noqa: E402


async def main():
    if len(sys.argv) < 3:
        print("Usage: bench_inpaint_e2e.py <input.mp4> <output.mp4> [x y w h]")
        sys.exit(1)
    inp = sys.argv[1]
    out = sys.argv[2]
    if len(sys.argv) >= 7:
        x, y, w, h = (int(v) for v in sys.argv[3:7])
    else:
        # Default: bottom caption band on 1080x1920 vertical
        x, y, w, h = 0, 1600, 1080, 260

    progress_state = {"last": 0}

    def on_progress(done, total):
        if done - progress_state["last"] >= 60 or done == total:
            print(f"  progress: {done}/{total} frames ({done * 100 // max(1, total)}%)")
            progress_state["last"] = done

    print(f"Input  : {inp}")
    print(f"Output : {out}")
    print(f"Region : x={x} y={y} w={w} h={h}")
    print()

    t0 = time.perf_counter()
    await inpaint_region(inp, out, x=x, y=y, w=w, h=h, on_progress=on_progress)
    dt = time.perf_counter() - t0

    import cv2
    cap = cv2.VideoCapture(inp)
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    duration = frames / max(1, fps)
    cap.release()

    out_size = Path(out).stat().st_size

    print()
    print(f"  wall time   : {dt:.2f} s")
    print(f"  source      : {frames} frames @ {fps:.1f} fps ({duration:.1f}s real)")
    print(f"  processing  : {frames / dt:.1f} fps ({duration / dt:.2f}x realtime)")
    print(f"  output size : {out_size // 1024} KB")


if __name__ == "__main__":
    asyncio.run(main())
