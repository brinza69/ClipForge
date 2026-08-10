"""
ClipForge — AI Video Upscaler (Real-ESRGAN ncnn-vulkan)

Turns a low-res / soft clip into a genuinely higher-res one by RECONSTRUCTING
detail with a GAN, not by stretching pixels. Pipeline (same one proven by hand):

  1. Extract every frame to PNG (optional light denoise first — download/TikTok
     sources are recompressed, so a mild hqdn3d pass stops the AI amplifying
     block artifacts).
  2. Upscale each frame with realesrgan-ncnn-vulkan on the GPU (Vulkan).
  3. Reassemble at the target resolution (down-sampling the ×2/×4 frames to the
     requested short-side = supersampling → clean edges), copy the original
     audio, encode H.264.

Two model profiles:
  * "video" → realesr-animevideov3 ×2 : fast (~0.5 s/frame on RTX 3060),
              temporally stable, best for real footage. THE default.
  * "photo" → realesrgan-x4plus ×4    : slow, maximum texture, photo-tuned;
              can flicker on video but squeezes more detail from stylised clips.

Honest limit: AI reconstructs plausible detail; it cannot recover detail a
native higher-res capture would have had. Soft/AI-generated sources gain the
least. Runs blocking — call it inside loop.run_in_executor.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("clipforge.upscaler")

# model profile -> (realesrgan model name, native scale factor)
MODELS = {
    "video": ("realesr-animevideov3", 2),
    "photo": ("realesrgan-x4plus", 4),
}

VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi"}


def _ffmpeg_bin() -> str:
    try:
        from config import settings
        loc = settings.ffmpeg_location
        if loc:
            exe = Path(loc) / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
            if exe.exists():
                return str(exe)
    except Exception:
        pass
    return shutil.which("ffmpeg") or "ffmpeg"


def _ffprobe_bin() -> str:
    ff = _ffmpeg_bin()
    probe = ff.replace("ffmpeg", "ffprobe")
    if probe != "ffprobe" and not Path(probe).exists():
        probe = shutil.which("ffprobe") or "ffprobe"
    return probe


def _creationflags() -> int:
    return 0x08000000 if os.name == "nt" else 0


def _run(cmd: list[str], timeout: int = 3600) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=_creationflags(), timeout=timeout,
    )


def _probe(input_path: str) -> dict:
    """Return {width, height, fps, nb_frames} — best-effort, sane fallbacks."""
    probe = _ffprobe_bin()
    out = {"width": 0, "height": 0, "fps": "30", "nb_frames": 0}
    try:
        r = _run([
            probe, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate,nb_frames",
            "-of", "default=noprint_wrappers=1:nokey=0", input_path,
        ], timeout=30)
        for line in r.stdout.decode("utf-8", "replace").splitlines():
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip()
            if k == "width":
                out["width"] = int(v or 0)
            elif k == "height":
                out["height"] = int(v or 0)
            elif k == "r_frame_rate" and v and v != "0/0":
                out["fps"] = v
            elif k == "nb_frames" and v.isdigit():
                out["nb_frames"] = int(v)
    except Exception:
        logger.exception("ffprobe failed; using fallbacks")
    return out


def upscale_video(
    input_path: str,
    output_path: str,
    *,
    model: str = "video",
    target_p: int = 1080,
    denoise: bool = True,
    gpu_id: int = 0,
    workdir: Optional[str] = None,
    on_progress: Optional[Callable[[float, str], None]] = None,
) -> dict:
    """
    Upscale a video with Real-ESRGAN. Returns a stats dict:
        { src_w, src_h, out_w, out_h, frames, model, out_size }

    `target_p` is the SHORT side of the output (1080 → 1080×1920 for a vertical
    clip, 1920×1080 for a landscape one). Aspect ratio is preserved; dims are
    forced even for H.264.
    """
    from config import settings

    def _p(frac: float, msg: str):
        if on_progress:
            on_progress(max(0.0, min(1.0, frac)), msg)

    src = Path(input_path)
    if not src.exists():
        raise RuntimeError(f"Input file missing: {input_path}")
    if model not in MODELS:
        raise ValueError(f"Unknown model profile {model!r}; expected one of {list(MODELS)}")

    exe = settings.realesrgan_bin
    if not exe:
        raise RuntimeError(
            "Real-ESRGAN binary not found. Install it at "
            "<repo>/tools/realesrgan/pkg/ or set CLIPFORGE_REALESRGAN_PATH."
        )
    model_name, scale = MODELS[model]
    models_dir = Path(exe).parent / "models"

    wd = Path(workdir) if workdir else src.parent / f".upscale_{src.stem}"
    frames_dir = wd / "frames"
    up_dir = wd / "up"
    for d in (frames_dir, up_dir):
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)

    info = _probe(input_path)
    fps = info["fps"]
    ffmpeg = _ffmpeg_bin()

    try:
        # ── 1. Extract frames ────────────────────────────────────────────────
        _p(0.02, "Extracting frames…")
        vf = "hqdn3d=1.5:1.5:6:6" if denoise else None
        extract_cmd = [ffmpeg, "-y", "-v", "error", "-i", input_path]
        if vf:
            extract_cmd += ["-vf", vf]
        extract_cmd += ["-pix_fmt", "rgb24", str(frames_dir / "%06d.png")]
        r = _run(extract_cmd, timeout=3600)
        if r.returncode != 0:
            raise RuntimeError(
                "Frame extraction failed: "
                + r.stderr.decode("utf-8", "replace")[-400:]
            )
        total = len(list(frames_dir.glob("*.png")))
        if total == 0:
            raise RuntimeError("No frames were extracted from the input video")
        _p(0.08, f"Extracted {total} frames — starting AI upscale…")

        # ── 2. AI upscale (poll output dir for progress) ─────────────────────
        up_cmd = [
            exe, "-i", str(frames_dir), "-o", str(up_dir),
            "-n", model_name, "-s", str(scale), "-g", str(gpu_id), "-f", "png",
        ]
        if models_dir.exists():
            up_cmd += ["-m", str(models_dir)]
        # CRITICAL: realesrgan streams verbose per-tile progress to stderr. We
        # track progress by counting output files, not by reading its pipes, so
        # we must NOT use stderr=PIPE here — with no one draining it the 64 KB
        # OS pipe buffer fills after a few big frames and the child blocks on
        # write, deadlocking the whole upscale. Send stdout to the void and
        # stderr to a file we only read on failure.
        err_path = wd / "realesrgan_stderr.txt"
        with open(err_path, "wb") as errf:
            proc = subprocess.Popen(
                up_cmd, stdout=subprocess.DEVNULL, stderr=errf,
                creationflags=_creationflags(),
            )
            UP_LO, UP_HI = 0.08, 0.85
            while proc.poll() is None:
                time.sleep(1.5)
                done = len(list(up_dir.glob("*.png")))
                frac = UP_LO + (UP_HI - UP_LO) * (done / total if total else 0)
                _p(frac, f"AI upscaling frame {done}/{total}…")
        if proc.returncode != 0:
            try:
                stderr = err_path.read_text("utf-8", "replace")
            except Exception:
                stderr = ""
            raise RuntimeError(f"Real-ESRGAN failed: {stderr[-400:]}")
        up_count = len(list(up_dir.glob("*.png")))
        if up_count < total:
            logger.warning("Upscaled %d/%d frames (some missing)", up_count, total)
        if up_count == 0:
            raise RuntimeError("Upscaler produced no frames")

        # ── 3. Reassemble at target resolution + original audio ──────────────
        _p(0.88, f"Encoding {target_p}p…")
        # Short side -> target_p, keep aspect, force even dims. Landscape sets
        # height, portrait/square sets width.
        scale_expr = (
            f"scale=w='if(gt(iw,ih),-2,{target_p})':"
            f"h='if(gt(iw,ih),{target_p},-2)',"
            f"unsharp=5:5:0.4:5:5:0.0"
        )
        encode_cmd = [
            ffmpeg, "-y", "-v", "error",
            "-framerate", fps, "-i", str(up_dir / "%06d.png"),
            "-i", input_path,
            "-map", "0:v:0", "-map", "1:a:0?",
            "-vf", scale_expr,
            "-r", fps,
            "-c:v", "libx264", "-crf", "17", "-preset", "slow",
            "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(output_path),
        ]
        r = _run(encode_cmd, timeout=3600)
        if r.returncode != 0:
            raise RuntimeError(
                "Final encode failed: " + r.stderr.decode("utf-8", "replace")[-400:]
            )

        out = Path(output_path)
        if not out.exists() or out.stat().st_size < 1000:
            raise RuntimeError("Upscale produced no output file")

        out_info = _probe(str(out))
        _p(1.0, "Done")
        stats = {
            "src_w": info["width"], "src_h": info["height"],
            "out_w": out_info["width"], "out_h": out_info["height"],
            "frames": total, "model": model_name,
            "out_size": out.stat().st_size,
        }
        logger.info(
            "upscale done: %sx%s -> %sx%s (%d frames, %s, %d KB)",
            stats["src_w"], stats["src_h"], stats["out_w"], stats["out_h"],
            total, model_name, stats["out_size"] // 1024,
        )
        return stats
    finally:
        # Reclaim the heavy PNG scratch (frames + upscaled) — can be tens of GB.
        for d in (frames_dir, up_dir):
            shutil.rmtree(d, ignore_errors=True)
