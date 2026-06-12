"""
ClipForge — Disk cleanup for cancelled / failed jobs.

When a pipeline job is cancelled or fails, its intermediate working files
(downloaded source, erased video, per-variant dirs) are no longer useful but
were previously left on disk forever. This removes the project's media subtree
while leaving finalized exports untouched.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from config import settings

logger = logging.getLogger("clipforge.cleanup")


def _dir_size(p: Path) -> int:
    total = 0
    for sub in p.rglob("*"):
        try:
            total += sub.stat().st_size
        except OSError:
            pass
    return total


def cleanup_job_workspace(project_id: str) -> dict:
    """Remove data/media/<project_id> (the pipeline's scratch + outputs for
    this project). Returns {"freed_bytes", "removed"}.

    NOTE: only call this for cancelled / failed jobs. Successful jobs keep
    their media dir because the finished video lives there and the user
    wants to download it.
    """
    freed = 0
    removed: list[str] = []
    if not project_id:
        return {"freed_bytes": 0, "removed": removed}
    media = Path(settings.media_dir) / project_id
    if media.exists():
        try:
            size = _dir_size(media)
            shutil.rmtree(media, ignore_errors=True)
            freed += size
            removed.append(str(media))
        except Exception as e:
            logger.exception(f"could not remove {media}: {e}")
    return {"freed_bytes": freed, "removed": removed}
