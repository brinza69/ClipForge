"""Render clipper candidates in the dynamic multi-cam style.

Reads what the AI Stream Clipper analysis already wrote under
`data/clipper/<project>/` — no re-download, no re-transcription — plans a shot
list with `services.clipper.dynamic_edit`, and renders it in one encode with
`services.clipper.dynamic_render`.

    python scripts/render_dynamic_clip.py <project_id> --top 3
    python scripts/render_dynamic_clip.py <project_id> --rank 0 --preview

`--preview` renders 540x960 at crf 28 (seconds instead of minutes) and is what
you want while tuning the style; drop it for the real 1080x1920 export.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Derived from this file's location, never hardcoded: the repo has already been
# checked out on at least two machines under different drive letters, and an
# absolute path here fails with "no such clipper project" pointing at a drive
# that does not exist. CLIPFORGE_DATA_DIR overrides for a split data volume.
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "server"))

from services.caption_overlays import build_overlays_ass          # noqa: E402
from services.clipper import captions as clip_captions            # noqa: E402
from services.clipper import dynamic_edit, dynamic_render         # noqa: E402
from services.clipper.ffmpeg_tools import video_info              # noqa: E402

DATA = Path(os.environ.get("CLIPFORGE_DATA_DIR") or (_REPO / "data")) / "clipper"

# The per-window analysis moved into the service layer when the pipeline needed
# it too; this script drives the same code the export path runs.
from services.clipper.dynamic_cameras import ACTION_BAND          # noqa: E402
from services.clipper.dynamic_window import (                     # noqa: E402
    FACE_HOP_S, analyse_window,
)


def load(project: Path, name: str) -> dict | list:
    path = project / "analysis" / f"{name}.json"
    if not path.exists():
        raise SystemExit(f"missing artefact: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def build_ass(cand: dict, out_dir: Path, stem: str, *, preset: str,
              max_words: int, position: str, out_w: int, out_h: int,
              entry_pop: bool = False) -> str | None:
    """Word-highlight captions for the clip, or None when there is no speech."""
    plan = clip_captions.build_caption_plan(
        cand, {}, preset_id=preset, max_words=max_words, position=position,
        layout={"safe_zones": {}, "out_w": out_w, "out_h": out_h},
        entry_pop=entry_pop)
    overlays = clip_captions.caption_plan_to_overlays(plan)
    if not overlays:
        return None
    ass = out_dir / f"{stem}.ass"
    build_overlays_ass(overlays, out_w, out_h, str(ass))
    return str(ass)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project")
    ap.add_argument("--top", type=int, default=1, help="render the N best candidates")
    ap.add_argument("--rank", type=int, help="render one specific rank (0 = best)")
    ap.add_argument("--preview", action="store_true", help="540x960 draft render")
    ap.add_argument("--out", default=None, help="output directory")
    ap.add_argument("--preset", default="bold_impact", help="caption preset id")
    ap.add_argument("--max-words", type=int, default=2)
    ap.add_argument("--caption-pos", default="center",
                    help="bottom | center | hook — the references sit mid-frame")
    ap.add_argument("--caption-pop", action="store_true",
                    help="scale overshoot as each card lands (measured on _LQ379ZhspI)")
    ap.add_argument("--fixed-band", dest="auto_band", action="store_false",
                    default=True,
                    help="use the hardcoded ACTION_BAND instead of deriving it "
                         "from the detected facecam")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--style", default=None, help="JSON overrides for DEFAULT_STYLE")
    args = ap.parse_args()

    project = DATA / args.project
    if not project.exists():
        raise SystemExit(f"no such clipper project: {project}")

    source = project / "source" / "source.mp4"
    proxy = project / "proxy" / "proxy.mp4"
    for path in (source, proxy):
        if not path.exists():
            raise SystemExit(f"missing media: {path}")

    signals = load(project, "signals")
    meta = load(project, "meta")
    candidates = sorted(load(project, "candidates"),
                        key=lambda c: -float(c.get("overall") or 0.0))

    info = (meta.get("source") if isinstance(meta, dict) else {}) or {}
    src_w = int(info.get("width") or video_info(str(source))["width"])
    src_h = int(info.get("height") or video_info(str(source))["height"])

    style = dict(dynamic_edit.DEFAULT_STYLE)
    if args.style:
        style.update(json.loads(args.style))

    out_w, out_h = (540, 960) if args.preview else (1080, 1920)
    crf, preset = (28, "veryfast") if args.preview else (18, "medium")
    out_dir = Path(args.out) if args.out else project / "dynamic"
    out_dir.mkdir(parents=True, exist_ok=True)

    ranks = [args.rank] if args.rank is not None else list(range(args.top))
    for rank in ranks:
        if not 0 <= rank < len(candidates):
            print(f"[skip] rank {rank} is out of range (0..{len(candidates) - 1})")
            continue
        cand = candidates[rank]
        start = float(cand["start"])
        duration = float(cand["end"]) - start
        stem = f"r{rank:02d}_{int(start)}s"
        print(f"\n=== rank {rank}  score {cand.get('overall')}  "
              f"{start:.1f}s +{duration:.1f}s ===")
        print(f"    {(cand.get('text') or '')[:110]}")

        t0 = time.time()
        win = analyse_window(
            proxy, start, duration, None if args.auto_band else ACTION_BAND, src_w)
        band = win["band"]
        print(f"    banda de actiune x {band[0]:.2f}-{band[1]:.2f}  "
              f"y {band[2]:.2f}-{band[3]:.2f}"
              + ("  (derivata din facecam)" if args.auto_band else "  (constanta)"))
        seen = sum(1 for f in win["faces"] if f["boxes"])
        print(f"    faces {seen}/{len(win['faces'])} samples, "
              f"{len(win['motion'])} motion samples  ({time.time() - t0:.1f}s)")

        plan = dynamic_edit.plan_dynamic_edit(
            cand, signals, win["faces"], src_w=src_w, src_h=src_h,
            proxy_w=int(signals.get("proxy_width") or 0),
            proxy_h=int(signals.get("proxy_height") or 0),
            game_motion=win["motion"], game_focus=win["focus"],
            game_detail=win["detail"], game_ui=win["ui"],
            game_motion_hop=win["hop"], style=style)
        shots = plan["shots"]
        # lowercase -> uppercase reads widest -> tightest.
        letters = {"face": "f", "face_medium": "m", "face_tight": "F",
                   "game": "g", "game_tight": "G"}
        print(f"    {len(shots)} shots, {len(plan['hits'])} hits  "
              f"(avg {duration / max(1, len(shots)):.2f}s)  "
              f"[{''.join(letters.get(s['camera'], '?') for s in shots)}]")
        print(f"    facecam @ {plan['subject']['face']} ")
        for warning in plan["warnings"]:
            print(f"    ! {warning}")

        ass = build_ass(cand, out_dir, stem, preset=args.preset,
                        max_words=args.max_words, position=args.caption_pos,
                        out_w=out_w, out_h=out_h, entry_pop=args.caption_pop)

        (out_dir / f"{stem}.plan.json").write_text(
            json.dumps(plan, indent=1), encoding="utf-8")

        out = out_dir / f"{stem}.mp4"
        t0 = time.time()
        result = dynamic_render.render_dynamic_clip(
            str(source), plan, str(out), start=start, work_dir=out_dir,
            ass_path=ass, src_w=src_w, src_h=src_h, fps=args.fps, crf=crf,
            preset=preset, out_w=out_w, out_h=out_h)
        print(f"    -> {out}  {result['size'] / 1e6:.1f} MB  "
              f"({time.time() - t0:.1f}s)")


if __name__ == "__main__":
    main()
