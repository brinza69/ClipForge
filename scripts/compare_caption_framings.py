"""Three framings per shot, side by side, without encoding anything.

    python scripts/compare_caption_framings.py pilotf81b 6053a598cf06 b23c14c41495

WHAT IT COMPARES, per shot of the clip's own re-plan:

    as-shipped   the delivered 9:16 crop, from `evidence_map.crop_window`
    full-fit     the whole source letterboxed, which is what was rendered
    region       the rectangle holding the subject and the subtitle lines
                 ACTUALLY OBSERVED in that shot, letterboxed

and for each, two numbers a person can act on: how wide the speaker's face lands
in the 1080x1920 output, and how tall the caption band lands. Plus whether the
framing contains the observed text at all — which is the question the cumulative
band could only warn about.

WHY IT SAMPLES PER SHOT AND NOT PER LINE. The region has to be constant across
the interval it applies to. Recomputing it as each caption appears would resize
the picture on every line, which is the defect the whole exercise exists to
avoid: `absorb_brief_fit_islands` is there because brief composition changes
read as broken, and a zoom that breathes with the dialogue is that at a higher
frequency.

AND IT ENCODES NOTHING. Codex's order is observation, then proposed framing,
then computed visibility, then the layer, then a render, then verification in
the mp4. This is the middle three; the render waits on a person's verdict about
the two files already produced.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))

DATA = Path(os.environ.get("CLIPFORGE_DATA_DIR") or (_ROOT / "data")) / "clipper"

OUT_W, OUT_H = 1080, 1920
#: Samples per shot. Two is the minimum that can show a line appearing inside
#: one; more costs OCR seconds per frame and this runs over every shot.
PER_SHOT = 2


async def _plan_for(project_id: str, clip_id: str):
    from database import init_db
    from services.clipper import storage
    from workers.clipper_render_plan import _decide_render, _load

    await init_db()
    clip, project = await _load(clip_id)
    paths = storage.paths(project_id)
    decision = await _decide_render(clip, project, paths["exports_dir"])
    return clip, project, decision


def _sample_times(shots: list[dict], start: float) -> list[float]:
    """Times on the SOURCE clock: the shots' own times plus the clip's start.

    Evenly inside each shot rather than at its edges — a sample taken exactly on
    a boundary belongs to neither shot, and the decoder's rounding decides which
    one it lands in.
    """
    out: list[float] = []
    for shot in shots:
        try:
            t0, t1 = float(shot["t0"]), float(shot["t1"])
        except (KeyError, TypeError, ValueError):
            continue
        for i in range(1, PER_SHOT + 1):
            out.append(start + t0 + (t1 - t0) * i / (PER_SHOT + 1))
    return out


def _contains(rect: dict, boxes: list[dict], sw: float, sh: float,
              *, off_y: float = 0.0) -> bool | None:
    """Whether a SOURCE-pixel rectangle holds every observed box. None if the
    question cannot be asked — no boxes is not a containing framing."""
    if not boxes:
        return None
    for box in boxes:
        try:
            x0, x1 = float(box["x0"]) * sw, float(box["x1"]) * sw
            y0, y1 = float(box["y0"]) * sh + off_y, float(box["y1"]) * sh + off_y
        except (KeyError, TypeError, ValueError):
            return None
        if (x0 < rect["x"] - 0.5 or x1 > rect["x"] + rect["w"] + 0.5
                or y0 < rect["y"] - 0.5 or y1 > rect["y"] + rect["h"] + 0.5):
            return False
    return True


def _row(shot: dict, plan: dict, obs: Any, start: float) -> dict:
    from services.clipper import caption_region as cr
    from services.clipper import evidence_map as em
    from services.clipper import source_caption_observation as sco
    from services.clipper.dynamic_geometry import canvas_size

    sw, sh = int(plan.get("src_w") or 0), int(plan.get("src_h") or 0)
    _cw, _ch, off_y = canvas_size(sw, sh)
    face_w = float(((plan.get("subject") or {}).get("face") or {}).get("w") or 0.0)

    cover = sco.coverage(obs, start + float(shot["t0"]), start + float(shot["t1"]))
    boxes = cover["boxes"]
    band_h = 0.0
    if boxes:
        band_h = (max(float(b["y1"]) for b in boxes)
                  - min(float(b["y0"]) for b in boxes)) * sh

    out: dict[str, Any] = {
        "shot": shot.get("index"), "composition": shot.get("composition"),
        "t0": round(float(shot["t0"]), 2), "t1": round(float(shot["t1"]), 2),
        "samples": cover["samples"], "with_text": cover["with_text"],
        "unevidenced": cover["unevidenced"], "band_h_src_px": round(band_h, 1),
        "framings": {},
    }

    crop = em.crop_window(shot, src_w=sw, src_h=sh, style=plan.get("style") or {})
    framings: dict[str, Any] = {}
    if isinstance(crop, str):
        framings["as-shipped"] = {"refused": crop}
    else:
        # The delivered crop is in CANVAS pixels, so the boxes are compared on
        # the canvas too — the pad is applied before the crop.
        rect = {"x": crop[0], "y": crop[1], "w": crop[2], "h": crop[3]}
        framings["as-shipped"] = {
            **cr.delivered_sizes(rect, subject_w=face_w, band_h=band_h),
            "rect": {k: round(v, 1) for k, v in rect.items()},
            "contains_text": _contains(rect, boxes, sw, sh, off_y=off_y)}

    whole = {"x": 0.0, "y": 0.0, "w": float(sw), "h": float(sh)}
    framings["full-fit"] = {
        **cr.delivered_sizes(whole, subject_w=face_w, band_h=band_h),
        "rect": whole, "contains_text": _contains(whole, boxes, sw, sh)}

    region = cr.region_for(boxes, plan.get("subject"), sw, sh)
    if region["rect"] is None:
        framings["region"] = {"refused": region["why"]}
    else:
        framings["region"] = {
            **cr.delivered_sizes(region["rect"], subject_w=face_w,
                                 band_h=band_h),
            "rect": region["rect"], "aspect": region["aspect"],
            "contains_text": _contains(region["rect"], boxes, sw, sh)}
    out["framings"] = framings
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project")
    ap.add_argument("clips", nargs="+")
    args = ap.parse_args()

    from services.clipper import source_caption_observation as sco

    proxy = DATA / args.project / "proxy" / "proxy.mp4"
    if not proxy.exists():
        print(f"REFUSED: no proxy at {proxy}")
        return 2

    out_dir = DATA / args.project / "framing_comparison"
    out_dir.mkdir(parents=True, exist_ok=True)
    report: list[dict] = []
    refused = 0

    for clip_id in args.clips:
        clip, project, decision = asyncio.run(_plan_for(args.project, clip_id))
        plan = decision.get("dyn") or {}
        shots = [s for s in (plan.get("shots") or []) if isinstance(s, dict)]
        if not shots:
            print(f"{clip_id}: REFUSED — the re-plan has no shots")
            refused += 1
            continue
        start = float(clip.start_time or 0.0)
        times = _sample_times(shots, start)
        print(f"{clip_id}: reading {len(times)} frames over {len(shots)} shots "
              f"...", flush=True)
        obs = sco.observe(str(proxy), times)
        if obs["why"]:
            print(f"{clip_id}: REFUSED — {obs['why']}")
            refused += 1
            continue

        rows = [_row(s, plan, obs, start) for s in shots]
        report.append({"clip": clip_id, "shots": rows,
                       "observation": {k: obs[k] for k in
                                       ("image_w", "image_h", "read",
                                        "refused", "refusals")},
                       # NOT a recommendation, for the same reason the video
                       # probe carries none: which framing is right is what is
                       # being asked, not what is being answered.
                       "verdict": None})
        _print(clip_id, rows)

    (out_dir / "framings.json").write_text(
        json.dumps({"project": args.project, "clips": args.clips,
                    "rows": report, "verdict": None}, indent=1, default=str),
        encoding="utf-8")
    print(f"\nwritten to {out_dir / 'framings.json'}")
    if refused or not report:
        # A clip asked for and not compared is a refusal, not a smaller run.
        print(f"REFUSED {refused} of {len(args.clips)}")
        return 2
    return 0


def _print(clip_id: str, rows: list[dict]) -> None:
    with_text = [r for r in rows if r["with_text"]]
    print(f"\n{clip_id}: {len(rows)} shots, {len(with_text)} with observed text, "
          f"{sum(1 for r in rows if r['unevidenced'])} unevidenced")
    print(f"  {'shot':>4} {'comp':<5} {'text':>4}  "
          f"{'as-shipped face/band/holds':<28} "
          f"{'full-fit face/band/holds':<26} region face/band/holds")
    for r in with_text:
        cells = []
        for name in ("as-shipped", "full-fit", "region"):
            f = r["framings"].get(name) or {}
            if "refused" in f:
                cells.append(f"{f['refused'][:22]:<26}")
                continue
            cells.append(f"{_n(f['subject_w_out_px']):>5}/"
                         f"{_n(f['band_h_out_px']):>5}/"
                         f"{str(f['contains_text']):<5} ")
        print(f"  {r['shot']:>4} {str(r['composition']):<5} {r['with_text']:>4}  "
              f"{cells[0]:<28}{cells[1]:<26}{cells[2]}")
    # THE DENOMINATOR BEFORE THE COUNT. "3 framings contain the text" out of an
    # unstated number of shots is the figure this repo keeps catching.
    for name in ("as-shipped", "full-fit", "region"):
        held = sum(1 for r in with_text
                   if (r["framings"].get(name) or {}).get("contains_text") is True)
        unknown = sum(1 for r in with_text
                      if (r["framings"].get(name) or {}).get("contains_text") is None)
        print(f"  {name:<11} holds the observed text in {held} of "
              f"{len(with_text)} shots with text ({unknown} unanswerable)")


def _n(value: Any) -> str:
    return "-" if value is None else f"{value:.0f}"


if __name__ == "__main__":
    raise SystemExit(main())
