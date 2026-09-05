"""The same clip planned with and without the geometric second camera.

    python scripts/render_camera_probe.py pilotf81b 30d7c6d4eae5

WHY IT EXISTS. `layout_policy` was reported on the strength of a PLAN
measurement — 48.1 s of delivered windows that exclude the subject, dropping to
0.0 s — and a plan is not the delivered artefact. Codex said so in the same
words the repo already uses about `move: push` and `caption_plan.y_pct`: the
guard stops the `game` family being CHOSEN, and that on its own demonstrates
neither "the rhythm is untouched" nor "the empty background is gone from the
export". This renders both, through `render_dynamic_clip`, so the second claim
is about a file.

WHAT IT REPORTS BESIDE THE FILES. The boundary times of both plans, matched
within 40 ms, because equal shot COUNTS prove nothing — the same number of cuts
at different moments is a different edit. And how many shots deliver the
IDENTICAL picture by `dynamic_geometry.visual_key`, which is the honest measure
of how much of the edit actually changed: replacing one game shot moves the face
rung its neighbours get, so the difference is never confined to the shots that
were wrong.

Writes into `<project>/camera_probe/`, never `exports/`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))

DATA = Path(os.environ.get("CLIPFORGE_DATA_DIR") or (_ROOT / "data")) / "clipper"

#: Two boundaries this far apart are the same cut. Plans are written with three
#: decimals and 40 ms is a frame and a half at 60 fps — below what a viewer can
#: attribute to anything.
SAME_CUT_S = 0.04


def _bounds(plan: dict) -> list[float]:
    return [round(float(s["t0"]), 3) for s in (plan.get("shots") or [])][1:]


def _keys(plan: dict) -> list:
    from services.clipper import dynamic_geometry as dg

    style = plan.get("style") or {}
    sw, sh = int(plan["src_w"]), int(plan["src_h"])
    return [dg.visual_key(s, style, sw, sh) for s in plan.get("shots") or []]


async def _plans(project_id: str, clip_id: str):
    from database import init_db
    from workers.clipper_render_plan import _dynamic_plan, _load

    await init_db()
    clip, project = await _load(clip_id)
    w, h = int(project.width or 1920), int(project.height or 1080)
    with_second = await _dynamic_plan(clip, project, w, h,
                                      no_second_camera=False)
    without = await _dynamic_plan(clip, project, w, h, no_second_camera=True)
    return clip, project, with_second, without


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project")
    ap.add_argument("clips", nargs="+")
    args = ap.parse_args()

    from services.clipper import dynamic_render, layout_policy as lp
    from workers.clipper_render_plan import _source_path

    out_dir = DATA / args.project / "camera_probe"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    refused = 0

    for clip_id in args.clips:
        clip, project, a, b = asyncio.run(_plans(args.project, clip_id))
        if not (a and b and a.get("shots") and b.get("shots")):
            print(f"{clip_id}: REFUSED — one of the two plans has no shots")
            refused += 1
            continue
        ba, bb = _bounds(a), _bounds(b)
        kept = sum(1 for t in ba
                   if any(abs(t - u) <= SAME_CUT_S for u in bb))
        ka, kb = _keys(a), _keys(b)
        same = sum(1 for x, y in zip(ka, kb) if x is not None and x == y)
        oa, ob = lp.off_subject_seconds(a), lp.off_subject_seconds(b)

        src = _source_path(project)
        start = float(clip.start_time or 0.0)
        files: dict[str, str] = {}
        for name, plan in (("with-second-camera", a),
                           ("no-second-camera", b)):
            path = out_dir / f"{clip_id}.{name}.mp4"
            print(f"{clip_id}: rendering {name} "
                  f"({len(plan['shots'])} shots) ...", flush=True)
            dynamic_render.render_dynamic_clip(
                str(src), plan, str(path), start=start, work_dir=str(out_dir),
                ass_path=None,
                src_w=int(project.width or 1920),
                src_h=int(project.height or 1080))
            files[name] = str(path.resolve())

        da = [float(s["t1"]) - float(s["t0"]) for s in a["shots"]]
        db = [float(s["t1"]) - float(s["t0"]) for s in b["shots"]]
        row = {
            "clip": clip_id, "files": files,
            "boundaries": {"with": len(ba), "without": len(bb),
                           "kept_within_40ms": kept, "moved": len(ba) - kept},
            "median_shot_s": [round(statistics.median(da), 2),
                              round(statistics.median(db), 2)],
            "identical_pictures": {"same": same,
                                   "of": min(len(ka), len(kb))},
            "off_subject_s": [oa["seconds"], ob["seconds"]],
            # NOT a recommendation. Whether the replacement shot is the right
            # one is what the files are for.
            "verdict": None,
        }
        rows.append(row)
        print(f"  boundaries {len(ba)} -> {len(bb)}, {kept} kept within "
              f"{SAME_CUT_S * 1000:.0f}ms, {len(ba) - kept} moved")
        print(f"  median shot {row['median_shot_s'][0]}s -> "
              f"{row['median_shot_s'][1]}s")
        print(f"  shots delivering the IDENTICAL picture: {same} of "
              f"{min(len(ka), len(kb))} — the rest changed, because replacing a "
              f"game shot moves the rung its neighbours get")
        print(f"  off-subject {oa['seconds']}s -> {ob['seconds']}s\n")

    (out_dir / "probe.json").write_text(
        json.dumps({"project": args.project, "rows": rows, "verdict": None},
                   indent=1, default=str), encoding="utf-8")
    print(f"{len(rows)} clip(s) rendered both ways into {out_dir}")
    if refused or not rows:
        print(f"REFUSED {refused} of {len(args.clips)}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
