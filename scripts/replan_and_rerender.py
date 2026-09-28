"""Re-PLAN the stored clips and render what the planner produces now.

    python scripts/replan_and_rerender.py pilot6b38 --dry-run
    python scripts/replan_and_rerender.py pilotf81b pilotee0e pilot6b38 pilot2c8a

HOW THIS DIFFERS FROM `rerender_pilots.py`, which is the whole point. That script
REPLAYS: it re-encodes from the sidecar already on disk, so the plan is the one
written on 22 August and only the renderer changes. That is why R1 landed on
29 August, removed 116 invisible cuts from the planner, and changed no export at
all — the corpus still measures exactly 116. This one runs `_decide_render`
again, so the plan is rebuilt and every planner fix since actually reaches a
file.

WHAT IT REFUSES TO DO WITHOUT PROOF. `verify_replan.py` asks whether the re-plan
delivers what the fixes claim, without encoding anything, and this will not start
until that has passed — including its fourth proof, which is a decision a person
has to have made about the long `crop<->fit` junctions. Hours of encoding spent
on a version somebody then rejects is the failure this pair exists to avoid.

EVERY RUN PRESERVES THE GENERATION IT IS ABOUT TO REPLACE, into a directory that
does not yet exist — `services.clipper.export_generations`, which both this and
`rerender_pilots.py` use so there is one implementation. The version before it
CONTINUED when `exports_pre_replan/` already existed, on the reasoning that
`copytree` refuses an existing destination and the originals were therefore
safe. They were; the generation the run was about to replace was not. That
directory holds the corpus from before the FIRST replan, and it exists on all
four pilots, so a second run would have overwritten the current 58 exports —
the caption policy applied, the 37-to-22 reject figure, the survival and
provenance numbers — with no copy of them anywhere.

A CLIP THAT CANNOT BE RENDERED KEEPS ITS ROW AND FAILS THE RUN. It does not
vanish, and the sidecar is written only after the encode returns.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))

DATA = Path(os.environ.get("CLIPFORGE_DATA_DIR") or (_ROOT / "data")) / "clipper"

#: The label the preserved generations carry: `exports_pre_replan/`, then
#: `exports_pre_replan_02/` and so on. A serial rather than a timestamp because
#: the ORDER is what a reader needs, and two runs a second apart sort by name in
#: the order they happened.
BACKUP = "exports_pre_replan"


def _preserve(project_id: str) -> str | None:
    """Copy the CURRENT `exports/` aside. Returns why not, or None.

    THE VERSION THIS REPLACES CONTINUED WHEN THE BACKUP EXISTED, and argued the
    originals were safe because `copytree` refuses an existing destination. That
    is true and is not the danger. The danger runs the other way:

        run 1   exports/ = A  ->  exports_pre_replan/ = A; exports/ becomes B
        run 2   the backup exists, nothing is preserved; exports/ becomes C
                and B is gone

    `exports_pre_replan/` holds the generation before the FIRST run, never the
    one the NEXT run replaces — and it already exists on all four pilots, so the
    corpus every current measurement rests on was one run away from being
    overwritten with no copy anywhere.

    Now every run preserves into a directory that does not exist, and a run that
    cannot preserve does not proceed.
    """
    from services.clipper import export_generations as eg

    got = eg.preserve(DATA / project_id, label="pre_replan")
    if got["why"]:
        return got["why"]
    print(f"  preserved {got['files']} files "
          f"({got['bytes'] / 1e6:.0f} MB) to {Path(got['destination']).name}/",
          flush=True)
    return None


async def _render_one(project_id: str, clip_id: str, *, output_dir: Path | None = None) -> dict:
    from services.clipper import storage
    from workers.clipper_render_output import render_export
    from workers.clipper_render_plan import _decide_render, _load, _source_path

    clip, project = await _load(clip_id)
    if project.id != project_id:
        raise ValueError("clip belongs to another project")
    paths = storage.paths(project_id)
    work = Path(output_dir) if output_dir is not None else paths["exports_dir"]
    work.mkdir(parents=True, exist_ok=True)
    out = work / f"{clip_id}.mp4"
    if output_dir is not None and (out.exists() or out.with_suffix(".json").exists()):
        raise FileExistsError(f"probe output already exists: {out}")
    decision = await _decide_render(clip, project, work)
    result = await render_export(clip, project, decision, out, src=str(_source_path(project)))
    body = result["sidecar"]
    return {"clip": clip_id, "refused": None,
            "shots": len((decision["dyn"] or {}).get("shots") or []),
            "renderer": body["render_version"], "path": str(out),
            "bytes": body["output_identity"].get("bytes")}


async def _project(project_id: str, *, dry_run: bool,
                   only: set[str] | None = None,
                   output_root: Path | None = None) -> list[dict]:
    from sqlalchemy import select

    from database import async_session, init_db
    from models import ClipModel

    await init_db()
    async with async_session() as session:
        clips = (await session.execute(
            select(ClipModel.id).where(ClipModel.project_id == project_id)
        )).scalars().all()
    have = [c for c in clips
            if (DATA / project_id / "exports" / f"{c}.mp4").exists()
            and (only is None or c in only)]
    if dry_run:
        return [{"clip": c, "refused": None, "dry_run": True} for c in have]

    output_dir = output_root / project_id if output_root is not None else None
    if output_dir is not None:
        live = (DATA / project_id / "exports").resolve()
        resolved = output_dir.resolve()
        if resolved == live or live in resolved.parents:
            return [{"clip": "-", "refused": "probe output must be outside exports"}]
    else:
        why = _preserve(project_id)
        if why:
            return [{"clip": "-", "refused": why}]

    out = []
    for i, clip_id in enumerate(have, 1):
        started = time.time()
        try:
            row = await _render_one(project_id, clip_id, output_dir=output_dir)
        except Exception as exc:
            row = {"clip": clip_id, "refused": f"{type(exc).__name__}: {exc}"}
        row["seconds"] = round(time.time() - started, 1)
        outcome = row["refused"] or f"{row.get('shots')} shots"
        print(f"  [{i}/{len(have)}] {clip_id}  {outcome}  {row['seconds']}s",
              flush=True)
        out.append(row)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("projects", nargs="+")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default="",
                    help="comma-separated clip ids — the micro-gate Codex asks "
                         "for before the full corpus")
    ap.add_argument("--output-dir", type=Path,
                    help="render isolated probes under DIR/project; leave exports unchanged")
    args = ap.parse_args()
    only = {c.strip() for c in args.only.split(",") if c.strip()} or None

    rows: list[dict] = []
    for project in args.projects:
        print(f"\n=== {project}", flush=True)
        rows.extend({"project": project, **row}
                    for row in asyncio.run(_project(project,
                                                    dry_run=args.dry_run,
                                                    only=only, output_root=args.output_dir)))

    refused = [r for r in rows if r["refused"]]
    print(f"\n{len(rows) - len(refused)} rendered, {len(refused)} refused")
    for row in refused:
        print(f"FAIL: {row['project']}/{row['clip']}: {row['refused']}")
    if not rows:
        print("FAIL: nothing to render — a pass over nothing is not a pass")
    return 1 if refused or not rows else 0


if __name__ == "__main__":
    raise SystemExit(main())
