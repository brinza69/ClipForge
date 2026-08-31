"""Re-render stored exports with the CURRENT renderer, from their own sidecars.

    python scripts/rerender_pilots.py pilot2c8a pilot6b38
    python scripts/rerender_pilots.py --all --dry-run

WHY THIS EXISTS. The exports on disk were rendered before the caption fix of
31 August 2026, and 27 of the 88 stored clips have seconds with no caption at
all — `dynamic_render` burned the subtitles onto a frame whose letterbox bars
are transparent, so the text kept an alpha of zero and the overlay composited it
away. Every gate that reads those files is reading a picture the renderer would
no longer produce.

IT RE-RENDERS FROM THE SIDECAR, not from a re-analysis. The plan, the caption
file and the source window are exactly the ones that produced the original, so
the ONLY difference in the output is the renderer. That is what makes a
before/after comparison mean anything.

THE ORIGINALS ARE MOVED, NOT OVERWRITTEN. Each project's `exports/` is copied to
`exports_pre_caption_fix/` first, and the run refuses to start if that directory
already holds a copy — a second run would otherwise back up the NEW files over
the only record of the old ones, and the evidence for the defect would be gone.

A CLIP THAT CANNOT BE RE-RENDERED KEEPS ITS ROW AND FAILS THE RUN. It does not
vanish and it is not left half-written: the render goes to a temporary name and
is moved into place only when ffmpeg returns zero.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))

DATA = Path(os.environ.get("CLIPFORGE_DATA_DIR") or (_ROOT / "data")) / "clipper"
BACKUP = "exports_pre_caption_fix"

from services.clipper.dynamic_render import (  # noqa: E402
    build_dynamic_cmd, write_sendcmd)


def _needed(side: dict) -> str | None:
    """Why this sidecar cannot be re-rendered, or None."""
    plan = side.get("dynamic_plan")
    if not isinstance(plan, dict) or not plan.get("shots"):
        return "no_dynamic_plan"
    if not (plan.get("src_w") and plan.get("src_h")):
        return "no_source_dimensions"
    src = (side.get("source") or {}).get("path")
    if not src or not Path(src).exists():
        return "source_missing"
    for key in ("source_start", "duration"):
        if not isinstance(side.get(key), (int, float)):
            return f"no_{key}"
    return None


def _render(path: Path, side: dict, dry: bool) -> dict:
    row = {"project": path.parent.parent.name, "clip": path.stem}
    why = _needed(side)
    if why:
        row["refused"] = why
        return row

    plan = side["dynamic_plan"]
    mp4 = path.with_suffix(".mp4")
    ass = path.with_suffix(".ass")
    row["had_ass"] = ass.exists()
    if dry:
        row["would_render"] = str(mp4)
        return row

    tmp = mp4.with_suffix(".rendering.mp4")
    cmd_path = write_sendcmd(plan, plan["src_w"], plan["src_h"],
                             str(path.with_suffix(".cmd.txt")))
    argv = build_dynamic_cmd(
        (side["source"] or {})["path"], plan, cmd_path,
        str(ass) if ass.exists() else None, str(tmp),
        start=float(side["source_start"]), duration=float(side["duration"]),
        src_w=plan["src_w"], src_h=plan["src_h"],
        fps=int((side.get("render") or {}).get("fps") or 30),
        crf=int((side.get("render") or {}).get("crf") or 18),
        preset=str((side.get("render") or {}).get("preset") or "medium"))

    started = time.time()
    result = subprocess.run(argv, capture_output=True, text=True)
    row["seconds"] = round(time.time() - started, 1)
    if result.returncode != 0 or not tmp.exists():
        # HALF-WRITTEN IS WORSE THAN MISSING. The temporary file is removed so
        # nothing downstream reads a truncated render as a finished one.
        tmp.unlink(missing_ok=True)
        row["refused"] = f"ffmpeg_exit_{result.returncode}"
        row["stderr"] = result.stderr[-400:]
        return row
    tmp.replace(mp4)
    row["bytes"] = mp4.stat().st_size
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("projects", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    projects = sorted({p.parent.parent.name
                       for p in DATA.glob("*/exports/*.json")})
    missing: list[str] = []
    if not args.all:
        wanted = list(dict.fromkeys(args.projects))
        if not wanted:
            ap.error("name a project or pass --all")
        missing = [n for n in wanted if n not in projects]
        projects = [n for n in projects if n in set(wanted)]

    bad = [f"project asked for and not found: {n}" for n in missing]
    rows: list[dict] = []

    for name in projects:
        exports = DATA / name / "exports"
        backup = DATA / name / BACKUP
        if not args.dry_run:
            if backup.exists():
                # A SECOND RUN WOULD BACK UP THE NEW FILES over the only record
                # of the old ones, and the evidence for the defect would be gone.
                bad.append(f"{name}: {BACKUP}/ already exists — refusing to "
                           "overwrite the record of the originals")
                continue
            shutil.copytree(exports, backup)
            print(f"{name}: originals copied to {BACKUP}/", flush=True)

        for path in sorted(exports.glob("*.json")):
            try:
                side = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                rows.append({"project": name, "clip": path.stem,
                             "refused": f"sidecar_unreadable_{type(exc).__name__}"})
                continue
            row = _render(path, side if isinstance(side, dict) else {},
                          args.dry_run)
            rows.append(row)
            # PRINTED AS IT GOES. A render run is long, and a report that only
            # arrives at the end is a report an interruption destroys.
            mark = row.get("refused") or f"{row.get('seconds', 0)}s"
            print(f"  {row['project']}/{row['clip']:14} {mark}", flush=True)

    refused = [r for r in rows if r.get("refused")]
    for r in refused:
        bad.append(f"{r['project']}/{r['clip']}: {r['refused']}")
    if not rows and not bad:
        bad.append("nothing to render — a pass over nothing is not a pass")

    print()
    print(f"clips {len(rows)}   refused {len(refused)}")
    print(f"seconds spent {round(sum(r.get('seconds', 0) for r in rows))}")
    for line in bad:
        print(f"FAIL: {line}")
    if not bad:
        print("every clip re-rendered; the originals are in "
              f"<project>/{BACKUP}/")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
