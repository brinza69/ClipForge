"""
ClipForge — Exports & Storage Router
API endpoints for export management, storage info, and cleanup.
"""

import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from models import ClipModel, ClipStatus, ProjectModel
from config import settings

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
async def cleanup_storage(target: str = "temp"):
    """
    Cleanup storage.
    Targets: temp, cache, exports, media
    """
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


def _format_size(size_bytes: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if abs(size_bytes) < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"
