"""The junctions a human timestamped, cut hard and eased, side by side.

    python scripts/render_junction_variants.py pilot6b38 46921099031e

WHAT THIS IS FOR. On 31 August a person watched twenty pilot clips and, on the
one where he gave exact times, every moment he called broken was a `crop<->fit`
composition change — four of them, all four inside his four windows, nothing
else. `absorb_brief_fit_islands` removes the brief ones and touches NONE of
these: they belong to `fit` runs of 7.5s and longer, which have earned their
place. The junction is a 3.16x change in apparent size by the geometry of 16:9,
so what is left is a presentation question nobody has answered.

Codex's condition before the corpus is re-rendered: demonstrate two or three
presentations on exactly those windows and get the verdict, rather than
implementing one generally and finding out afterwards.

SO IT RENDERS THROUGH THE REAL PATH. `render_dynamic_clip` with `ease_s` set,
not a hand-built filtergraph beside it — a demonstration that avoids the
shipping code proves nothing about the shipping code, which is the mistake the
first blind-review session made when it served previews instead of exports.

WHAT IT WRITES, and where: short mp4s under `<project>/junction_variants/`,
which is NOT `exports/`. Six places in this repo read `exports/*.json` as a
clip's sidecar, and one stray cache file already took the R0 gate down once.
Nothing here touches the clip's own export, sidecar or database row.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))

DATA = Path(os.environ.get("CLIPFORGE_DATA_DIR") or (_ROOT / "data")) / "clipper"

#: Seconds either side of a junction. Long enough to see what the shot was and
#: what it becomes, short enough that a person can watch eight of them.
REACH_S = 2.5

#: The treatments to compare. `0.0` is what ships today and is included on
#: purpose: a comparison without the current behaviour in it is a choice between
#: two things nobody asked about.
TREATMENTS: tuple[tuple[str, float], ...] = (
    ("hard-cut", 0.0),
    ("ease-0.30", 0.30),
    ("ease-0.60", 0.60),
)


def _junctions(plan: dict) -> list[float]:
    shots = [s for s in (plan.get("shots") or []) if isinstance(s, dict)]
    out = []
    for a, b in zip(shots, shots[1:]):
        if a.get("composition") != b.get("composition"):
            try:
                out.append(float(b["t0"]))
            except (KeyError, TypeError, ValueError):
                continue
    return out


async def _plan_for(project_id: str, clip_id: str):
    from database import init_db
    from services.clipper import storage
    from workers.clipper_render_plan import _decide_render, _load

    await init_db()
    clip, project = await _load(clip_id)
    paths = storage.paths(project_id)
    decision = await _decide_render(clip, project, paths["exports_dir"])
    return clip, project, decision


def _trim(src: Path, dst: Path, start: float, seconds: float) -> None:
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{start:.3f}",
         "-i", str(src), "-t", f"{seconds:.3f}",
         "-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
         "-pix_fmt", "yuv420p", "-an", str(dst), "-y"],
        check=True, capture_output=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project")
    ap.add_argument("clip")
    ap.add_argument("--reach", type=float, default=REACH_S)
    args = ap.parse_args()

    from services.clipper import dynamic_render
    from workers.clipper_render_plan import _source_path

    clip, project, decision = asyncio.run(_plan_for(args.project, args.clip))
    plan = decision.get("dyn") or {}
    junctions = _junctions(plan)
    if not junctions:
        print("FAIL: this clip's re-plan has no composition change to show")
        return 1

    out_dir = DATA / args.project / "junction_variants"
    out_dir.mkdir(parents=True, exist_ok=True)
    src = _source_path(project)
    start = float(clip.start_time or 0.0)
    made: list[dict] = []

    for name, ease in TREATMENTS:
        whole = out_dir / f"{args.clip}.{name}.mp4"
        print(f"rendering {name} ...", flush=True)
        dynamic_render.render_dynamic_clip(
            str(src), plan, str(whole), start=start, work_dir=str(out_dir),
            ass_path=decision.get("ass_path"),
            src_w=int(project.width or 1920), src_h=int(project.height or 1080),
            ease_s=ease)
        for at in junctions:
            piece = out_dir / f"{args.clip}.at{at:06.2f}.{name}.mp4"
            _trim(whole, piece, max(0.0, at - args.reach), args.reach * 2)
            made.append({"junction_s": round(at, 2), "treatment": name,
                         "ease_s": ease, "file": str(piece.resolve())})

    index = out_dir / f"{args.clip}.variants.json"
    index.write_text(json.dumps({
        "project": args.project, "clip": args.clip,
        "junctions": [round(j, 2) for j in junctions],
        "treatments": [t[0] for t in TREATMENTS],
        "reach_s": args.reach,
        # NOT a recommendation. Which of these is better is the question being
        # asked, and a script that answered it would be answering for the person
        # it is asking.
        "verdict": None,
        "pieces": made,
    }, indent=1), encoding="utf-8")

    print(f"\n{len(made)} clips over {len(junctions)} junctions "
          f"x {len(TREATMENTS)} treatments")
    print(f"index: {index}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
