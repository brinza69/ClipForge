"""Small, shared guards for files returned by media endpoints."""

from pathlib import Path


def is_usable_file(path: str | Path, minimum_bytes: int = 1024) -> bool:
    """Return true only for a regular file with enough bytes to be useful."""
    try:
        candidate = Path(path)
        return candidate.is_file() and candidate.stat().st_size > minimum_bytes
    except (OSError, TypeError, ValueError):
        return False
