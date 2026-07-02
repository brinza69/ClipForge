"""
ClipForge — Inpainting Service

Seamless region removal for captions, logos, and watermarks. Two modes:

  - Static rect  → pass x/y/w/h. One mask, every frame.
  - Time-varying → pass `segments=[{start_t,end_t,x,y,w,h}, ...]`. Each
                   segment defines a rect that's active only during its
                   time range. Frames outside any segment pass through
                   the encoder unchanged (huge speedup for sparse captions).

The pipeline is:

  decoder ffmpeg (multi-threaded H.264) → bgr24 raw frames → per-frame inpaint
  → bgr24 raw frames → encoder ffmpeg (NVENC if available, else libx264)
  → mp4 with audio remuxed from the original input.

The inpaint stage tries, in order:
  1. LaMa (GPU, neural inpainting via simple_lama_inpainting). Best quality;
     runs on the user's NVIDIA GPU. We batch frames from the same active
     segment to keep the GPU fed.
  2. OpenCV TELEA/NS on a tight ROI crop. cv2.inpaint's working buffers are
     proportional to the input size, so cropping to the ROI before calling
     it is far faster than full-frame inpaint.

Separate decoder/encoder processes give parallel decode + inpaint + encode.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Literal, Optional

import cv2
import numpy as np

logger = logging.getLogger("clipforge.inpaint")

Algorithm = Literal["telea", "ns"]


# ── Backend detection (cached) ───────────────────────────────────────────────

_LAMA_MODEL = None
_LAMA_AVAILABLE: Optional[bool] = None
# fp16 is opt-in: LaMa uses Fast Fourier Convolution internally, and cuFFT
# in half precision only supports power-of-2 input dimensions. Caption ROIs
# rarely satisfy that, so default to fp32 and let advanced users opt in.
_LAMA_FP16 = os.environ.get("CLIPFORGE_LAMA_FP16", "0") == "1"
# Batch size: 16 is the sweet spot on 8GB cards — bigger thrashes VRAM on the
# FFC layers (~1.4GB at B=8, ~2.6GB at B=16, ~5GB at B=32 with slowdown). On
# cards with >=12GB we auto-raise to 24 at load time (more headroom → better
# GPU utilisation). An explicit CLIPFORGE_LAMA_BATCH env var overrides both.
# NOTE: 8GB cards (e.g. RTX 2080 Super) stay at exactly 16 — unchanged.
_LAMA_BATCH_ENV = os.environ.get("CLIPFORGE_LAMA_BATCH")
_LAMA_BATCH = max(1, int(_LAMA_BATCH_ENV)) if _LAMA_BATCH_ENV else 16

_NVENC_CACHE: dict[str, bool] = {}

# ── Degenerate-patch repair (S5) ─────────────────────────────────────────────
# On bright high-dynamic scenes (fire), LaMa occasionally hallucinates a small
# dark garbage block inside the masked area instead of continuing the
# background (seen as 2 black squares where "the salt" was erased). Detector:
# a connected blob of masked pixels that is MUCH darker than the ring of
# unmasked pixels around the mask. Repair: redo that frame's mask with
# diffusion inpaint (telea), which cannot hallucinate. Runs per LaMa output
# frame (~1ms on a caption ROI); legit dark fills next to dark surroundings
# never trigger (the ring is dark too).
_REPAIR_DARK_DELTA = 75   # blob must be this much darker than the ring mean
_REPAIR_MIN_AREA = 64     # ignore specks below this many pixels


def _patch_degenerate(patch_bgr: np.ndarray, mask_roi: np.ndarray) -> bool:
    """True when a LaMa output patch contains a dark garbage blob inside the
    mask, judged against the brightness just outside the mask."""
    m = mask_roi > 0
    if not m.any():
        return False
    gray = cv2.cvtColor(patch_bgr, cv2.COLOR_BGR2GRAY)
    ring = (cv2.dilate(mask_roi, np.ones((15, 15), np.uint8)) > 0) & ~m
    if not ring.any():
        return False
    ring_mean = float(gray[ring].mean())
    dark = ((gray < ring_mean - _REPAIR_DARK_DELTA) & m).astype(np.uint8)
    if int(dark.sum()) < _REPAIR_MIN_AREA:
        return False
    n, _lbl, stats, _c = cv2.connectedComponentsWithStats(dark, connectivity=8)
    return any(int(stats[k, cv2.CC_STAT_AREA]) >= _REPAIR_MIN_AREA
               for k in range(1, n))


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

    Just checking `-encoders` isn't enough — the encoder is compiled in but
    fails at runtime if the NVIDIA driver is older than the nvenc API
    version ffmpeg was built against (e.g. ffmpeg 8.1.1 needs driver 570+).
    Real one-frame probe and cache the result.
    """
    if ffmpeg_bin in _NVENC_CACHE:
        return _NVENC_CACHE[ffmpeg_bin]
    try:
        listed = subprocess.run(
            [ffmpeg_bin, "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=5,
            creationflags=_creationflags(),
        )
        if "h264_nvenc" not in (listed.stdout or ""):
            _NVENC_CACHE[ffmpeg_bin] = False
            return False
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
    # Autotune cuDNN kernels for the recurring ROI shape. ~1.5x speedup on
    # batched LaMa runs where every batch has identical (B,C,H,W).
    torch.backends.cudnn.benchmark = True
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True

    # Auto-size the batch to the card so it runs on ANY GPU without manual
    # tuning. Only when the user did NOT pin CLIPFORGE_LAMA_BATCH.
    #   >=12GB (RTX 3060/4070…) → 24   |   >=8GB (RTX 2080 Super…) → 16
    #   >=6GB  (GTX 1660 Super…) → 8    |   <6GB                    → 4
    # Bigger batches thrash VRAM on the FFC layers, so smaller cards must use a
    # smaller batch or the erase stage OOMs.
    global _LAMA_BATCH
    if not _LAMA_BATCH_ENV:
        try:
            if torch.cuda.is_available():
                vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
                if vram_gb >= 11.5:
                    _LAMA_BATCH = 24
                elif vram_gb >= 7.5:
                    _LAMA_BATCH = 16
                elif vram_gb >= 5.5:
                    _LAMA_BATCH = 8
                else:
                    _LAMA_BATCH = 4
                logger.info(f"VRAM {vram_gb:.1f}GB → LaMa batch auto-set to {_LAMA_BATCH}")
        except Exception as e:
            logger.warning(f"VRAM probe failed, keeping LaMa batch={_LAMA_BATCH}: {e}")
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


# ── Per-segment precomputed state ────────────────────────────────────────────

@dataclass
class _SegState:
    """All the precomputed bits we need to inpaint frames of one segment."""
    start_t: float
    end_t: float
    rx: int
    ry: int
    rx2: int
    ry2: int
    roi_h: int
    roi_w: int
    mask_roi: np.ndarray            # uint8, shape (roi_h, roi_w)
    # LaMa-only state (None when LaMa is not in use)
    mask_tensor: object = None      # torch.Tensor [1,1,H_pad,W_pad]
    roi_h_pad: int = 0
    roi_w_pad: int = 0


def _build_segment_state(
    seg: dict,
    vw: int,
    vh: int,
    dilate_px: int,
    inpaint_radius: int,
    lama,
    device,
) -> _SegState:
    x = int(seg["x"]); y = int(seg["y"])
    w = int(seg["w"]); h = int(seg["h"])

    mx = max(0, min(x, vw - 1))
    my = max(0, min(y, vh - 1))
    mw = max(1, min(w, vw - mx))
    mh = max(1, min(h, vh - my))

    # ROI with margin around the mask for inpaint context.
    roi_margin = max(dilate_px, 0) + max(inpaint_radius, 0) + 8
    rx = max(0, mx - roi_margin)
    ry = max(0, my - roi_margin)
    rx2 = min(vw, mx + mw + roi_margin)
    ry2 = min(vh, my + mh + roi_margin)
    roi_h = ry2 - ry
    roi_w = rx2 - rx

    # T20: a segment may carry an arbitrary full-frame `mask` (tight per-glyph
    # or box mask). When present, crop it to this ROI instead of filling the
    # whole rectangle — that's what makes the erase minimal/clear. Falls back
    # to the filled rectangle (legacy behaviour) when no mask is given.
    seg_mask = seg.get("mask")
    mask_roi = np.zeros((roi_h, roi_w), dtype=np.uint8)
    used_arbitrary = False
    if seg_mask is not None:
        try:
            sub = np.asarray(seg_mask)[ry:ry2, rx:rx2]
            if sub.shape[:2] == (roi_h, roi_w) and sub.any():
                mask_roi = (sub > 0).astype(np.uint8) * 255
                used_arbitrary = True
        except Exception:
            used_arbitrary = False
    if not used_arbitrary:
        mask_roi[my - ry : my - ry + mh, mx - rx : mx - rx + mw] = 255
    if dilate_px > 0:
        k = np.ones((dilate_px, dilate_px), np.uint8)
        mask_roi = cv2.dilate(mask_roi, k, iterations=1)

    state = _SegState(
        start_t=float(seg.get("start_t", 0.0)),
        end_t=float(seg.get("end_t", float("inf"))),
        rx=rx, ry=ry, rx2=rx2, ry2=ry2,
        roi_h=roi_h, roi_w=roi_w,
        mask_roi=mask_roi,
    )

    if lama is not None:
        import torch
        roi_h_pad = ((roi_h + 7) // 8) * 8
        roi_w_pad = ((roi_w + 7) // 8) * 8
        m_arr = np.zeros((roi_h_pad, roi_w_pad), dtype=np.float32)
        m_arr[:roi_h, :roi_w] = (mask_roi > 0).astype(np.float32)
        mask_tensor = torch.from_numpy(m_arr).unsqueeze(0).unsqueeze(0).to(device)
        if _LAMA_FP16:
            mask_tensor = mask_tensor.half()
        state.mask_tensor = mask_tensor
        state.roi_h_pad = roi_h_pad
        state.roi_w_pad = roi_w_pad

    return state


# ── Public API ────────────────────────────────────────────────────────────────

async def inpaint_region(
    input_path: str,
    output_path: str,
    x: int = 0,
    y: int = 0,
    w: int = 0,
    h: int = 0,
    *,
    segments: Optional[list[dict]] = None,
    algorithm: Algorithm = "telea",
    dilate_px: int = 6,
    inpaint_radius: int = 5,
    on_progress: Optional[Callable[[int, int], None]] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> str:
    """
    Seamlessly remove one or more rectangular regions from a video.

    Audio from the original is muxed back into the output.

    `is_cancelled`, if provided, is polled each frame; when it returns True the
    ffmpeg decode/encode subprocesses are killed and JobCancelledError is
    raised so the pipeline stops promptly (asyncio task.cancel() alone can't
    interrupt this run_in_executor thread).
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

        # Normalize "static rect" into a single full-duration segment so the
        # rest of the pipeline only deals with one code path.
        use_segments = segments is not None and len(segments) > 0
        if use_segments:
            seg_list = list(segments)
        else:
            seg_list = [{
                "start_t": 0.0, "end_t": float("inf"),
                "x": x, "y": y, "w": w, "h": h,
            }]

        # ── Choose inpaint backend ───────────────────────────────────────────
        lama = _try_load_lama()
        use_lama = lama is not None
        device = None
        if use_lama:
            device = lama.device

        # Build per-segment state (mask, ROI bounds, LaMa tensors)
        seg_states: List[_SegState] = [
            _build_segment_state(s, vw, vh, dilate_px, inpaint_radius, lama, device)
            for s in seg_list
        ]

        algo_flag = cv2.INPAINT_TELEA if algorithm == "telea" else cv2.INPAINT_NS

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        ffmpeg = _resolve_ffmpeg()
        frame_nbytes = vw * vh * 3

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
        # This is now an INTERMEDIATE feeding the fused speed-match+caption
        # pass, which is the only real quality encode. So keep it near-lossless
        # (low cq/crf) to minimise generational loss, while staying fast.
        if _has_nvenc(ffmpeg):
            enc_video_args = [
                "-c:v", "h264_nvenc",
                "-preset", "p4",
                "-tune", "hq",
                "-rc", "vbr",
                "-cq", "17",
                "-b:v", "0",
                "-pix_fmt", "yuv420p",
            ]
            enc_label = "h264_nvenc"
        else:
            # ultrafast keeps the encoder from being the end-to-end bottleneck
            # (~2x faster vs veryfast on 1080p). crf 16 makes this intermediate
            # near-visually-lossless so the fused final pass has clean bits.
            enc_video_args = [
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-tune", "fastdecode",
                "-crf", "16",
                "-threads", "0",
                "-pix_fmt", "yuv420p",
            ]
            enc_label = "libx264:ultrafast"

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
            f"mode={'segments' if use_segments else 'static'} "
            f"({len(seg_states)} seg), inpaint={backend}, encoder={enc_label}"
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

        dec_stderr_buf: list[bytes] = []
        enc_stderr_buf: list[bytes] = []

        def _drain(stream, buf):
            for chunk in iter(lambda: stream.read(4096), b""):
                buf.append(chunk)

        dec_drainer = threading.Thread(target=_drain, args=(dec.stderr, dec_stderr_buf), daemon=True)
        enc_drainer = threading.Thread(target=_drain, args=(enc.stderr, enc_stderr_buf), daemon=True)
        dec_drainer.start()
        enc_drainer.start()

        # ── Batched LaMa state ───────────────────────────────────────────────
        # Buffers hold frames for the CURRENT batch (same segment).
        batch_seg: Optional[_SegState] = None
        frame_buf: list[np.ndarray] = []
        roi_rgb_buf: list[np.ndarray] = []
        repair_count = [0]  # frames whose LaMa patch was redone with telea

        def _flush_lama_batch() -> bool:
            """Flush the current LaMa batch (same segment). Returns False on pipe error."""
            if not roi_rgb_buf or batch_seg is None:
                return True
            import torch
            seg = batch_seg
            B = len(roi_rgb_buf)
            batch = np.stack(roi_rgb_buf, axis=0).astype(np.float32) / 255.0
            batch = np.transpose(batch, (0, 3, 1, 2))
            img_t = torch.from_numpy(batch).to(device, non_blocking=True)
            if _LAMA_FP16:
                img_t = img_t.half()
            mask_b = seg.mask_tensor.expand(B, -1, -1, -1).contiguous()
            with torch.inference_mode():
                out = lama.model(img_t, mask_b)
            out = out.float().clamp(0, 1)
            out = (out * 255.0).to(torch.uint8).permute(0, 2, 3, 1).cpu().numpy()
            out = out[:, :seg.roi_h, :seg.roi_w, ::-1]  # crop padding + RGB->BGR
            for i, f in enumerate(frame_buf):
                patch = out[i]
                if _patch_degenerate(patch, seg.mask_roi):
                    # LaMa hallucinated a dark garbage block — redo this
                    # frame's mask with diffusion inpaint instead.
                    orig_roi = f[seg.ry:seg.ry2, seg.rx:seg.rx2]
                    patch = cv2.inpaint(orig_roi, seg.mask_roi,
                                        inpaint_radius, cv2.INPAINT_TELEA)
                    repair_count[0] += 1
                f[seg.ry:seg.ry2, seg.rx:seg.rx2] = patch
                try:
                    enc.stdin.write(f.tobytes())
                except (BrokenPipeError, OSError) as e:
                    logger.warning(f"encoder pipe closed early: {e}")
                    return False
            frame_buf.clear()
            roi_rgb_buf.clear()
            return True

        def _find_active_segment(t: float) -> Optional[_SegState]:
            """First segment whose [start_t, end_t) contains t. None if none."""
            for s in seg_states:
                if s.start_t <= t < s.end_t:
                    return s
            return None

        # Wall-clock safety cap for the whole decode→inpaint→encode stream.
        # Popen has no `timeout=`, so without this a wedged ffmpeg would hang
        # the job forever. 1h is a huge margin: a 3-min clip inpaints in ~5min
        # on GPU; only a genuine deadlock hits this.
        _INPAINT_MAX_S = 3600.0
        _inpaint_start = time.time()

        frame_idx = 0
        try:
            while True:
                if time.time() - _inpaint_start > _INPAINT_MAX_S:
                    try:
                        dec.kill(); enc.kill()
                    except Exception:
                        pass
                    raise RuntimeError(
                        f"Inpaint exceeded {int(_INPAINT_MAX_S)}s wall clock "
                        f"({frame_idx}/{total} frames) — aborting."
                    )
                # Poll for user cancellation between frames so a cancel takes
                # effect within a frame or two instead of after the whole
                # (potentially minutes-long) inpaint finishes.
                if is_cancelled is not None and is_cancelled():
                    try:
                        dec.kill(); enc.kill()
                    except Exception:
                        pass
                    from job_queue import JobCancelledError
                    raise JobCancelledError(
                        f"Inpaint cancelled by user at {frame_idx}/{total} frames"
                    )
                buf = dec.stdout.read(frame_nbytes)
                if not buf or len(buf) < frame_nbytes:
                    # Final flush before exit
                    if use_lama:
                        if _flush_lama_batch() is False:
                            break
                    break

                # Mutable buffer so in-place writes (ROI overwrite) work.
                frame = np.frombuffer(bytearray(buf), dtype=np.uint8).reshape((vh, vw, 3))
                t = frame_idx / fps
                active = _find_active_segment(t)

                if active is None:
                    # No segment active → pass frame through unchanged.
                    if use_lama and batch_seg is not None:
                        if _flush_lama_batch() is False:
                            break
                        batch_seg = None
                    try:
                        enc.stdin.write(frame.tobytes())
                    except (BrokenPipeError, OSError) as e:
                        logger.warning(f"encoder pipe closed early: {e}")
                        break
                elif use_lama:
                    # Segment changed since last frame → flush.
                    if batch_seg is not None and active is not batch_seg:
                        if _flush_lama_batch() is False:
                            break
                    batch_seg = active
                    # Pad ROI to (roi_h_pad, roi_w_pad), convert BGR -> RGB.
                    roi = frame[active.ry:active.ry2, active.rx:active.rx2]
                    roi_rgb = np.zeros((active.roi_h_pad, active.roi_w_pad, 3), dtype=np.uint8)
                    roi_rgb[:active.roi_h, :active.roi_w] = roi[..., ::-1]
                    frame_buf.append(frame)
                    roi_rgb_buf.append(roi_rgb)
                    if len(roi_rgb_buf) >= _LAMA_BATCH:
                        if _flush_lama_batch() is False:
                            break
                else:
                    # OpenCV path: ROI-cropped cv2.inpaint
                    roi = frame[active.ry:active.ry2, active.rx:active.rx2]
                    inpainted_roi = cv2.inpaint(roi, active.mask_roi, inpaint_radius, algo_flag)
                    frame[active.ry:active.ry2, active.rx:active.rx2] = inpainted_roi
                    try:
                        enc.stdin.write(frame.tobytes())
                    except (BrokenPipeError, OSError) as e:
                        logger.warning(f"encoder pipe closed early: {e}")
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

        # Bounded waits so a wedged ffmpeg at EOF can't hang the worker.
        try:
            dec_rc = dec.wait(timeout=120)
        except subprocess.TimeoutExpired:
            dec.kill(); dec_rc = dec.wait()
        try:
            enc_rc = enc.wait(timeout=300)
        except subprocess.TimeoutExpired:
            enc.kill(); enc_rc = enc.wait()
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

        repaired = repair_count[0]
        logger.info(
            f"Inpaint done: {frame_idx} frames, {out.stat().st_size // 1024} KB"
            + (f", {repaired} degenerate LaMa patches repaired with telea"
               if repaired else "")
        )
        return str(out)

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _run)
