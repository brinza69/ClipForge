"""
ClipForge — Exports & Storage Router
API endpoints for export management, storage info, and cleanup.
"""

import io
import shutil
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import BaseModel as PydanticBaseModel

from database import get_session
from models import ClipModel, ClipStatus, ProjectModel
from config import settings


class CleanupRequest(PydanticBaseModel):
    target: str = "temp"

router = APIRouter(prefix="/api/exports", tags=["exports"])


@router.get("/")
async def list_exports(session: AsyncSession = Depends(get_session)):
    """List all exported clips."""
    result = await session.execute(
        select(ClipModel)
        .where(ClipModel.status == ClipStatus.exported.value)
        .order_by(ClipModel.created_at.desc())
    )
    clips = result.scalars().all()

    exports = []
    for clip in clips:
        export_path = Path(clip.export_path) if clip.export_path else None
        exports.append({
            "clip_id": clip.id,
            "project_id": clip.project_id,
            "title": clip.title,
            "duration": clip.duration,
            "momentum_score": clip.momentum_score,
            "export_path": clip.export_path,
            "file_exists": export_path.exists() if export_path else False,
            "file_size": export_path.stat().st_size if export_path and export_path.exists() else 0,
        })

    return exports


@router.get("/storage")
async def storage_info():
    """Get storage usage information."""
    def dir_size(path: Path) -> int:
        if not path.exists():
            return 0
        total = 0
        for f in path.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
        return total

    # Get free disk space
    import shutil as sh
    try:
        total, used, free = sh.disk_usage(str(settings.data_dir))
    except Exception:
        total = used = free = 0

    return {
        "media_size": dir_size(settings.media_dir),
        "exports_size": dir_size(settings.exports_dir),
        "cache_size": dir_size(settings.cache_dir),
        "temp_size": dir_size(settings.temp_dir),
        "thumbnails_size": dir_size(settings.thumbnails_dir),
        "total_data_size": dir_size(settings.data_dir),
        "disk_total": total,
        "disk_used": used,
        "disk_free": free,
    }


@router.post("/cleanup")
async def cleanup_storage(body: CleanupRequest = CleanupRequest()):
    """
    Cleanup storage.
    Targets: temp, cache, exports, media
    """
    target = body.target
    target_map = {
        "temp": settings.temp_dir,
        "cache": settings.cache_dir,
    }

    path = target_map.get(target)
    if not path:
        raise HTTPException(400, f"Invalid cleanup target: {target}. Use: {list(target_map.keys())}")

    cleaned = 0
    if path.exists():
        for item in path.iterdir():
            if item.is_dir():
                size = sum(f.stat().st_size for f in item.rglob("*") if f.is_file())
                shutil.rmtree(item, ignore_errors=True)
                cleaned += size
            elif item.is_file():
                cleaned += item.stat().st_size
                item.unlink()

    return {
        "target": target,
        "cleaned_bytes": cleaned,
        "cleaned_formatted": _format_size(cleaned),
    }


@router.get("/open-folder")
async def get_exports_folder():
    """Return the exports folder path for the user to open."""
    return {"path": str(settings.exports_dir)}


@router.get("/{clip_id}/download")
async def download_export(clip_id: str, session: AsyncSession = Depends(get_session)):
    """Download an exported clip file."""
    result = await session.execute(
        select(ClipModel).where(ClipModel.id == clip_id)
    )
    clip = result.scalar_one_or_none()
    if not clip:
        raise HTTPException(404, f"Clip {clip_id} not found")
    if not clip.export_path:
        raise HTTPException(404, f"Clip {clip_id} has no export file")

    export_path = Path(clip.export_path)
    if not export_path.exists():
        raise HTTPException(404, f"Export file not found on disk: {clip.export_path}")

    return FileResponse(
        path=export_path,
        filename=export_path.name,
        media_type="video/mp4",
    )


@router.get("/{clip_id}/file")
async def get_export_file_url(clip_id: str, session: AsyncSession = Depends(get_session)):
    """Return the URL and path for an exported clip file."""
    result = await session.execute(
        select(ClipModel).where(ClipModel.id == clip_id)
    )
    clip = result.scalar_one_or_none()
    if not clip:
        raise HTTPException(404, f"Clip {clip_id} not found")
    if not clip.export_path:
        raise HTTPException(404, f"Clip {clip_id} has no export file")

    export_path = Path(clip.export_path)
    if not export_path.exists():
        raise HTTPException(404, f"Export file not found on disk: {clip.export_path}")

    # Build the static-file URL relative to the /exports/ mount
    try:
        relative = export_path.relative_to(settings.exports_dir)
        url = f"/exports/{relative.as_posix()}"
    except ValueError:
        url = None

    return {
        "url": url,
        "path": str(export_path),
        "filename": export_path.name,
        "size": export_path.stat().st_size,
    }


@router.get("/{clip_id}/parts/{part_num}/download")
async def download_export_part(
    clip_id: str,
    part_num: int,
    session: AsyncSession = Depends(get_session),
):
    """Download a specific part of a split export."""
    result = await session.execute(select(ClipModel).where(ClipModel.id == clip_id))
    clip = result.scalar_one_or_none()
    if not clip:
        raise HTTPException(404, f"Clip {clip_id} not found")

    parts = clip.export_parts or []
    part_entry = next((p for p in parts if p.get("part_num") == part_num), None)
    if not part_entry:
        raise HTTPException(404, f"Part {part_num} not found for clip {clip_id}")

    part_path = Path(part_entry["path"])
    if not part_path.exists():
        raise HTTPException(404, f"Part file not found on disk: {part_entry['path']}")

    return FileResponse(
        path=part_path,
        filename=part_entry["filename"],
        media_type="video/mp4",
        headers={"Content-Disposition": f'attachment; filename="{part_entry["filename"]}"'},
    )


@router.get("/{clip_id}/download-all")
async def download_all_parts(
    clip_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Stream a ZIP archive of all export parts for a clip."""
    result = await session.execute(select(ClipModel).where(ClipModel.id == clip_id))
    clip = result.scalar_one_or_none()
    if not clip:
        raise HTTPException(404, f"Clip {clip_id} not found")

    parts = clip.export_parts or []
    if not parts:
        if clip.export_path:
            ep = Path(clip.export_path)
            if ep.exists():
                parts = [{"part_num": 1, "total_parts": 1, "path": clip.export_path, "filename": ep.name, "duration": clip.duration}]
        if not parts:
            raise HTTPException(404, f"No export files found for clip {clip_id}")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_STORED) as zf:
        for entry in parts:
            p = Path(entry["path"])
            if p.exists():
                zf.write(p, arcname=entry["filename"])
    buf.seek(0)

    zip_filename = f"clip_{clip_id}_parts.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_filename}"'},
    )


def _format_size(size_bytes: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if abs(size_bytes) < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"
