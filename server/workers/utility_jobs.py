"""
ClipForge — Utility job handlers.

Two flavors of erase live here:

  * handle_erase: one-shot, user-uploaded clip (Caption Eraser page). Output
    served via GET /api/utilities/erase/{job_id}/download.

  * handle_erase_project: operates on a project's downloaded video.mp4 (used
    by the batch-process feature and as the final stage of full_pipeline
    when erase_params is set). Output is data/media/<project>/video_erased.mp4.

Both routes share inpaint/blur primitives.
"""

import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path

from config import settings
from database import async_session
from models import JobType, ProjectModel
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


def _scale_region_to_video(region: dict, src_w: int, src_h: int, dst_w: int, dst_h: int) -> dict:
    """
    Map (x, y, w, h) drawn on a source video (src_w x src_h) into the dst
    video's coordinate space. If src == dst (same dimensions) this is a no-op.
    Used when the user picks a region on video 1 of a batch and we apply it
    to videos 2..N that may have different resolutions.
    """
    if not src_w or not src_h or (src_w == dst_w and src_h == dst_h):
        return {
            "x": int(region.get("x", 0)),
            "y": int(region.get("y", 0)),
            "w": int(region.get("w", 0)),
            "h": int(region.get("h", 0)),
        }
    sx = dst_w / float(src_w)
    sy = dst_h / float(src_h)
    return {
        "x": max(0, int(round(region["x"] * sx))),
        "y": max(0, int(round(region["y"] * sy))),
        "w": max(1, int(round(region["w"] * sx))),
        "h": max(1, int(round(region["h"] * sy))),
    }


async def handle_erase_project(job_id, project_id, clip_id, metadata, queue):
    """
    Apply the eraser to a project's downloaded video.mp4 file. Reads the
    erase rectangle from project.erase_params (set by the batch endpoint),
    scales it to the actual video dimensions, writes video_erased.mp4 next
    to the original, and records the path on the project row.
    """
    async with async_session() as session:
        project = await session.get(ProjectModel, project_id)
        if not project:
            raise RuntimeError(f"Project {project_id} not found")
        if not project.video_path or not Path(project.video_path).exists():
            raise RuntimeError(f"Project {project_id} has no downloaded video to erase")
        params = project.erase_params or {}

    if not params:
        # Nothing to do — treat as no-op so the pipeline still completes.
        await queue.update_progress(job_id, 1.0, "No erase params; skipping erase step")
        logger.info(f"erase_project {job_id}: project {project_id} has no erase_params, skipping")
        return

    region = params.get("region") or params
    mode = params.get("mode", "inpaint")
    algorithm = params.get("algorithm", "telea")
    src = params.get("source_dimensions") or {}
    src_w = int(src.get("width", 0) or 0)
    src_h = int(src.get("height", 0) or 0)
    dst_w = int(project.width or 0)
    dst_h = int(project.height or 0)
    scaled = _scale_region_to_video(region, src_w, src_h, dst_w, dst_h)

    input_path = Path(project.video_path)
    output_path = input_path.with_name("video_erased.mp4")

    logger.info(
        f"erase_project {job_id}: project={project_id} mode={mode} "
        f"region={scaled} src=({src_w}x{src_h}) dst=({dst_w}x{dst_h})"
    )
    await queue.update_progress(job_id, 0.02, f"Erasing video ({mode})…")

    if mode == "inpaint":
        loop = asyncio.get_event_loop()

        def _progress_cb(frame_idx: int, total: int):
            if total <= 0:
                return
            p = 0.05 + 0.94 * (frame_idx / total)
            asyncio.run_coroutine_threadsafe(
                queue.update_progress(job_id, p, f"Inpainting frame {frame_idx}/{total}"),
                loop,
            )

        try:
            await inpaint_region(
                input_path=str(input_path),
                output_path=str(output_path),
                x=scaled["x"], y=scaled["y"], w=scaled["w"], h=scaled["h"],
                algorithm=algorithm if algorithm in ("telea", "ns") else "telea",
                on_progress=_progress_cb,
            )
        except Exception as e:
            logger.exception(f"erase_project {job_id}: inpaint failed")
            raise RuntimeError(f"Inpainting failed: {str(e)[-400:]}")
    else:
        # ffmpeg avgblur fallback (fast).
        ffmpeg_bin = _ffmpeg_bin()
        x, y, w, h = scaled["x"], scaled["y"], scaled["w"], scaled["h"]
        vf = (
            f"split=2[main][blur_src];"
            f"[blur_src]crop={w}:{h}:{x}:{y},avgblur=sizeX=50:sizeY=50[blurred];"
            f"[main][blurred]overlay={x}:{y}"
        )
        cmd = [
            ffmpeg_bin, "-y",
            "-i", str(input_path),
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(output_path),
        ]
        await queue.update_progress(job_id, 0.1, "Running ffmpeg blur…")
        loop = asyncio.get_event_loop()
        proc = await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                cmd,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=_creationflags(),
            ),
        )
        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", errors="replace")
            tail = "\n".join(stderr.strip().splitlines()[-8:])
            raise RuntimeError(f"Blur failed: {tail[-400:]}")

    if not output_path.exists() or output_path.stat().st_size < 1000:
        raise RuntimeError("Erase produced no output file")

    # Persist the new path on the project so the API can expose a download URL.
    async with async_session() as session:
        project = await session.get(ProjectModel, project_id)
        if project:
            project.erased_video_path = str(output_path)
            await session.commit()

    await queue.update_progress(job_id, 1.0, "Erase complete")
    logger.info(f"erase_project {job_id}: done -> {output_path} ({output_path.stat().st_size // 1024} KB)")


def register_utility_handlers(queue):
    queue.register_handler(JobType.erase.value, handle_erase)
    queue.register_handler(JobType.erase_project.value, handle_erase_project)
    logger.info("Utility handlers registered")
