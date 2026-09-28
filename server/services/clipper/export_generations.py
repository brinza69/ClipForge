"""Preserving a generation of exports before a run replaces it.

THE DEFECT THIS EXISTS TO STOP, found by Codex reading the two scripts side by
side. `replan_and_rerender._preserve` copies `exports/` to `exports_pre_replan/`
and, if that directory already exists, prints "already holds the pre-replan
record" and CONTINUES. Its comment argues that the originals are never lost
because `copytree` refuses an existing destination — which is true and is not
the danger. The danger is the other direction:

    run 1   exports/ (generation A)  ->  exports_pre_replan/ = A
            exports/ becomes generation B
    run 2   exports_pre_replan/ exists, so nothing is preserved
            exports/ becomes generation C, and B is gone

`exports_pre_replan/` holds the generation before the FIRST run, not the one the
NEXT run is about to replace. On `pilotf81b`, `pilot2c8a`, `pilot6b38` and
`pilotee0e` that directory already exists, so the corpus every current
measurement rests on — the caption policy applied to 58 exports, the 37-to-22
reject figure, the survival and provenance numbers — would be overwritten with
no copy of it anywhere.

`rerender_pilots` has the mirror of the same bug: it REFUSES when the backup
exists, which is safe for the data and means the current generation can never be
preserved by it at all. One correct implementation, used by both.

THE RULE. Every run preserves the generation it is about to replace, into a
directory that does not yet exist. Nothing is ever written into an existing one.
A run that cannot preserve does not proceed — which is the opposite of the
behaviour this replaces, and the point.

AND A COPY IS NOT AN IDENTIFICATION. Codex asked for the generations to be kept
apart "with an inventory and hashes for the MP4s and the sidecars", because four
directories of similar files are not distinguishable by looking at them. The
inventory is written INSIDE the preserved directory, so it travels with the copy
rather than living somewhere that can drift from it.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Any

__all__ = ["INVENTORY_NAME", "inventory", "preserve", "generations"]

#: Written inside each preserved directory. Named with a leading underscore so
#: it sorts away from the clips and cannot be mistaken for one.
INVENTORY_NAME = "_generation.json"

NO_SOURCE = "there_is_no_exports_directory_to_preserve"
EMPTY_SOURCE = "the_exports_directory_is_empty"
NO_FREE_NAME = "every_candidate_destination_already_exists"
COPY_FAILED = "the_copy_did_not_complete"


def _digest(path: Path) -> tuple[str | None, int | None]:
    try:
        data = path.read_bytes()
    except Exception:
        return None, None
    return hashlib.sha256(data).hexdigest(), len(data)


def inventory(directory: Path | str) -> dict:
    """Every file in a generation, with its size and sha256.

    Unreadable files are listed with `sha256: null` and counted, never skipped:
    a generation that is short of a file must not look complete, and an
    inventory that quietly omitted one would be exactly that.
    """
    root = Path(directory)
    out: dict[str, Any] = {"schema": "clipper_export_generation_v1",
                           "directory": str(root), "files": [], "bytes": 0,
                           "unreadable": 0,
                           "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    if not root.is_dir():
        out["why"] = NO_SOURCE
        return out
    for path in sorted(root.iterdir()):
        if not path.is_file() or path.name == INVENTORY_NAME:
            continue
        sha, size = _digest(path)
        if sha is None:
            out["unreadable"] += 1
        else:
            out["bytes"] += size or 0
        out["files"].append({"name": path.name, "sha256": sha, "bytes": size})
    out["why"] = None
    return out


def generations(project_dir: Path | str, prefix: str = "exports_") -> list[str]:
    """The preserved generations already on disk, oldest name first."""
    root = Path(project_dir)
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir()
                  if p.is_dir() and p.name.startswith(prefix)
                  and p.name != "exports")


def preserve(project_dir: Path | str, *, label: str = "pre_replan",
             limit: int = 99) -> dict:
    """Copy the CURRENT `exports/` aside, into a directory that does not exist.

    Returns `{destination, files, bytes, why}`. `why` is set and `destination`
    is None when nothing was preserved, and a caller that proceeds on that has
    thrown away the generation it was about to replace.

    The first destination is `exports_<label>`; if that is taken — which it is
    on all four pilots — the name gains a serial, `exports_<label>_02` and so
    on. A serial rather than a timestamp because the ORDER is the thing a reader
    needs, and two runs a second apart sort by name in the order they happened.
    """
    root = Path(project_dir)
    exports = root / "exports"
    out: dict[str, Any] = {"destination": None, "files": 0, "bytes": 0,
                           "why": None, "existing": generations(root)}
    if not exports.is_dir():
        out["why"] = NO_SOURCE
        return out
    if not any(p.is_file() for p in exports.iterdir()):
        # An empty source is not a generation. Preserving it would create a
        # directory that looks like a record and holds nothing.
        out["why"] = EMPTY_SOURCE
        return out

    base = root / f"exports_{label}"
    dest = base if not base.exists() else None
    if dest is None:
        for serial in range(2, limit + 1):
            candidate = root / f"exports_{label}_{serial:02d}"
            if not candidate.exists():
                dest = candidate
                break
    if dest is None:
        out["why"] = NO_FREE_NAME
        return out

    shutil.copytree(exports, dest)
    got = inventory(dest)
    # WRITTEN INSIDE THE COPY, so it cannot drift from what it describes.
    (dest / INVENTORY_NAME).write_text(
        json.dumps({**got, "preserved_from": str(exports), "label": label},
                   indent=1), encoding="utf-8")
    if not got["files"]:
        out["why"] = COPY_FAILED
        return out
    out["destination"] = str(dest)
    out["files"] = len(got["files"])
    out["bytes"] = got["bytes"]
    out["unreadable"] = got["unreadable"]
    return out
