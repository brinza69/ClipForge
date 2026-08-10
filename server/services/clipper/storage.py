"""
ClipForge — AI Stream Clipper: on-disk artifact store.

SQLite holds the small, queryable rows (projects, clips, feedback). Everything
heavy lives here instead: the analysis proxy, the 16 kHz speech track, sampled
frames and the per-pass JSON artifacts, which run to tens of megabytes for a
multi-hour stream and would bloat the DB file for no gain.

Every write is atomic (temp file + os.replace), the same precedent as
services/doodle/storage.py and services/tiktok_transform/storage.py, so a crash
mid-write never leaves a half-written signals.json that the resume path then
chokes on.

Layout (settings.clipper_dir / {project_id}/):
    source/            downloaded original (or a copy of an upload)
    proxy/proxy.mp4    ~480px wide, 10 fps, no audio — every analysis pass reads this
    audio/speech.wav   mono 16 kHz PCM
    frames/            sparse sampled JPEGs
    thumbs/            per-candidate thumbnails
    analysis/          signals|faces|regions|segments|candidates|meta .json
    previews/{clip_id}.mp4
    exports/{clip_id}.mp4 (+ {clip_id}.json sidecar)
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger("clipforge.clipper.storage")

# Allowlist for the HTTP artifacts endpoint: the only names read_artifact and
# write_artifact will ever turn into a filesystem path.
ARTIFACT_NAMES: frozenset[str] = frozenset(
    {"signals", "faces", "regions", "segments", "candidates", "meta"}
)

_SUBDIRS = (
    "source",
    "proxy",
    "audio",
    "frames",
    "thumbs",
    "analysis",
    "previews",
    "exports",
)

# Anything that could turn a project id or a clip id into a path outside the
# project. Checked before the path is built, on top of the resolve() containment
# check below — an id reaches here straight off an HTTP route.
_ID_REJECT_CHARS = ("/", "\\", "\0", ":")


# ── Path safety ──────────────────────────────────────────────────────────────

def _check_id(value: str, kind: str) -> str:
    """Reject ids that carry path structure. Traversal has to be impossible
    before the string ever touches the filesystem."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"invalid {kind}: must be a non-empty string")
    if value in (".", "..") or value.startswith(".."):
        raise ValueError(f"invalid {kind}: {value!r}")
    for ch in _ID_REJECT_CHARS:
        if ch in value:
            raise ValueError(f"invalid {kind}: {value!r} contains {ch!r}")
    return value


def _clipper_root() -> Path:
    from config import settings

    return settings.clipper_dir


def project_dir(project_id: str) -> Path:
    _check_id(project_id, "project_id")
    return _clipper_root() / project_id


def safe_join(project_id: str, *parts: str) -> Path:
    """Join `parts` under the project dir and prove the result stays inside it.

    Use this for every path derived from user- or client-supplied text (clip
    ids, filenames). Raises ValueError on escape rather than returning a path
    the caller might then happily read or delete."""
    base = project_dir(project_id)
    candidate = base
    for part in parts:
        if not isinstance(part, str) or not part:
            raise ValueError("path segment must be a non-empty string")
        if "\0" in part:
            raise ValueError("path segment contains a null byte")
        if Path(part).is_absolute() or os.path.splitdrive(part)[0]:
            raise ValueError(f"path segment must be relative: {part!r}")
        candidate = candidate / part

    resolved_base = base.resolve()
    resolved = candidate.resolve()
    if resolved != resolved_base and not resolved.is_relative_to(resolved_base):
        raise ValueError(f"path escapes project dir: {'/'.join(parts)!r}")
    return resolved


# ── Layout ───────────────────────────────────────────────────────────────────

def paths(project_id: str) -> dict[str, Path]:
    """Canonical artifact paths for a project. Pure — creates nothing."""
    d = project_dir(project_id)
    analysis = d / "analysis"
    return {
        "root": d,
        "source_dir": d / "source",
        "proxy_dir": d / "proxy",
        "proxy": d / "proxy" / "proxy.mp4",
        "audio_dir": d / "audio",
        "audio": d / "audio" / "speech.wav",
        "frames_dir": d / "frames",
        "thumbs_dir": d / "thumbs",
        "analysis_dir": analysis,
        "previews_dir": d / "previews",
        "exports_dir": d / "exports",
        "signals": analysis / "signals.json",
        "faces": analysis / "faces.json",
        "regions": analysis / "regions.json",
        "segments": analysis / "segments.json",
        "candidates": analysis / "candidates.json",
        "meta": analysis / "meta.json",
    }


def ensure_dirs(project_id: str) -> None:
    """Create the project tree. Idempotent — every stage calls it on entry so a
    resumed run never assumes a previous stage got that far."""
    d = project_dir(project_id)
    for sub in _SUBDIRS:
        (d / sub).mkdir(parents=True, exist_ok=True)


def preview_path(project_id: str, clip_id: str) -> Path:
    _check_id(clip_id, "clip_id")
    return safe_join(project_id, "previews", f"{clip_id}.mp4")


def export_path(project_id: str, clip_id: str) -> Path:
    _check_id(clip_id, "clip_id")
    return safe_join(project_id, "exports", f"{clip_id}.mp4")


# ── Artifacts ────────────────────────────────────────────────────────────────

def _artifact_path(project_id: str, name: str) -> Path:
    if name not in ARTIFACT_NAMES:
        raise ValueError(f"unknown artifact: {name!r}")
    # Name is allowlisted, so this can only land in analysis/ — safe_join is
    # still the single place that proves containment.
    return safe_join(project_id, "analysis", f"{name}.json")


def _json_default(obj: Any) -> Any:
    """numpy scalars/arrays come out of the signal and face passes and are not
    JSON-serialisable; a whole analysis run should not be lost on the write."""
    item = getattr(obj, "item", None)
    if callable(item) and getattr(obj, "ndim", None) == 0:
        return item()
    tolist = getattr(obj, "tolist", None)
    if callable(tolist):
        return tolist()
    raise TypeError(f"not JSON-serialisable: {type(obj).__name__}")


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{uuid.uuid4().hex[:8]}")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_artifact(project_id: str, name: str, data: dict | list) -> Path:
    """Atomically write analysis/{name}.json. Raises ValueError for a name
    outside ARTIFACT_NAMES."""
    path = _artifact_path(project_id, name)
    # Compact separators, not indent=2: signals.json carries one sample per
    # proxy frame, so pretty-printing a 3-hour stream costs tens of MB.
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=_json_default)
    _atomic_write_text(path, text)
    return path


def read_artifact(project_id: str, name: str) -> dict | list | None:
    """Return the parsed artifact, or None if it is missing or unreadable.

    Corrupt JSON is logged and swallowed on purpose: one bad artifact means the
    caller re-runs that pass, it must not take the whole project down."""
    path = _artifact_path(project_id, name)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        logger.exception(f"corrupt clipper artifact {name} for project {project_id}")
        return None


def artifact_exists(project_id: str, name: str) -> bool:
    """Existence probe used to decide whether a pass already ran. An unknown
    name is simply 'not present' — no exception from a read-only check."""
    if name not in ARTIFACT_NAMES:
        return False
    return _artifact_path(project_id, name).exists()


# ── Housekeeping ─────────────────────────────────────────────────────────────

def delete_project(project_id: str) -> None:
    """Remove the whole project tree. Silent when it was never created."""
    d = project_dir(project_id)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
        logger.info(f"clipper project {project_id} artifacts deleted")


def dir_size_bytes(project_id: str) -> int:
    """Bytes on disk for a project — the storage figure the UI shows before
    offering a cleanup. Returns 0 when nothing has been written yet."""
    d = project_dir(project_id)
    if not d.exists():
        return 0
    total = 0
    for root, _dirs, files in os.walk(d, onerror=None):
        for fname in files:
            try:
                # A render or download may be writing into this tree right now;
                # a file that vanishes mid-walk is not an error worth raising.
                total += os.path.getsize(os.path.join(root, fname))
            except OSError:
                continue
    return total
