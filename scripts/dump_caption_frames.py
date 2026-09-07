"""The observed frames, with their boxes drawn and numbered, as contact sheets.

    python scripts/dump_caption_frames.py pilotf81b 6053a598cf06 --per-shot 2

WHAT IT IS FOR. §A's confirmation is a person's or an agent's, and it cannot be
made from a JSON file of coordinates: deciding that a box is the source's
subtitle rather than a watermark, a diagram label or a piece of interface means
LOOKING at it. This renders exactly the frames `source_caption_observation` read,
draws each box with its index, and stamps the time — so a label written
afterwards points at a box somebody actually saw.

THE INDICES ARE THE CONTRACT. `caption_labels` targets a box by its position in
that sample's list, so the number drawn on the frame IS the index a label must
use. Drawing them in a different order, or renumbering per sheet, would produce
labels that attach to the wrong boxes and nothing downstream could tell.

THE TIME DRAWN IS THE DECODED ONE, for the same reason: `cv2` seeks
approximately, `caption_labels` matches on `t_decoded`, and a sheet stamped with
the requested time would invite labels that match no sample at all.

It writes into `<project>/caption_frames/` and touches nothing else.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))

DATA = Path(os.environ.get("CLIPFORGE_DATA_DIR") or (_ROOT / "data")) / "clipper"

#: Tiles per contact sheet. Twelve at 480x270 is a 1920x810 image — big enough
#: to read a subtitle line, small enough to take in at once.
COLS, ROWS = 4, 3


async def _plan_for(project_id: str, clip_id: str):
    from database import init_db
    from services.clipper import storage
    from workers.clipper_render_plan import _decide_render, _load

    await init_db()
    clip, project = await _load(clip_id)
    paths = storage.paths(project_id)
    return clip, project, await _decide_render(clip, project,
                                               paths["exports_dir"])


def _times(shots: list[dict], start: float, per_shot: int) -> list[float]:
    out: list[float] = []
    for shot in shots:
        try:
            t0, t1 = float(shot["t0"]), float(shot["t1"])
        except (KeyError, TypeError, ValueError):
            continue
        for i in range(1, per_shot + 1):
            out.append(start + t0 + (t1 - t0) * i / (per_shot + 1))
    return out


def _observe_frames(video: str, frames: list[int]) -> dict:
    """`source_caption_observation.observe`, addressed by frame index.

    The same record in the same shape — the detector, the boxes as fractions,
    the refusals — but seeking by `POS_FRAMES` so a hold-out set is exactly the
    frames it was chosen to be. `observe` seeks by time, and a hold-out picked
    to avoid the construction frames can land back on them.
    """
    import cv2

    from services.clipper import source_captions as scap
    from services.clipper import source_caption_observation as sco

    engine = scap._reader()
    if engine is None:
        return sco._empty(video, sco.NO_DETECTOR)
    cap = cv2.VideoCapture(video)
    try:
        if not cap.isOpened():
            return sco._empty(video, sco.NO_VIDEO)
        out = sco._empty(video, "")
        out["why"] = None
        samples = []
        for index in frames:
            cap.set(cv2.CAP_PROP_POS_FRAMES, index)
            got_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
            at = cap.get(cv2.CAP_PROP_POS_FRAMES)
            ok, frame = cap.read()
            if not ok or frame is None:
                samples.append({"t_requested": None, "t_decoded": None,
                                "frame": index, "boxes": None,
                                "refused": sco.FRAME_UNREADABLE})
                continue
            h, w = int(frame.shape[0]), int(frame.shape[1])
            if out["image_w"] is None:
                out["image_w"], out["image_h"] = w, h
            boxes = sco._boxes(frame, engine, w, h)
            samples.append({
                "t_requested": None,
                "t_decoded": round(float(got_ms) / 1000.0, 3),
                "frame": int(at), "boxes": boxes,
                "refused": None if boxes is not None else sco.DETECTOR_FAILED})
        out["samples"] = samples
        out["read"] = sum(1 for s in samples if s["boxes"] is not None)
        out["refused"] = len(samples) - out["read"]
        out["refusals"] = sorted({s["refused"] for s in samples if s["refused"]})
        return out
    finally:
        cap.release()


def _draw(frame: Any, sample: dict, shot: Any, cv2: Any) -> Any:
    """Boxes numbered by their INDEX in the sample, plus the decoded time."""
    height, width = frame.shape[0], frame.shape[1]
    for i, box in enumerate(sample.get("boxes") or []):
        x0, x1 = int(box["x0"] * width), int(box["x1"] * width)
        y0, y1 = int(box["y0"] * height), int(box["y1"] * height)
        cv2.rectangle(frame, (x0, y0), (x1, y1), (0, 255, 0), 1)
        cv2.putText(frame, str(i), (x0, max(10, y0 - 2)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)
    cv2.putText(frame, f"shot {shot}  t={sample.get('t_decoded')}",
                (4, height - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                (255, 255, 255), 1)
    return frame


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project")
    ap.add_argument("clips", nargs="+")
    ap.add_argument("--per-shot", type=int, default=2)
    ap.add_argument("--frames", default="",
                    help="comma-separated FRAME indices instead of per-shot "
                         "sampling. This is how a HOLD-OUT set is observed: the "
                         "frames are chosen to be disjoint from the "
                         "construction set and addressed by index, because "
                         "seeking by time lands on whatever the decoder gives")
    ap.add_argument("--suffix", default="",
                    help="write to <clip><suffix>.samples.json instead of "
                         "overwriting the construction observation")
    args = ap.parse_args()

    import cv2

    from services.clipper import source_caption_observation as sco

    proxy = DATA / args.project / "proxy" / "proxy.mp4"
    if not proxy.exists():
        print(f"REFUSED: no proxy at {proxy}")
        return 2
    out_dir = DATA / args.project / "caption_frames"
    out_dir.mkdir(parents=True, exist_ok=True)
    made: list[str] = []

    for clip_id in args.clips:
        clip, project, decision = asyncio.run(_plan_for(args.project, clip_id))
        plan = decision.get("dyn") or {}
        shots = [s for s in (plan.get("shots") or []) if isinstance(s, dict)]
        if not shots:
            print(f"{clip_id}: REFUSED — no shots")
            return 2
        start = float(clip.start_time or 0.0)
        if args.frames:
            try:
                want = [int(x) for x in args.frames.split(",") if x.strip()]
            except ValueError:
                print("REFUSED: --frames is not a list of integers")
                return 2
            print(f"{clip_id}: reading {len(want)} frames by index ...",
                  flush=True)
            obs = _observe_frames(str(proxy), want)
        else:
            times = _times(shots, start, args.per_shot)
            print(f"{clip_id}: reading {len(times)} frames ...", flush=True)
            obs = sco.observe(str(proxy), times)
        if obs["why"]:
            print(f"{clip_id}: REFUSED — {obs['why']}")
            return 2

        # Which shot each sample belongs to, so the tile can say so.
        owner: list[Any] = []
        for shot in shots:
            owner.extend([shot.get("index")] * args.per_shot)

        cap = cv2.VideoCapture(str(proxy))
        tiles: list[Any] = []
        try:
            for sample, shot in zip(obs["samples"], owner):
                if sample.get("frame") is None:
                    continue
                cap.set(cv2.CAP_PROP_POS_FRAMES, sample["frame"])
                ok, frame = cap.read()
                if not ok or frame is None:
                    # A tile that could not be drawn is skipped and COUNTED;
                    # a sheet silently one frame short would renumber nothing
                    # but would hide that a sample was never looked at.
                    print(f"  frame {sample['frame']} could not be re-read")
                    continue
                tiles.append(_draw(frame, sample, shot, cv2))
        finally:
            cap.release()

        per = COLS * ROWS
        for page in range((len(tiles) + per - 1) // per):
            chunk = tiles[page * per:(page + 1) * per]
            rows = []
            for r in range(0, len(chunk), COLS):
                row = chunk[r:r + COLS]
                while len(row) < COLS:
                    import numpy as np

                    row.append(np.zeros_like(chunk[0]))
                rows.append(cv2.hconcat(row))
            sheet = cv2.vconcat(rows)
            path = out_dir / f"{clip_id}{args.suffix}.sheet{page:02d}.jpg"
            cv2.imwrite(str(path), sheet, [cv2.IMWRITE_JPEG_QUALITY, 88])
            made.append(str(path))
            print(f"  {path.name}: {len(chunk)} frames")

        (out_dir / f"{clip_id}{args.suffix}.samples.json").write_text(
            json.dumps({"clip": clip_id, "start": start,
                        "shot_of_sample": owner, "observation": obs},
                       indent=1, default=str), encoding="utf-8")

    print(f"\n{len(made)} sheet(s) in {out_dir}")
    return 0 if made else 2


if __name__ == "__main__":
    raise SystemExit(main())
