"""
ClipForge — Disk cleanup for cancelled / failed jobs.

When a pipeline job is cancelled or fails, its intermediate working files
(downloaded source, erased video, per-variant dirs) are no longer useful but
were previously left on disk forever. Cleanup removes those files while
preserving finalized outputs that may already exist in the same workspace.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Iterable

from config import settings

logger = logging.getLogger("clipforge.cleanup")


def _dir_size(p: Path) -> int:
    total = 0
    for sub in p.rglob("*"):
        try:
            if sub.is_file():
                total += sub.stat().st_size
        except OSError:
            pass
    return total


def _preserved_files(media: Path, preserve_paths: Iterable[str | Path]) -> set[Path]:
    """Resolve only files that are safely inside this project's media tree."""
    root = media.resolve()
    preserved: set[Path] = set()
    for raw in preserve_paths:
        try:
            candidate = Path(raw).resolve()
            if candidate != root and candidate.is_relative_to(root):
                preserved.add(candidate)
        except (OSError, TypeError, ValueError):
            continue

    # Remix and parallel pipelines use this stable name before persisting all
    # metadata. Keeping it covers a crash between the render and metadata
    # update, which is exactly when cleanup must not destroy a good export.
    try:
        preserved.update(p.resolve() for p in media.rglob("video_final.mp4") if p.is_file())
    except OSError:
        pass
    return preserved


def _remove_tree(path: Path, preserved: set[Path]) -> tuple[int, list[str]]:
    """Remove a tree but never remove a preserved file or its parent dirs."""
    freed = 0
    removed: list[str] = []
    try:
        children = list(path.iterdir())
    except OSError:
        return 0, removed

    for child in children:
        try:
            resolved = child.resolve()
            if resolved in preserved:
                continue
            if child.is_symlink() or child.is_file():
                size = child.stat().st_size if child.is_file() else 0
                child.unlink(missing_ok=True)
                freed += size
                removed.append(str(child))
                continue
            if child.is_dir():
                child_freed, child_removed = _remove_tree(child, preserved)
                freed += child_freed
                removed.extend(child_removed)
                try:
                    child.rmdir()
                    removed.append(str(child))
                except OSError:
                    # A preserved output or a concurrent writer keeps it alive.
                    pass
        except OSError:
            logger.warning("could not remove cleanup entry %s", child, exc_info=True)
    return freed, removed


def cleanup_job_workspace(
    project_id: str,
    *,
    preserve_paths: Iterable[str | Path] = (),
    remove_outputs: bool = False,
) -> dict:
    """Clean a failed/cancelled workspace without deleting final outputs.

    By default, files listed in ``preserve_paths`` and any ``video_final.mp4``
    below the workspace survive. ``remove_outputs=True`` is reserved for an
    explicit user delete action and retains the old full-tree deletion.
    Returns ``{"freed_bytes", "removed"}``.

    NOTE: only call this for cancelled / failed jobs unless
    ``remove_outputs=True`` is an explicit delete operation.
    """
    stats = {"freed_bytes": 0, "removed": []}
    if not project_id:
        return stats
    media = Path(settings.media_dir) / project_id
    if media.exists():
        try:
            if remove_outputs:
                stats["freed_bytes"] = _dir_size(media)
                shutil.rmtree(media, ignore_errors=True)
                stats["removed"].append(str(media))
            else:
                preserved = _preserved_files(media, preserve_paths)
                stats["freed_bytes"], stats["removed"] = _remove_tree(media, preserved)
        except Exception as e:
            logger.exception(f"could not remove {media}: {e}")
    return stats
