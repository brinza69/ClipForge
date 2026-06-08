"""
ClipForge — AI Background Removal

Runs U²-Net (via rembg) frame-by-frame on a commentator video to produce a
WebM with a real alpha channel. The chroma-key path keeps working as the
fallback; this is what gets you "CapCut-style background remove" — works on
any background, no green screen required.

Pipeline:

    input.mp4 ─► ffmpeg extract frames ─► PIL.Image
                                              │
                                              ▼
                                  rembg.remove(session=u2net)
                                              │
                                              ▼
                          frame_NNNN.png (RGBA, real alpha)
                                              │
                                              ▼
                  ffmpeg -i frame_%04d.png -c:v libvpx-vp9 -pix_fmt yuva420p
                                              │
                                              ▼
                                       processed.webm

WebM/VP9 is the only widely-supported video container with a real per-pixel
alpha channel, so the overlay stage can drop chromakey entirely when the
preset has been "AI-processed" — the alpha is baked in.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

from config import settings

logger = logging.getLogger("clipforge.bg_removal")

# Singleton session — rembg's model load is the slow part (~5s), so we cache
# the loaded session in the worker process.
_session = None
_session_lock = None
_DEFAULT_MODEL = os.environ.get("CLIPFORGE_REMBG_MODEL", "u2net")


def _get_session():
    """Lazy-initialize the rembg session. Tries GPU first, falls back to CPU."""
    global _session, _session_lock
    if _session is not None:
        return _session
    if _session_lock is None:
        import threading
        _session_lock = threading.Lock()
    with _session_lock:
        if _session is not None:
            return _session
        from rembg import new_session

        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        logger.info(f"Loading rembg model={_DEFAULT_MODEL} providers={providers}…")
        t0 = time.time()
        try:
            sess = new_session(model_name=_DEFAULT_MODEL, providers=providers)
            actual = sess.inner_session.get_providers() if hasattr(sess, "inner_session") else providers
            logger.info(f"rembg session ready in {time.time() - t0:.1f}s — providers={actual}")
            _session = sess
            return sess
        except Exception as e:
            logger.exception(f"rembg session load failed: {e}")
            raise


# ── ffmpeg helpers (mirror commentator_overlay) ─────────────────────────────


def _ffmpeg() -> str:
    loc = settings.ffmpeg_location
    if loc:
        exe = Path(loc) / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if exe.exists():
            return str(exe)
    return shutil.which("ffmpeg") or "ffmpeg"


def _ffprobe() -> str:
    f = _ffmpeg()
    p = f.replace("ffmpeg", "ffprobe")
    if Path(p).exists() or p == "ffprobe":
        return p
    return shutil.which("ffprobe") or "ffprobe"


def _creationflags() -> int:
    return 0x08000000 if os.name == "nt" else 0


def _probe_meta(path: str) -> dict:
    r = subprocess.run(
        [_ffprobe(), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,avg_frame_rate,nb_frames,duration",
         "-of", "default=noprint_wrappers=1", str(path)],
        capture_output=True, text=True, creationflags=_creationflags(),
        timeout=60,
    )
    out: dict = {}
    for line in (r.stdout or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def remove_background_video(
    src_video: str,
    dst_webm: str,
    *,
    fps_override: Optional[float] = None,
    on_progress: Optional[Callable[[float, str], None]] = None,
) -> dict:
    """
    Process every frame through rembg and write a WebM with real alpha.

    `on_progress(fraction, message)` is called after every batch of frames.
    Returns a small stats dict.
    """
    from PIL import Image

    src_meta = _probe_meta(src_video)
    width = int(src_meta.get("width", 0))
    height = int(src_meta.get("height", 0))
    if not width or not height:
        raise RuntimeError(f"Could not probe video dims: {src_video}")

    # Parse frame rate (e.g. "30/1")
    fr = src_meta.get("avg_frame_rate") or "30/1"
    if "/" in fr:
        num, den = fr.split("/", 1)
        try:
            fps = float(num) / max(1.0, float(den))
        except Exception:
            fps = 30.0
    else:
        try:
            fps = float(fr)
        except Exception:
            fps = 30.0
    if fps_override:
        fps = float(fps_override)

    duration = float(src_meta.get("duration") or 0)
    expected_frames = int(round(duration * fps)) or int(src_meta.get("nb_frames") or 0)

    work = Path(dst_webm).parent / f".bgremove_{int(time.time())}"
    work.mkdir(parents=True, exist_ok=True)
    frames_in = work / "in"
    frames_out = work / "out"
    frames_in.mkdir(exist_ok=True)
    frames_out.mkdir(exist_ok=True)

    try:
        # 1) Extract every frame as PNG so PIL can read them directly.
        #    `image2` muxer with %06d gives us deterministic ordering.
        if on_progress:
            on_progress(0.0, "Extracting frames…")
        extract_cmd = [
            _ffmpeg(), "-y", "-loglevel", "error",
            "-i", str(src_video),
            "-vf", f"fps={fps:.6f}",
            "-q:v", "1",
            str(frames_in / "f_%06d.png"),
        ]
        r = subprocess.run(extract_cmd, capture_output=True, text=True, creationflags=_creationflags(), timeout=1800)
        if r.returncode != 0:
            raise RuntimeError(f"frame extract failed: {(r.stderr or '')[-500:]}")

        frame_files = sorted(frames_in.glob("f_*.png"))
        total = len(frame_files)
        if total == 0:
            raise RuntimeError("no frames extracted from source video")
        logger.info(
            f"bg_removal: extracted {total} frames at {fps:.2f}fps "
            f"({width}x{height}, expected ~{expected_frames})"
        )

        # 2) Run rembg on each frame. Reuse the same session for speed.
        from rembg import remove
        sess = _get_session()

        t0 = time.time()
        done_frames = 0
        for i, f in enumerate(frame_files):
            img = Image.open(f)
            out = remove(img, session=sess)
            out_path = frames_out / f.name
            out.save(out_path, "PNG")
            done_frames += 1
            # Throttle progress to ~50 callbacks so we don't spam.
            if on_progress and (done_frames == 1 or done_frames % max(1, total // 50) == 0 or done_frames == total):
                elapsed = time.time() - t0
                fps_proc = done_frames / max(0.001, elapsed)
                eta = (total - done_frames) / max(0.001, fps_proc)
                on_progress(
                    0.05 + 0.85 * (done_frames / total),
                    f"AI keying frame {done_frames}/{total} ({fps_proc:.1f} fps, ETA {eta:.0f}s)",
                )

        logger.info(f"bg_removal: processed {total} frames in {time.time() - t0:.1f}s")

        # 3) Re-encode the RGBA PNG sequence as WebM VP9 with real alpha.
        if on_progress:
            on_progress(0.92, "Encoding WebM with alpha…")
        encode_cmd = [
            _ffmpeg(), "-y", "-loglevel", "error",
            "-framerate", f"{fps:.6f}",
            "-i", str(frames_out / "f_%06d.png"),
            "-c:v", "libvpx-vp9",
            "-pix_fmt", "yuva420p",
            "-b:v", "0", "-crf", "30",
            "-row-mt", "1",
            "-auto-alt-ref", "0",        # required for alpha
            str(dst_webm),
        ]
        r = subprocess.run(encode_cmd, capture_output=True, text=True, creationflags=_creationflags(), timeout=1800)
        if r.returncode != 0:
            raise RuntimeError(f"webm encode failed: {(r.stderr or '')[-500:]}")
        if on_progress:
            on_progress(1.0, "Done")

        out_meta = _probe_meta(dst_webm)
        # ffprobe returns the literal string "N/A" for VP9 alpha webm streams
        # because the duration lives in the format header, not on the stream.
        # Wrap the conversion so that's tolerated.
        try:
            out_dur = float(out_meta.get("duration") or 0)
        except (TypeError, ValueError):
            out_dur = 0.0
        return {
            "model": _DEFAULT_MODEL,
            "frame_count": total,
            "fps": round(fps, 3),
            "width": width,
            "height": height,
            "output_size": Path(dst_webm).stat().st_size,
            "output_duration": out_dur,
        }

    finally:
        # Clean intermediate PNGs — they balloon disk usage fast.
        try:
            shutil.rmtree(work, ignore_errors=True)
        except Exception:
            pass


def is_available() -> tuple[bool, Optional[str]]:
    """Cheap probe — does NOT load the model."""
    try:
        import rembg  # noqa
        return True, None
    except ImportError:
        return False, "Run: pip install rembg onnxruntime-gpu"
