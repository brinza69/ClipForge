"""
ClipForge — Utility job handlers (caption eraser, etc.)

These are user-uploaded one-shot jobs that don't belong to a project. They
run through the same async job queue as the pipeline so the HTTP request
that submits the work can return immediately and the browser can poll for
status — avoiding the "Failed to fetch" class of bugs that hit a synchronous
multi-minute endpoint when the TCP connection drops.

Output files live under data/temp/erase/<job_id>/output.mp4 and are served
by the GET /api/utilities/erase/{job_id}/download endpoint.
"""

import asyncio
import logging
import os
import subprocess
from pathlib import Path

from config import settings
from models import JobType
from services.inpaint import inpaint_region

logger = logging.getLogger("clipforge.utility_jobs")


def _ffmpeg_bin() -> str:
    loc = settings.ffmpeg_location
    if loc:
        exe = Path(loc) / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if exe.exists():
            return str(exe)
    import shutil
    return shutil.which("ffmpeg") or "ffmpeg"


def _creationflags() -> int:
    return 0x08000000 if os.name == "nt" else 0


async def handle_erase(job_id, project_id, clip_id, metadata, queue):
    """Run the eraser on an uploaded file. Metadata carries all params + paths."""
    input_path = metadata["input_path"]
    output_path = metadata["output_path"]
    x = int(metadata.get("x", 0))
    y = int(metadata.get("y", 0))
    w = int(metadata.get("w", 0))
    h = int(metadata.get("h", 0))
    mode = metadata.get("mode", "inpaint")
    algorithm = metadata.get("algorithm", "telea")

    if w <= 0 or h <= 0:
        raise RuntimeError("Region width and height must be greater than 0")
    if not Path(input_path).exists():
        raise RuntimeError(f"Input file missing: {input_path}")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    await queue.update_progress(job_id, 0.02, f"Starting {mode}...")

    if mode == "inpaint":
        algo = algorithm if algorithm in ("telea", "ns") else "telea"

        loop = asyncio.get_event_loop()

        def _progress_cb(frame_idx: int, total: int):
            if total <= 0:
                return
            # Map frame progress into 0.05–0.99 so the user sees movement even on
            # short clips while keeping headroom for the "finalizing" step.
            p = 0.05 + 0.94 * (frame_idx / total)
            msg = f"Inpainting frame {frame_idx}/{total}"
            asyncio.run_coroutine_threadsafe(
                queue.update_progress(job_id, p, msg), loop
            )

        try:
            await inpaint_region(
                input_path=input_path,
                output_path=output_path,
                x=x, y=y, w=w, h=h,
                algorithm=algo,
                on_progress=_progress_cb,
            )
        except Exception as e:
            logger.exception(f"Erase job {job_id} failed during inpaint")
            raise RuntimeError(f"Inpainting failed: {str(e)[-400:]}")
    else:
        # FFmpeg avgblur fallback. Single subprocess, fast.
        ffmpeg_bin = _ffmpeg_bin()
        vf = (
            f"split=2[main][blur_src];"
            f"[blur_src]crop={w}:{h}:{x}:{y},avgblur=sizeX=50:sizeY=50[blurred];"
            f"[main][blurred]overlay={x}:{y}"
        )
        cmd = [
            ffmpeg_bin, "-y",
            "-i", input_path,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "copy",
            "-movflags", "+faststart",
            output_path,
        ]

        await queue.update_progress(job_id, 0.1, "Running ffmpeg blur...")

        loop = asyncio.get_event_loop()

        def _run():
            return subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=_creationflags(),
            )

        proc = await loop.run_in_executor(None, _run)
        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", errors="replace")
            tail = "\n".join(stderr.strip().splitlines()[-8:])
            logger.error(f"Erase job {job_id} blur failed:\n{tail}")
            raise RuntimeError(f"Blur failed: {tail[-400:]}")

    out = Path(output_path)
    if not out.exists() or out.stat().st_size < 1000:
        raise RuntimeError("Erase produced no output file")

    await queue.update_progress(job_id, 1.0, "Complete")
    logger.info(f"Erase job {job_id} done: {out.stat().st_size // 1024} KB")


def register_utility_handlers(queue):
    queue.register_handler(JobType.erase.value, handle_erase)
    logger.info("Utility handlers registered")
