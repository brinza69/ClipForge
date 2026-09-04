"""What the render actually produced — the half `input_fingerprint` cannot reach.

`edit_quality.input_fingerprint` is a digest over `FINGERPRINT_KEYS`: the recipe,
everything the export was made FROM. It is the right thing and it answers a
different question. Recomputing it proves the plan was not edited after the
render; it says nothing about the mp4, because the recipe never touches the mp4.
`FINGERPRINT_KEYS`' own comment records this — the output size is decided by
defaults inside two renderers "with no shared authority to read it from, so it
is absent here until one exists".

THIS IS THAT AUTHORITY, and the resolution is the rule this repo keeps
relearning: read the artefact rather than the intention. Nobody has to agree a
constant for the output size, because the delivered file has one and it can be
measured. The two renderers may disagree about what they meant to produce; they
cannot disagree about what came out.

WHY A CONTENT HASH AND NOT ONLY SIZE AND MTIME. Elsewhere in this repo — the OCR
cache — size and mtime are enough, because the question there is "is this still
the file I measured minutes ago" and a re-render always rewrites both. Here the
question is "is this the file the sidecar describes", asked of an artefact that
may be months old and may have been copied, restored from a backup, or synced.
Copying preserves size and resets mtime; a truncated transfer preserves neither
usefully. A digest answers it outright, and an mp4 is small enough that it costs
under a second.

REFUSALS, NEVER GUESSES. A file that is not there, cannot be read, or has no
video stream produces a refusal with its reason, not a record of zeroes. A
record of zeroes would satisfy every check that merely asks whether the field is
present — which is the shape of every defect this batch has found.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

__all__ = ["SCHEMA", "REFUSALS", "probe", "matches"]

SCHEMA = "clipper_output_identity_v1"

#: Why there is no identity. Each keeps the export in the denominator: an export
#: nobody could measure is not an export that measured clean.
NO_FILE = "no_file_at_that_path"
EMPTY_FILE = "file_is_empty"
UNREADABLE = "file_could_not_be_read"
NO_VIDEO = "no_readable_video_stream"
REFUSALS: tuple[str, ...] = (NO_FILE, EMPTY_FILE, UNREADABLE, NO_VIDEO)

#: Read in blocks so a large export does not have to fit in memory.
_BLOCK = 1 << 20


def _digest(path: Path) -> str:
    got = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_BLOCK):
            got.update(chunk)
    return got.hexdigest()


def probe(path: Any) -> dict:
    """`clipper_output_identity_v1` for one rendered file, or why there is none.

    Never raises. This runs at the end of a render that has already succeeded,
    and a job that failed on its own bookkeeping would be a worse outcome than
    an export whose identity is missing — the identity is a record, not a gate.
    """
    out: dict[str, Any] = {"schema": SCHEMA, "refused": None}
    try:
        target = Path(path)
    except (TypeError, ValueError):
        out["refused"] = NO_FILE
        return out
    if not target.is_file():
        out["refused"] = NO_FILE
        return out

    try:
        size = target.stat().st_size
    except OSError:
        out["refused"] = UNREADABLE
        return out
    if size <= 0:
        # A zero-byte file is present and unusable, which is a defect rather
        # than an absence — and hashing it would produce the digest of nothing,
        # a value that is identical for every empty export ever written.
        out["refused"] = EMPTY_FILE
        return out

    try:
        info = _video_info(str(target))
    except Exception:
        out["refused"] = NO_VIDEO
        return out

    try:
        out["sha256"] = _digest(target)
    except OSError:
        out["refused"] = UNREADABLE
        return out

    out.update({
        "bytes": size,
        # MEASURED FROM THE FILE, which is what makes this the shared authority
        # the two renderers never had. Neither of them has to declare a size.
        "width": int(info.get("width") or 0),
        "height": int(info.get("height") or 0),
        "duration_s": round(float(info.get("duration") or 0.0), 3),
        "fps": info.get("fps"),
        "has_audio": bool(info.get("has_audio")),
        "codec": info.get("codec") or "",
    })
    if not (out["width"] > 0 and out["height"] > 0 and out["duration_s"] > 0):
        # ffprobe answered, and answered with something that cannot describe a
        # video. Present and impossible is a defect, not an absence.
        return {"schema": SCHEMA, "refused": NO_VIDEO}
    return out


def _video_info(path: str) -> dict:
    from services.clipper.ffmpeg_tools import video_info

    return video_info(path)


def matches(recorded: Any, path: Any) -> tuple[bool | None, str | None]:
    """`(is this still that file, why not)` — `None` when it cannot be told.

    THREE ANSWERS, NOT TWO. A recorded identity that is a refusal, or a file
    that cannot be measured now, gives `None`: nothing was compared, and a
    comparison nobody could make must never read as a match. Only an actual
    digest disagreement is `False`.
    """
    if not isinstance(recorded, dict) or recorded.get("schema") != SCHEMA:
        return None, "no_recorded_identity"
    if recorded.get("refused"):
        return None, f"recorded_identity_refused_{recorded['refused']}"
    if not isinstance(recorded.get("sha256"), str) or not recorded["sha256"]:
        return None, "recorded_identity_has_no_digest"

    now = probe(path)
    if now.get("refused"):
        return None, f"file_now_{now['refused']}"
    if now["sha256"] != recorded["sha256"]:
        return False, "the_file_is_not_the_one_the_sidecar_describes"
    return True, None
