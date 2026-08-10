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
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, r"D:\clipforge\server")

from services.caption_overlays import build_overlays_ass          # noqa: E402
from services.clipper import captions as clip_captions            # noqa: E402
from services.clipper import dynamic_edit, dynamic_render         # noqa: E402
from services.clipper.ffmpeg_tools import ffmpeg_bin, video_info  # noqa: E402
from services.clipper.signals import face_presence                # noqa: E402

DATA = Path(r"D:\clipforge\data\clipper")
FACE_HOP_S = 0.25

# The slice of the frame that "is something happening in the game" is measured
# over: right of the bottom-left facecam, left of the chat strip. Fractions of
# width/height as (x0, x1, y0, y1).
ACTION_BAND = (0.34, 0.86, 0.04, 0.94)


def load(project: Path, name: str) -> dict | list:
    path = project / "analysis" / f"{name}.json"
    if not path.exists():
        raise SystemExit(f"missing artefact: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


MOTION_COLS = 8


def region_motion(window: Path, hop: float, band: tuple[float, float, float, float],
                  src_w: int) -> tuple[list[float], list[float]]:
    """(how much is happening in the game, where it is happening) per `hop`.

    Two things the whole-frame motion signal in signals.json cannot give us.
    It cannot answer "is something happening in the GAME", because the facecam
    is in the same frame and he moves constantly — hence the band. And it has no
    spatial component at all, so a gameplay camera built on it points at a fixed
    rectangle and spends half the clip framing an empty wall. Splitting the band
    into columns and returning the busiest one turns the second camera into
    something that actually follows the action.

    The focus value is an x centre in SOURCE pixels, ready to hand to
    `dynamic_edit`; `-1.0` means "nothing moved, no opinion".
    """
    import cv2
    import numpy as np

    cap = cv2.VideoCapture(str(window))
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) or 10.0
        step = max(1, int(round(fps * hop)))
        band_x0, band_x1 = src_w * band[0], src_w * band[1]
        col_w = (band_x1 - band_x0) / MOTION_COLS

        totals: list[float] = []
        focus: list[float] = []
        previous = None
        index = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if index % step == 0:
                h, w = frame.shape[:2]
                crop = frame[int(h * band[2]):int(h * band[3]),
                             int(w * band[0]):int(w * band[1])]
                grey = cv2.resize(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY),
                                  (MOTION_COLS * 12, 54),
                                  interpolation=cv2.INTER_AREA).astype(np.float32)
                if previous is None:
                    totals.append(0.0)
                    focus.append(-1.0)
                else:
                    diff = np.abs(grey - previous)
                    totals.append(float(diff.mean()))
                    per_col = diff.reshape(54, MOTION_COLS, 12).mean(axis=(0, 2))
                    hottest = int(per_col.argmax())
                    focus.append(-1.0 if per_col.max() <= 0.0
                                 else band_x0 + (hottest + 0.5) * col_w)
                previous = grey
            index += 1
        return totals, focus
    finally:
        cap.release()


def analyse_window(proxy: Path, start: float, duration: float,
                   band: tuple[float, float, float, float], src_w: int
                   ) -> tuple[list[dict], list[float], list[float]]:
    """(dense face track in proxy pixels, gameplay-band motion) for one window.

    The whole-VOD face track in signals.json samples roughly every 11 seconds —
    three boxes inside a 30-second clip, nowhere near enough to anchor 20 shots.
    Cutting the window out of the proxy first turns hundreds of random seeks on
    a 6-hour file into one sequential read, and both measurements share it.
    """
    work = Path(tempfile.mkdtemp(prefix="dynwin_"))
    try:
        window = work / "window.mp4"
        subprocess.run(
            [ffmpeg_bin(), "-y", "-loglevel", "error",
             "-ss", f"{start:.3f}", "-i", str(proxy), "-t", f"{duration:.3f}",
             "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "24",
             str(window)],
            check=True, capture_output=True, timeout=600)

        times = [i * FACE_HOP_S for i in range(int(duration / FACE_HOP_S) + 1)]
        samples = face_presence(str(window), times)
        # Re-base onto the source clock: dynamic_edit subtracts cand["start"].
        faces = [{"t": float(s.get("t", 0.0)) + start, "boxes": s.get("boxes") or []}
                 for s in samples]
        totals, focus = region_motion(window, FACE_HOP_S, band, src_w)
        return faces, totals, focus
    finally:
        shutil.rmtree(work, ignore_errors=True)


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
        faces, motion, focus = analyse_window(proxy, start, duration,
                                              ACTION_BAND, src_w)
        seen = sum(1 for f in faces if f["boxes"])
        print(f"    faces {seen}/{len(faces)} samples, "
              f"{len(motion)} motion samples  ({time.time() - t0:.1f}s)")

        plan = dynamic_edit.plan_dynamic_edit(
            cand, signals, faces, src_w=src_w, src_h=src_h,
            proxy_w=int(signals.get("proxy_width") or 0),
            proxy_h=int(signals.get("proxy_height") or 0),
            game_motion=motion, game_focus=focus, game_motion_hop=FACE_HOP_S,
            style=style)
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
