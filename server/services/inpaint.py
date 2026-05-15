"""
ClipForge — Inpainting Service

Seamless region removal for captions, logos, and watermarks. The pipeline is:

  decoder ffmpeg (multi-threaded H.264) → bgr24 raw frames → per-frame inpaint
  → bgr24 raw frames → encoder ffmpeg (NVENC if available, else libx264)
  → mp4 with audio remuxed from the original input.

The inpaint stage tries, in order:
  1. LaMa (GPU, neural inpainting via PyTorch + simple_lama_inpainting). Best
     quality; runs on the user's NVIDIA GPU when available.
  2. OpenCV TELEA/NS on a tight ROI crop around the mask. Much faster than
     calling cv2.inpaint on the full frame, since cv2's working buffers are
     proportional to the input image size — not just the mask area.

The decoder being a separate ffmpeg process is key: OpenCV's VideoCapture is
single-threaded and ~3× slower than ffmpeg for H.264 decode. Piping bgr24
frames out of ffmpeg lets libavcodec's threaded decoder feed the Python
inpaint loop while libx264/NVENC consumes the encoder side concurrently.
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


# ── Backend detection (cached) ───────────────────────────────────────────────

_LAMA_MODEL = None           # lazy singleton (SimpleLama instance)
_LAMA_AVAILABLE: Optional[bool] = None  # tri-state: None=not probed, True=ok, False=disabled
# fp16 is opt-in: LaMa uses Fast Fourier Convolution internally, and cuFFT
# in half precision only supports power-of-2 input dimensions. Caption ROIs
# rarely satisfy that, so default to fp32 and let advanced users opt in.
_LAMA_FP16 = os.environ.get("CLIPFORGE_LAMA_FP16", "0") == "1"
_LAMA_BATCH = max(1, int(os.environ.get("CLIPFORGE_LAMA_BATCH", "8")))

_NVENC_CACHE: dict[str, bool] = {}


def _resolve_ffmpeg() -> str:
    from config import settings
    loc = settings.ffmpeg_location
    if loc:
        exe = Path(loc) / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if exe.exists():
            return str(exe)
    return shutil.which("ffmpeg") or "ffmpeg"


def _creationflags() -> int:
    return 0x08000000 if os.name == "nt" else 0


def _has_nvenc(ffmpeg_bin: str) -> bool:
    """
    Probe whether h264_nvenc is *actually usable* on this machine.

    Just checking `-encoders` isn't enough — the encoder is compiled into the
    Gyan.dev build but fails at runtime if the NVIDIA driver is older than the
    nvenc API version ffmpeg was built against (e.g. ffmpeg 8.1.1 needs driver
    570+). So we open a one-frame test encode and trust the exit code.
    """
    if ffmpeg_bin in _NVENC_CACHE:
        return _NVENC_CACHE[ffmpeg_bin]
    try:
        # Listed at all?
        listed = subprocess.run(
            [ffmpeg_bin, "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=5,
            creationflags=_creationflags(),
        )
        if "h264_nvenc" not in (listed.stdout or ""):
            _NVENC_CACHE[ffmpeg_bin] = False
            return False
        # Real probe: encode 1 frame to a discard sink.
        probe = subprocess.run(
            [
                ffmpeg_bin, "-y", "-hide_banner", "-v", "error",
                "-f", "lavfi", "-i", "color=c=black:s=64x64:r=30",
                "-frames:v", "1",
                "-c:v", "h264_nvenc",
                "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=10,
            creationflags=_creationflags(),
        )
        ok = probe.returncode == 0
        if not ok:
            tail = "\n".join((probe.stderr or "").strip().splitlines()[-3:])
            logger.info(f"NVENC unavailable (will use libx264). ffmpeg said: {tail}")
        else:
            logger.info("NVENC available — using h264_nvenc for encoding")
    except Exception as e:
        logger.info(f"NVENC probe failed (will use libx264): {e}")
        ok = False
    _NVENC_CACHE[ffmpeg_bin] = ok
    return ok


def _try_load_lama():
    """Return a SimpleLama instance, or None if torch/lama isn't installed."""
    global _LAMA_MODEL, _LAMA_AVAILABLE
    if _LAMA_AVAILABLE is False:
        return None
    if _LAMA_MODEL is not None:
        return _LAMA_MODEL
    try:
        import torch  # noqa: F401
        from simple_lama_inpainting import SimpleLama
    except Exception as e:
        if _LAMA_AVAILABLE is None:
            logger.info(f"LaMa GPU inpainting unavailable, will fall back to OpenCV: {e}")
        _LAMA_AVAILABLE = False
        return None
    try:
        _LAMA_MODEL = SimpleLama()
        if _LAMA_FP16:
            try:
                _LAMA_MODEL.model = _LAMA_MODEL.model.half()
                logger.info("LaMa converted to fp16 (half-precision)")
            except Exception as e:
                logger.warning(f"LaMa fp16 conversion failed, staying on fp32: {e}")
        _LAMA_AVAILABLE = True
        logger.info(f"LaMa model loaded (GPU inpainting, batch={_LAMA_BATCH})")
        return _LAMA_MODEL
    except Exception as e:
        logger.warning(f"LaMa load failed, falling back to OpenCV: {e}")
        _LAMA_AVAILABLE = False
        return None


# ── Public API ────────────────────────────────────────────────────────────────

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

    The rect is in input-pixel coordinates (top-left origin). Audio from the
    original video is muxed back into the output.
    """

    def _run() -> str:
        # ── Metadata (one-shot read, then release cv2) ───────────────────────
        cap = cv2.VideoCapture(input_path)
        if not cap.isOpened():
            raise RuntimeError(f"Could not open input video: {input_path}")
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        vw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        vh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        cap.release()
        if vw == 0 or vh == 0:
            raise RuntimeError("Video has zero dimensions")

        # ── Clamp mask rect to frame ─────────────────────────────────────────
        mx = max(0, min(int(x), vw - 1))
        my = max(0, min(int(y), vh - 1))
        mw = max(1, min(int(w), vw - mx))
        mh = max(1, min(int(h), vh - my))

        # ROI with margin around the mask for inpaint context.
        roi_margin = max(dilate_px, 0) + max(inpaint_radius, 0) + 8
        rx = max(0, mx - roi_margin)
        ry = max(0, my - roi_margin)
        rx2 = min(vw, mx + mw + roi_margin)
        ry2 = min(vh, my + mh + roi_margin)
        roi_h = ry2 - ry
        roi_w = rx2 - rx

        # ROI-local mask (white = erase, black = keep)
        mask_roi = np.zeros((roi_h, roi_w), dtype=np.uint8)
        mask_roi[my - ry : my - ry + mh, mx - rx : mx - rx + mw] = 255
        if dilate_px > 0:
            k = np.ones((dilate_px, dilate_px), np.uint8)
            mask_roi = cv2.dilate(mask_roi, k, iterations=1)

        algo_flag = cv2.INPAINT_TELEA if algorithm == "telea" else cv2.INPAINT_NS

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        ffmpeg = _resolve_ffmpeg()
        frame_nbytes = vw * vh * 3

        # ── Choose inpaint backend ───────────────────────────────────────────
        lama = _try_load_lama()
        use_lama = lama is not None

        # ── Decoder (multi-threaded H.264 → bgr24 stdout) ────────────────────
        dec_cmd = [
            ffmpeg,
            "-loglevel", "warning",
            "-threads", "0",
            "-i", input_path,
            "-map", "0:v:0",
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-",
        ]

        # ── Encoder: NVENC if available, libx264 otherwise ───────────────────
        if _has_nvenc(ffmpeg):
            enc_video_args = [
                "-c:v", "h264_nvenc",
                "-preset", "p4",          # balanced quality/speed
                "-tune", "hq",
                "-rc", "vbr",
                "-cq", "23",
                "-b:v", "0",
                "-pix_fmt", "yuv420p",
            ]
            enc_label = "h264_nvenc"
        else:
            enc_video_args = [
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "20",
                "-pix_fmt", "yuv420p",
            ]
            enc_label = "libx264"

        enc_cmd = [
            ffmpeg, "-y",
            "-loglevel", "warning",
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-s", f"{vw}x{vh}",
            "-r", f"{fps:.6f}",
            "-i", "-",
            "-i", input_path,
            *enc_video_args,
            "-c:a", "aac",
            "-b:a", "192k",
            "-map", "0:v:0",
            "-map", "1:a:0?",
            "-shortest",
            "-movflags", "+faststart",
            output_path,
        ]

        backend = "LaMa(GPU)" if use_lama else f"cv2.{algorithm}"
        logger.info(
            f"Inpaint start: {vw}x{vh} @ {fps:.2f}fps, {total} frames, "
            f"rect=({mx},{my},{mw},{mh}), roi=({rx},{ry},{roi_w},{roi_h}), "
            f"inpaint={backend}, encoder={enc_label}"
        )

        dec = subprocess.Popen(
            dec_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_creationflags(),
        )
        enc = subprocess.Popen(
            enc_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_creationflags(),
        )

        # Drain both stderrs concurrently so neither pipe fills up.
        dec_stderr_buf: list[bytes] = []
        enc_stderr_buf: list[bytes] = []

        def _drain(stream, buf):
            for chunk in iter(lambda: stream.read(4096), b""):
                buf.append(chunk)

        dec_drainer = threading.Thread(target=_drain, args=(dec.stderr, dec_stderr_buf), daemon=True)
        enc_drainer = threading.Thread(target=_drain, args=(enc.stderr, enc_stderr_buf), daemon=True)
        dec_drainer.start()
        enc_drainer.start()

        # Pre-build LaMa-side state that's constant across frames:
        #   * mask padded to a multiple of 8 (LaMa downsamples 3× by 2)
        #   * the padded mask tensor on-device, in the right dtype.
        # We bypass SimpleLama.__call__ (which forces batch=1) and call the
        # underlying torch.jit model directly with a batched tensor.
        mask_tensor = None
        roi_h_pad = roi_w_pad = 0
        device = None
        if use_lama:
            import torch
            device = lama.device
            roi_h_pad = ((roi_h + 7) // 8) * 8
            roi_w_pad = ((roi_w + 7) // 8) * 8
            m_arr = np.zeros((roi_h_pad, roi_w_pad), dtype=np.float32)
            m_arr[:roi_h, :roi_w] = (mask_roi > 0).astype(np.float32)
            mask_tensor = torch.from_numpy(m_arr).unsqueeze(0).unsqueeze(0).to(device)  # [1,1,H,W]
            if _LAMA_FP16:
                mask_tensor = mask_tensor.half()

        # Buffers for the LaMa batch.
        frame_buf: list[np.ndarray] = []   # full-frame numpy refs, write-back targets
        roi_rgb_buf: list[np.ndarray] = [] # padded RGB float views fed to LaMa

        def _flush_lama_batch():
            if not roi_rgb_buf:
                return
            import torch
            B = len(roi_rgb_buf)
            batch = np.stack(roi_rgb_buf, axis=0).astype(np.float32) / 255.0  # [B,H,W,3]
            batch = np.transpose(batch, (0, 3, 1, 2))                          # [B,3,H,W]
            img_t = torch.from_numpy(batch).to(device, non_blocking=True)
            if _LAMA_FP16:
                img_t = img_t.half()
            mask_b = mask_tensor.expand(B, -1, -1, -1).contiguous()
            with torch.inference_mode():
                out = lama.model(img_t, mask_b)
            # Output is [B,3,H,W] in [0,1] RGB at padded size; trim back to ROI and to BGR.
            out = out.float().clamp(0, 1)
            out = (out * 255.0).to(torch.uint8).permute(0, 2, 3, 1).cpu().numpy()  # [B,H,W,3] RGB uint8
            out = out[:, :roi_h, :roi_w, ::-1]  # crop padding + RGB->BGR (writeable copy via slice + reverse)
            for i, f in enumerate(frame_buf):
                f[ry:ry2, rx:rx2] = out[i]
                try:
                    enc.stdin.write(f.tobytes())
                except (BrokenPipeError, OSError) as e:
                    logger.warning(f"ffmpeg encoder pipe closed early: {e}")
                    return False
            frame_buf.clear()
            roi_rgb_buf.clear()
            return True

        frame_idx = 0
        try:
            while True:
                buf = dec.stdout.read(frame_nbytes)
                if not buf or len(buf) < frame_nbytes:
                    if use_lama:
                        if _flush_lama_batch() is False:
                            break
                    break
                # Mutable view so the ROI write-back is in-place.
                frame = np.frombuffer(bytearray(buf), dtype=np.uint8).reshape((vh, vw, 3))

                if use_lama:
                    # Pad ROI to (roi_h_pad, roi_w_pad), convert BGR -> RGB.
                    roi = frame[ry:ry2, rx:rx2]
                    roi_rgb = np.zeros((roi_h_pad, roi_w_pad, 3), dtype=np.uint8)
                    roi_rgb[:roi_h, :roi_w] = roi[..., ::-1]  # BGR -> RGB
                    frame_buf.append(frame)
                    roi_rgb_buf.append(roi_rgb)
                    if len(roi_rgb_buf) >= _LAMA_BATCH:
                        if _flush_lama_batch() is False:
                            break
                else:
                    roi = frame[ry:ry2, rx:rx2]
                    inpainted_roi = cv2.inpaint(roi, mask_roi, inpaint_radius, algo_flag)
                    frame[ry:ry2, rx:rx2] = inpainted_roi
                    try:
                        enc.stdin.write(frame.tobytes())
                    except (BrokenPipeError, OSError) as e:
                        logger.warning(f"ffmpeg encoder pipe closed early: {e}")
                        break

                frame_idx += 1
                if on_progress and total and frame_idx % 15 == 0:
                    try:
                        on_progress(frame_idx, total)
                    except Exception:
                        pass
        finally:
            try:
                enc.stdin.close()
            except Exception:
                pass
            try:
                dec.stdout.close()
            except Exception:
                pass

        dec_rc = dec.wait()
        enc_rc = enc.wait()
        dec_drainer.join(timeout=10)
        enc_drainer.join(timeout=10)
        dec_err = b"".join(dec_stderr_buf).decode("utf-8", errors="replace")
        enc_err = b"".join(enc_stderr_buf).decode("utf-8", errors="replace")

        if enc_rc != 0:
            tail = "\n".join(enc_err.strip().splitlines()[-8:])
            raise RuntimeError(f"ffmpeg encoder failed (rc={enc_rc}):\n{tail}")
        if dec_rc != 0 and total and frame_idx < total * 0.95:
            tail = "\n".join(dec_err.strip().splitlines()[-8:])
            raise RuntimeError(f"ffmpeg decoder failed (rc={dec_rc}, {frame_idx}/{total} frames):\n{tail}")

        out = Path(output_path)
        if not out.exists() or out.stat().st_size < 1000:
            tail = "\n".join(enc_err.strip().splitlines()[-8:])
            raise RuntimeError(f"inpaint output missing/too small. ffmpeg:\n{tail}")

        logger.info(f"Inpaint done: {frame_idx} frames, {out.stat().st_size // 1024} KB")
        return str(out)

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _run)


# ── LaMa per-frame helper ────────────────────────────────────────────────────

def _inpaint_lama(lama, roi_bgr: np.ndarray, pil_mask) -> np.ndarray:
    """Run LaMa on a single BGR ROI; returns BGR ROI of identical shape."""
    from PIL import Image
    # BGR → RGB for PIL
    rgb = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(rgb)
    out = lama(pil_img, pil_mask)
    # simple-lama returns a PIL Image in RGB; convert back to BGR uint8
    arr = np.array(out, dtype=np.uint8)
    if arr.ndim == 3 and arr.shape[2] == 3:
        arr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    # LaMa may pad to multiples of 8; trim/resize to exact ROI shape
    if arr.shape[:2] != roi_bgr.shape[:2]:
        arr = cv2.resize(arr, (roi_bgr.shape[1], roi_bgr.shape[0]))
    return arr
