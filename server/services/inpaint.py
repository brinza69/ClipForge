"""
ClipForge — Inpainting Service

Seamless region removal for captions, logos, and watermarks. Uses OpenCV's
cv2.inpaint (TELEA or Navier-Stokes) on every decoded frame, then streams
the inpainted frames into FFmpeg for H.264 encoding while muxing the
original audio track back in.

Gives much more natural results than naive boxblur/avgblur for text and
small logo regions.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable, Literal, Optional

import cv2
import numpy as np

logger = logging.getLogger("clipforge.inpaint")

Algorithm = Literal["telea", "ns"]


def _resolve_ffmpeg() -> str:
    from config import settings
    loc = settings.ffmpeg_location
    if loc:
        exe = Path(loc) / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if exe.exists():
            return str(exe)
    return shutil.which("ffmpeg") or "ffmpeg"


def _creationflags() -> int:
    # CREATE_NO_WINDOW on Windows so background ffmpeg doesn't flash a console
    return 0x08000000 if os.name == "nt" else 0


async def inpaint_region(
    input_path: str,
    output_path: str,
    x: int,
    y: int,
    w: int,
    h: int,
    *,
    algorithm: Algorithm = "telea",
    dilate_px: int = 6,
    inpaint_radius: int = 5,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> str:
    """
    Seamlessly remove a rectangular region from every frame of a video.

    The rect is specified in input-pixel coordinates (top-left origin).
    The mask is slightly dilated to catch anti-aliased text edges, then
    each frame is run through cv2.inpaint using TELEA or NS.

    Audio from the original video is muxed back into the output.
    """

    def _run() -> str:
        cap = cv2.VideoCapture(input_path)
        if not cap.isOpened():
            raise RuntimeError(f"Could not open input video: {input_path}")

        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        vw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        vh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0

        if vw == 0 or vh == 0:
            cap.release()
            raise RuntimeError("Video has zero dimensions")

        # Clamp mask rect to frame bounds
        mx = max(0, min(int(x), vw - 1))
        my = max(0, min(int(y), vh - 1))
        mw = max(1, min(int(w), vw - mx))
        mh = max(1, min(int(h), vh - my))

        # Build mask: white rect over the region to erase
        mask = np.zeros((vh, vw), dtype=np.uint8)
        mask[my:my + mh, mx:mx + mw] = 255
        if dilate_px > 0:
            k = np.ones((dilate_px, dilate_px), np.uint8)
            mask = cv2.dilate(mask, k, iterations=1)

        algo_flag = cv2.INPAINT_TELEA if algorithm == "telea" else cv2.INPAINT_NS

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        ffmpeg = _resolve_ffmpeg()
        cmd = [
            ffmpeg, "-y",
            "-loglevel", "warning",
            # Raw video from stdin
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-s", f"{vw}x{vh}",
            "-r", f"{fps:.6f}",
            "-i", "-",
            # Original file for audio
            "-i", input_path,
            # Encoding
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-map", "0:v:0",
            "-map", "1:a:0?",   # ? = optional, don't fail if input has no audio
            "-shortest",
            "-movflags", "+faststart",
            output_path,
        ]

        logger.info(
            f"Inpaint start: {vw}x{vh} @ {fps:.2f}fps, {total} frames, "
            f"rect=({mx},{my},{mw},{mh}), algo={algorithm}"
        )

        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_creationflags(),
        )

        # Drain stderr in a thread so the pipe can't fill up and deadlock
        stderr_buf: list[bytes] = []

        def _drain():
            for chunk in iter(lambda: proc.stderr.read(4096), b""):
                stderr_buf.append(chunk)

        drainer = threading.Thread(target=_drain, daemon=True)
        drainer.start()

        frame_idx = 0
        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                inpainted = cv2.inpaint(frame, mask, inpaint_radius, algo_flag)
                try:
                    proc.stdin.write(inpainted.tobytes())
                except (BrokenPipeError, OSError) as e:
                    # ffmpeg died — surface the stderr below
                    logger.warning(f"ffmpeg pipe closed early: {e}")
                    break
                frame_idx += 1
                if on_progress and total and frame_idx % 30 == 0:
                    try:
                        on_progress(frame_idx, total)
                    except Exception:
                        pass
        finally:
            cap.release()
            try:
                proc.stdin.close()
            except Exception:
                pass

        rc = proc.wait()
        drainer.join(timeout=10)
        stderr_text = b"".join(stderr_buf).decode("utf-8", errors="replace")

        if rc != 0:
            tail = "\n".join(stderr_text.strip().splitlines()[-8:])
            raise RuntimeError(f"ffmpeg failed (rc={rc}):\n{tail}")

        out = Path(output_path)
        if not out.exists() or out.stat().st_size < 1000:
            tail = "\n".join(stderr_text.strip().splitlines()[-8:])
            raise RuntimeError(f"inpaint output missing/too small. ffmpeg:\n{tail}")

        logger.info(f"Inpaint done: {frame_idx} frames, {out.stat().st_size // 1024} KB")
        return str(out)

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _run)
