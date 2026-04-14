"""
ClipForge — Utilities Router
Quick-download: paste a URL and kick off the full pipeline immediately.
Caption Eraser: upload a video and blur/erase a rectangular region using FFmpeg.
"""

import asyncio
import logging
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from models import ProjectModel, ProjectStatus, JobType
from services.downloader import validate_url, detect_source_type, fetch_metadata
from job_queue import job_queue

logger = logging.getLogger("clipforge.routers.utilities")
router = APIRouter(prefix="/api/utilities", tags=["utilities"])


class QuickDownloadRequest(BaseModel):
    url: str
    title: Optional[str] = None


@router.post("/download")
async def quick_download(
    data: QuickDownloadRequest,
    session: AsyncSession = Depends(get_session),
):
    """
    Create a project from a URL and immediately enqueue the full pipeline.
    Fetches metadata inline, then queues download → transcribe → score.
    """
    check = await validate_url(data.url)
    if not check["valid"]:
        raise HTTPException(400, check.get("error", "Invalid or unsupported URL"))

    project = ProjectModel(
        title=data.title or "Quick Download",
        source_url=data.url,
        source_type=detect_source_type(data.url),
        status=ProjectStatus.fetching_metadata.value,
    )
    session.add(project)
    await session.commit()
    await session.refresh(project)

    # Fetch metadata inline (fast)
    meta = await fetch_metadata(data.url, project.id)
    if "error" in meta:
        project.status = ProjectStatus.failed.value
        project.description = f"[{meta.get('error_code', 'unknown')}] {meta['error']}"
        await session.commit()
        raise HTTPException(422, meta["error"])

    project.title = meta.get("title") or project.title
    project.channel_name = meta.get("channel_name")
    project.duration = meta.get("duration")
    project.width = meta.get("width")
    project.height = meta.get("height")
    project.fps = meta.get("fps")
    project.thumbnail_url = meta.get("thumbnail_url")
    project.estimated_size = meta.get("estimated_size")
    project.upload_date = meta.get("upload_date")
    project.description = meta.get("description")
    project.webpage_url = meta.get("webpage_url")
    project.extractor = meta.get("extractor")
    project.is_live = meta.get("is_live")
    project.was_live = meta.get("was_live")
    project.availability = meta.get("availability")
    project.status = ProjectStatus.metadata_ready.value
    await session.commit()

    # Enqueue full pipeline immediately
    job_id = await job_queue.enqueue(
        project_id=project.id,
        job_type=JobType.full_pipeline.value,
    )

    logger.info(f"Quick-download queued: project={project.id} job={job_id} url={data.url}")
    return {"project_id": project.id, "job_id": job_id, "title": project.title}


@router.post("/erase")
async def erase_region(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    x: int = Form(0),
    y: int = Form(0),
    w: int = Form(100),
    h: int = Form(50),
    mode: str = Form("inpaint"),       # "inpaint" (OpenCV TELEA) or "blur" (ffmpeg avgblur)
    algorithm: str = Form("telea"),    # "telea" or "ns" — only used when mode=inpaint
):
    """
    Seamlessly remove (inpaint) or blur a rectangular region in a video.

    Modes:
      - "inpaint" (default): OpenCV cv2.inpaint on every frame, muxed with
        original audio. Much more natural for captions/logos/watermarks.
      - "blur": fast fallback using ffmpeg avgblur filter.

    x, y, w, h are in input-pixel coordinates (top-left origin). Accepts
    MP4, MOV, WebM, MKV. Returns the processed video as a downloadable MP4.
    """
    from config import settings
    from services.inpaint import inpaint_region

    if w <= 0 or h <= 0:
        raise HTTPException(400, "Region width and height must be greater than 0")

    # Read uploaded content
    content = await file.read()
    if len(content) > 500 * 1024 * 1024:
        raise HTTPException(413, "File too large. Maximum 500 MB.")
    if len(content) < 1000:
        raise HTTPException(400, "File appears to be empty or invalid.")

    # Save to space-free temp paths (spaces in path break some ffmpeg filters)
    uid = uuid.uuid4().hex[:12]
    suffix = Path(file.filename or "video.mp4").suffix.lower() or ".mp4"
    tmp_dir = Path(tempfile.gettempdir())
    input_path = tmp_dir / f"cf_erase_in_{uid}{suffix}"
    output_path = tmp_dir / f"cf_erase_out_{uid}.mp4"
    input_path.write_bytes(content)

    stem = Path(file.filename or "video").stem
    out_filename = f"{stem}_erased.mp4"

    def _cleanup():
        input_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)

    logger.info(
        f"Erase job uid={uid} mode={mode} algo={algorithm}: "
        f"region x={x} y={y} w={w} h={h}, input={input_path.name}"
    )

    try:
        if mode == "inpaint":
            # OpenCV per-frame inpainting — seamless result
            algo = algorithm if algorithm in ("telea", "ns") else "telea"
            try:
                await asyncio.wait_for(
                    inpaint_region(
                        input_path=str(input_path),
                        output_path=str(output_path),
                        x=x, y=y, w=w, h=h,
                        algorithm=algo,
                    ),
                    timeout=900,  # 15 min cap — inpainting is slow
                )
            except asyncio.TimeoutError:
                input_path.unlink(missing_ok=True)
                raise HTTPException(504, "Inpainting timed out after 15 minutes. Try the 'blur' mode or a shorter clip.")
            except Exception as e:
                input_path.unlink(missing_ok=True)
                logger.exception(f"Inpaint failed uid={uid}")
                raise HTTPException(500, f"Inpainting failed: {str(e)[-400:]}")
        else:
            # Fast fallback: ffmpeg avgblur
            ffmpeg_bin = "ffmpeg"
            ffmpeg_loc = settings.ffmpeg_location
            if ffmpeg_loc:
                ffmpeg_bin = str(Path(ffmpeg_loc) / "ffmpeg")

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

            loop = asyncio.get_event_loop()

            def _run():
                return subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
                )

            try:
                proc = await asyncio.wait_for(loop.run_in_executor(None, _run), timeout=600)
            except asyncio.TimeoutError:
                input_path.unlink(missing_ok=True)
                raise HTTPException(504, "Processing timed out after 10 minutes")

            if proc.returncode != 0:
                stderr = proc.stderr.decode("utf-8", errors="replace")
                lines = [l for l in stderr.splitlines() if l and not l.startswith("  ") and not l.startswith("built")]
                tail = "\n".join(lines[-8:]).strip()
                logger.error(f"FFmpeg erase failed uid={uid}:\n{tail}")
                input_path.unlink(missing_ok=True)
                raise HTTPException(422, f"Processing failed: {tail[-400:]}")

        if not output_path.exists() or output_path.stat().st_size < 1000:
            input_path.unlink(missing_ok=True)
            raise HTTPException(500, "Processing produced no output file")

        logger.info(f"Erase job uid={uid} done: {output_path.stat().st_size // 1024} KB")
        background_tasks.add_task(_cleanup)

        return FileResponse(
            path=str(output_path),
            media_type="video/mp4",
            filename=out_filename,
            headers={"Content-Disposition": f'attachment; filename="{out_filename}"'},
        )
    except HTTPException:
        raise
    except Exception as e:
        input_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)
        logger.exception(f"Erase job uid={uid} crashed")
        raise HTTPException(500, f"Unexpected error: {str(e)[-400:]}")
