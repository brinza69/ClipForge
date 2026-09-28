"""S8a: freeze which export a reviewer is being asked about.

This binds the full media bytes and the sidecar observed at session creation;
it is NOT a visual or duration-quality check and cannot prove that an old
sidecar was written by the encode beside it. The renderer version comes from
that sidecar, never from the code installed when somebody opens the review.
Missing historical identities remain unknown. No files are copied or edited.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from services.clipper import storage

SCHEMA = "review_media_v1"


class MediaChanged(ValueError):
    """The session cannot continue against these media/provenance bytes."""


def _digest(path: Path) -> tuple[str, int]:
    hasher = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
            size += len(chunk)
    return hasher.hexdigest(), size


def _optional_id(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or value != value.strip():
        raise MediaChanged(f"invalid_{name}")
    return value


def _time(value: Any) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _declarations(sidecar: Any, clip: dict) -> tuple[str | None, str | None, str]:
    if not isinstance(sidecar, dict):
        raise MediaChanged("render_sidecar_not_a_record")
    for key in ("clip_id", "project_id"):
        if not isinstance(sidecar.get(key), str) or sidecar[key] != clip.get(key):
            raise MediaChanged(f"render_sidecar_{key}_mismatch")
    for source_key, row_key in (("source_start", "start_time"),
                                ("source_end", "end_time"),
                                ("duration", "duration")):
        stored, current = sidecar.get(source_key), clip.get(row_key)
        if not _time(stored) or not _time(current) or stored != current:
            raise MediaChanged(f"render_sidecar_{source_key}_mismatch")
    if clip["end_time"] <= clip["start_time"] or clip["duration"] <= 0:
        raise MediaChanged("invalid_source_window")

    selected = _optional_id(clip.get("selection_run_id"), "selection_run_id")
    if _optional_id(sidecar.get("selection_run_id"), "selection_run_id") != selected:
        raise MediaChanged("render_selection_run_mismatch")
    version = _optional_id(sidecar.get("render_version"), "render_version")
    transcript = sidecar.get("transcript")
    if transcript is not None and not isinstance(transcript, str):
        raise MediaChanged("render_transcript_not_text")
    return version, selected, transcript or ""


def capture(clip: dict) -> dict:
    """Snapshot the actual export and its matching source-window declaration.

    Equal file length is not equal content, and a prefix hash misses a change
    near EOF, so the digest always covers the complete file.
    """
    path = clip.get("export_path")
    if not isinstance(path, (str, Path)) or not storage.is_usable_output(path):
        raise MediaChanged("no_full_export")
    media = Path(path).resolve()
    sidecar_path = media.with_suffix(".json")
    try:
        raw = sidecar_path.read_bytes()
        sidecar = json.loads(raw)
    except (OSError, ValueError, UnicodeError) as exc:
        raise MediaChanged("unreadable_render_sidecar") from exc
    version, selected, transcript = _declarations(sidecar, clip)
    try:
        digest, size = _digest(media)
    except OSError as exc:
        raise MediaChanged("unreadable_export") from exc
    snapshot = {
        "schema": SCHEMA, "path": str(media), "sha256": digest, "size_bytes": size,
        "sidecar_path": str(sidecar_path),
        "sidecar_sha256": hashlib.sha256(raw).hexdigest(),
        "render_version": version, "selection_run_id": selected,
        "clip_id": clip["clip_id"], "project_id": clip["project_id"],
        "presentation": {
            "start_time": clip["start_time"], "end_time": clip["end_time"],
            "duration": clip["duration"], "export_path": str(media),
            # The DB text can be edited after the export. No fallback to it:
            # missing historical text means no aligned transcript to show.
            "transcript_text": transcript,
        },
    }
    # Catch ordinary replacement during capture, before a session is saved.
    verify(snapshot)
    return snapshot


def verify(snapshot: Any) -> Path:
    """Refuse a replaced file before displaying it or accepting its answer.

    Sessions predating this contract remain readable as historical results,
    but cannot accept new answers against unbound/current files.
    """
    if not isinstance(snapshot, dict) or snapshot.get("schema") != SCHEMA:
        raise MediaChanged("session_has_no_media_snapshot")
    for key in ("sha256", "sidecar_sha256"):
        value = snapshot.get(key)
        if (not isinstance(value, str) or len(value) != 64
                or any(c not in "0123456789abcdef" for c in value)):
            raise MediaChanged("malformed_media_snapshot")
    if (type(snapshot.get("size_bytes")) is not int or snapshot["size_bytes"] <= 0
            or not isinstance(snapshot.get("presentation"), dict)
            or not all(isinstance(snapshot.get(k), str) and snapshot[k]
                       for k in ("path", "sidecar_path"))):
        raise MediaChanged("malformed_media_snapshot")
    try:
        path = Path(snapshot["path"])
        digest, size = _digest(path)
        sidecar_bytes = Path(snapshot["sidecar_path"]).read_bytes()
        sidecar_digest = hashlib.sha256(sidecar_bytes).hexdigest()
    except (OSError, ValueError) as exc:
        raise MediaChanged("review_media_unreadable") from exc
    if size != snapshot["size_bytes"] or digest != snapshot["sha256"]:
        raise MediaChanged("review_export_changed")
    if sidecar_digest != snapshot["sidecar_sha256"]:
        raise MediaChanged("review_sidecar_changed")
    try:
        version, selected, transcript = _declarations(json.loads(sidecar_bytes), {
            **snapshot["presentation"], "clip_id": snapshot.get("clip_id"),
            "project_id": snapshot.get("project_id"),
            "selection_run_id": snapshot.get("selection_run_id"),
        })
    except (ValueError, UnicodeError) as exc:
        raise MediaChanged("snapshot_declarations_changed") from exc
    if (version != snapshot.get("render_version") or selected != snapshot.get("selection_run_id")
            or transcript != snapshot["presentation"].get("transcript_text")
            or snapshot["presentation"].get("export_path") != snapshot["path"]):
        raise MediaChanged("snapshot_declarations_changed")
    return path
