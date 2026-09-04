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

THE ORIGINALS ARE MOVED, NOT OVERWRITTEN, following `rerender_pilots.py` exactly:
each project's `exports/` is copied to `exports_pre_replan/` first and the run
refuses to start if that already holds a copy. The 58 stored exports are the
corpus every measurement in this batch rests on — the 116 equivalent cuts, the
84 junctions, the 37 duplicate-caption rejections, the human's 20 verdicts. A
second run that backed the NEW files up over them would erase the evidence for
every one of those numbers.

A CLIP THAT CANNOT BE RENDERED KEEPS ITS ROW AND FAILS THE RUN. It does not
vanish, and the sidecar is written only after the encode returns.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))

DATA = Path(os.environ.get("CLIPFORGE_DATA_DIR") or (_ROOT / "data")) / "clipper"

#: Where the pre-replan corpus goes. NOT `exports_pre_caption_fix/`, which holds
#: the generation before that one and must not be overwritten either.
BACKUP = "exports_pre_replan"


def _preserve(project_id: str) -> str | None:
    """Copy `exports/` aside once. Returns why not, or None."""
    root = DATA / project_id
    exports, backup = root / "exports", root / BACKUP
    if not exports.is_dir():
        return "no exports directory"
    if backup.exists():
        # A second run would back the NEW files up over the only record of the
        # old ones, and every figure in this batch would lose its evidence.
        return f"{BACKUP}/ already exists — refusing to overwrite the record"
    shutil.copytree(exports, backup)
    return None


async def _render_one(project_id: str, clip_id: str) -> dict:
    from services.clipper import dynamic_render, edit_quality, output_identity
    from services.clipper import storage
    from workers.clipper_render_plan import _decide_render, _load, _source_path

    clip, project = await _load(clip_id)
    paths = storage.paths(project_id)
    out = paths["exports_dir"] / f"{clip_id}.mp4"
    decision = await _decide_render(clip, project, paths["exports_dir"])
    plan = decision.get("dyn") or {}
    if not (plan.get("shots") or []):
        return {"clip": clip_id, "refused": "the re-plan has no shots"}

    src_w, src_h = int(project.width or 1920), int(project.height or 1080)
    dynamic_render.render_dynamic_clip(
        str(_source_path(project)), plan, str(out),
        start=float(clip.start_time or 0.0),
        work_dir=str(paths["exports_dir"]),
        ass_path=decision.get("ass_path"),
        src_w=src_w, src_h=src_h,
        drop_spans=decision.get("drop"),
        watermark=decision.get("watermark") or "")

    body = {
        "clip_id": clip_id, "project_id": project_id,
        "source": str(_source_path(project)),
        "source_start": float(clip.start_time or 0.0),
        "source_end": float(clip.end_time or 0.0),
        "duration": float(clip.duration or 0.0),
        "render_version": dynamic_render.RENDER_VERSION,
        "layout_plan": decision.get("plan"),
        "caption_plan": clip.caption_plan,
        "caption_y": decision.get("caption_y"),
        "caption_policy": decision.get("caption_policy"),
        "dynamic_plan": plan,
        "drop_spans": decision.get("drop"),
        "content_type": clip.content_type,
        "edit_profile": decision.get("edit_profile"),
        "creator_view": decision.get("creator_view"),
        "regime_view": decision.get("regime_view"),
        "rhythm_view": decision.get("rhythm_view"),
        "analysis_version": project.analysis_version,
        "ranker_version": clip.ranker_version,
        "render": decision.get("render"),
    }
    body["input_fingerprint"] = edit_quality.input_fingerprint(body)
    body["output_identity"] = output_identity.probe(out)
    storage.atomic_write_json(out.with_suffix(".json"), body, indent=2,
                              ensure_ascii=False, default=str)
    return {"clip": clip_id, "refused": None,
            "shots": len(plan.get("shots") or []),
            "bytes": body["output_identity"].get("bytes")}


async def _project(project_id: str, *, dry_run: bool) -> list[dict]:
    from sqlalchemy import select

    from database import async_session, init_db
    from models import ClipModel

    await init_db()
    async with async_session() as session:
        clips = (await session.execute(
            select(ClipModel.id).where(ClipModel.project_id == project_id)
        )).scalars().all()
    have = [c for c in clips
            if (DATA / project_id / "exports" / f"{c}.mp4").exists()]
    if dry_run:
        return [{"clip": c, "refused": None, "dry_run": True} for c in have]

    why = _preserve(project_id)
    if why:
        return [{"clip": "-", "refused": why}]

    out = []
    for i, clip_id in enumerate(have, 1):
        started = time.time()
        try:
            row = await _render_one(project_id, clip_id)
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
    args = ap.parse_args()

    rows: list[dict] = []
    for project in args.projects:
        print(f"\n=== {project}", flush=True)
        rows.extend({"project": project, **row}
                    for row in asyncio.run(_project(project,
                                                    dry_run=args.dry_run)))

    refused = [r for r in rows if r["refused"]]
    print(f"\n{len(rows) - len(refused)} rendered, {len(refused)} refused")
    for row in refused:
        print(f"FAIL: {row['project']}/{row['clip']}: {row['refused']}")
    if not rows:
        print("FAIL: nothing to render — a pass over nothing is not a pass")
    return 1 if refused or not rows else 0


if __name__ == "__main__":
    raise SystemExit(main())
