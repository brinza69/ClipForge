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
from models import JobModel, JobType, ProjectModel
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
    """Run the eraser on an uploaded file. Metadata carries all params + paths.

    If metadata.auto_detect is true, x/y/w/h are ignored — the worker runs OCR
    over the video, clusters detected captions into time-varying segments, and
    passes them to inpaint_region instead of a single static rect.
    """
    input_path = metadata["input_path"]
    output_path = metadata["output_path"]
    x = int(metadata.get("x", 0))
    y = int(metadata.get("y", 0))
    w = int(metadata.get("w", 0))
    h = int(metadata.get("h", 0))
    mode = metadata.get("mode", "inpaint")
    algorithm = metadata.get("algorithm", "telea")
    auto_detect = bool(metadata.get("auto_detect", False))

    if not auto_detect and (w <= 0 or h <= 0):
        raise RuntimeError("Region width and height must be greater than 0")
    if auto_detect and mode != "inpaint":
        raise RuntimeError("Auto-detect only supports inpaint mode")
    if not Path(input_path).exists():
        raise RuntimeError(f"Input file missing: {input_path}")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    await queue.update_progress(job_id, 0.02, f"Starting {mode}...")

    # Optional Stage A: OCR-based caption detection — produces a list of
    # {start_t, end_t, x, y, w, h} segments that the inpaint loop will only
    # apply during the frames where each is active.
    detected_segments = None
    if auto_detect:
        from services.caption_detector import detect_caption_segments

        loop = asyncio.get_event_loop()

        def _det_progress(p: float, msg: str):
            # Detection occupies 0.02 – 0.35 of the job; clamp.
            mapped = 0.02 + min(1.0, max(0.0, p)) * 0.33
            asyncio.run_coroutine_threadsafe(
                queue.update_progress(job_id, mapped, msg), loop
            )

        # If the user drew a rect, constrain detection to it (so busy frames
        # don't get scene text erased outside the caption band). No rect →
        # whole-frame scan (fallback).
        roi = {"x": x, "y": y, "w": w, "h": h} if (w > 0 and h > 0) else None
        await queue.update_progress(job_id, 0.03, "Loading OCR model…")
        detected_segments = await loop.run_in_executor(
            None,
            lambda: detect_caption_segments(input_path, roi=roi, on_progress=_det_progress),
        )
        if not detected_segments:
            raise RuntimeError(
                "No on-screen captions detected. Try selecting a region manually."
            )
        await queue.update_progress(
            job_id, 0.35,
            f"Detected {len(detected_segments)} caption segment(s) — inpainting…",
        )

    if mode == "inpaint":
        algo = algorithm if algorithm in ("telea", "ns") else "telea"

        loop = asyncio.get_event_loop()

        # When auto-detect ran first it consumed 0.02–0.35. Map inpaint into the
        # remaining 0.35–0.99 so the bar moves predictably.
        inpaint_base = 0.35 if auto_detect else 0.05
        inpaint_span = 0.99 - inpaint_base

        def _progress_cb(frame_idx: int, total: int):
            if total <= 0:
                return
            p = inpaint_base + inpaint_span * (frame_idx / total)
            msg = f"Inpainting frame {frame_idx}/{total}"
            asyncio.run_coroutine_threadsafe(
                queue.update_progress(job_id, p, msg), loop
            )

        try:
            kwargs = dict(
                input_path=input_path,
                output_path=output_path,
                algorithm=algo,
                on_progress=_progress_cb,
            )
            if detected_segments is not None:
                kwargs["segments"] = detected_segments
            else:
                kwargs.update(x=x, y=y, w=w, h=h)
            await inpaint_region(**kwargs)
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
                timeout=1800,
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
                timeout=1800,
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


async def handle_silence_remove(job_id, project_id, clip_id, metadata, queue):
    """
    Remove silence from a user-uploaded audio or video file.
    Metadata carries paths + pydub-style params (see services.silence_remover).
    """
    import asyncio as _asyncio
    from services.silence_remover import remove_silence

    input_path = metadata["input_path"]
    output_path = metadata["output_path"]
    mode = metadata.get("mode", "auto")
    min_silence_ms = int(metadata.get("min_silence_ms", 100))
    silence_thresh_db = float(metadata.get("silence_thresh_db", -45.0))
    keep_silence_ms = int(metadata.get("keep_silence_ms", 50))

    if not Path(input_path).exists():
        raise RuntimeError(f"Input file missing: {input_path}")

    await queue.update_progress(job_id, 0.02, "Starting silence removal…")

    loop = _asyncio.get_event_loop()

    def _on_progress(p: float, msg: str):
        # Map service's 0..1 onto 0.02..0.99 to leave room for finalize.
        mapped = 0.02 + max(0.0, min(1.0, p)) * 0.97
        _asyncio.run_coroutine_threadsafe(
            queue.update_progress(job_id, mapped, msg), loop
        )

    stats = await loop.run_in_executor(
        None,
        lambda: remove_silence(
            input_path, output_path,
            mode=mode,
            min_silence_ms=min_silence_ms,
            silence_thresh_db=silence_thresh_db,
            keep_silence_ms=keep_silence_ms,
            on_progress=_on_progress,
        ),
    )

    # Persist stats on the job so the result endpoint can return them.
    metadata["stats"] = stats
    async with async_session() as session:
        job = await session.get(JobModel, job_id)
        if job:
            job.metadata_json = json.dumps(metadata)
            await session.commit()

    await queue.update_progress(
        job_id, 1.0,
        f"Removed {stats['removed_pct']}% — {stats['segments']} segment(s) kept",
    )
    logger.info(
        f"silence_remove {job_id}: {stats['before_ms']}ms -> {stats['after_ms']}ms "
        f"(removed {stats['removed_pct']}%, {stats['segments']} segments)"
    )


async def handle_caption_burn(job_id, project_id, clip_id, metadata, queue):
    """
    Burn pre-baked manual caption overlays into a user-uploaded video.

    The router has already written the .ass file from the overlay list; we
    just shell out to ffmpeg + libass with the user-fonts directory wired in
    so freshly uploaded fonts work without a server restart.
    """
    import asyncio as _asyncio

    input_path = metadata["input_path"]
    output_path = metadata["output_path"]
    ass_path = metadata["ass_path"]
    fonts_dir_arg = metadata.get("fonts_dir") or ""

    if not Path(input_path).exists():
        raise RuntimeError(f"Input file missing: {input_path}")
    if not Path(ass_path).exists():
        raise RuntimeError(f"ASS file missing: {ass_path}")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    await queue.update_progress(job_id, 0.05, "Starting caption burn-in…")

    ass_arg = str(ass_path).replace("\\", "/").replace(":", "\\:")
    vf = f"subtitles=filename='{ass_arg}'"
    if fonts_dir_arg:
        fdir = str(fonts_dir_arg).replace("\\", "/").replace(":", "\\:")
        vf += f":fontsdir='{fdir}'"

    cmd = [
        _ffmpeg_bin(), "-y", "-loglevel", "error",
        "-i", str(input_path),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ]

    loop = _asyncio.get_event_loop()

    def _run() -> int:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            creationflags=_creationflags(),
            timeout=1800,
        )
        if proc.returncode != 0:
            tail = "\n".join((proc.stderr or "").strip().splitlines()[-8:])
            raise RuntimeError(f"ffmpeg caption-burn failed: {tail[-500:]}")
        return Path(output_path).stat().st_size

    size = await loop.run_in_executor(None, _run)
    await queue.update_progress(job_id, 1.0, f"Done ({size // 1024} KB)")
    logger.info(f"caption_burn {job_id}: done -> {output_path} ({size // 1024} KB)")


async def handle_commentator_bg_remove(job_id, project_id, clip_id, metadata, queue):
    """
    Run AI background removal (rembg / U²-Net) on a commentator preset's
    raw video and save the result as processed.webm alongside it. Subsequent
    composites use the alpha-baked WebM and skip chromakey entirely.
    """
    import asyncio as _asyncio
    from services.bg_removal import remove_background_video
    from services.commentators import _video_path, _ai_processed_path

    preset_id = metadata["preset_id"]
    src = _video_path(preset_id)
    dst = _ai_processed_path(preset_id)
    if not src.exists():
        raise RuntimeError(f"source video missing for preset {preset_id}")

    await queue.update_progress(job_id, 0.02, "Loading AI model…")

    loop = _asyncio.get_event_loop()

    def _on_progress(p: float, msg: str):
        p = max(0.0, min(1.0, p))
        _asyncio.run_coroutine_threadsafe(
            queue.update_progress(job_id, p, msg), loop
        )

    stats = await loop.run_in_executor(
        None,
        lambda: remove_background_video(str(src), str(dst), on_progress=_on_progress),
    )
    metadata["bg_removal_stats"] = stats
    async with async_session() as session:
        job = await session.get(JobModel, job_id)
        if job:
            job.metadata_json = json.dumps(metadata)
            await session.commit()

    await queue.update_progress(
        job_id, 1.0,
        f"AI background removed ({stats['frame_count']} frames, "
        f"{stats['output_size'] // 1024} KB output)",
    )
    logger.info(f"commentator_bg_remove {job_id}: {preset_id} → {dst.name}")


def register_utility_handlers(queue):
    queue.register_handler(JobType.erase.value, handle_erase)
    queue.register_handler(JobType.erase_project.value, handle_erase_project)
    queue.register_handler(JobType.silence_remove.value, handle_silence_remove)
    queue.register_handler(JobType.caption_burn.value, handle_caption_burn)
    queue.register_handler(JobType.commentator_bg_remove.value, handle_commentator_bg_remove)
    logger.info("Utility handlers registered")
