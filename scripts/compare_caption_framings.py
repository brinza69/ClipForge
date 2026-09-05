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

THREE THINGS ITS OUTPUT MAY NOT BE READ AS, and all three were read that way in
the first report off this script.

"as-shipped holds the text in 13 of 58 shots" means it holds the text IN THE
FRAMES THAT WERE READ — two per shot. It does not mean it preserves every
subtitle in those shots, and it cannot: two frames is not temporal coverage.

"region holds it in 58 of 58" is CONDITIONAL and close to circular. The region
is the union of that shot's own observed boxes, so it contains them by
construction. It demonstrates geometric feasibility for the observations used,
not behaviour on frames nobody looked at — `caption_region.verify_frozen` is
the non-circular version, and `--holdout` runs it here.

And every FACE size is `dynamic_plan.subject.face` — the clip-wide average —
projected through a scale. The text became local in this batch; the subject did
not. These are not measurements of the face visible in any given frame.

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


def _sample_times(shots: list[dict], start: float, *, count: int,
                  offset: float = 0.0) -> list[float]:
    """Times on the SOURCE clock: the shots' own times plus the clip's start.

    Evenly inside each shot rather than at its edges — a sample taken exactly on
    a boundary belongs to neither shot, and the decoder's rounding decides which
    one it lands in.

    `offset` shifts the whole comb by a fraction of a slot, which is how the
    held-out frames are guaranteed to be different frames from the ones that
    built the region. Reusing a construction frame as a test frame is the
    circularity this option exists to escape, and it would be invisible.
    """
    out: list[float] = []
    for shot in shots:
        try:
            t0, t1 = float(shot["t0"]), float(shot["t1"])
        except (KeyError, TypeError, ValueError):
            continue
        for i in range(1, count + 1):
            out.append(start + t0 + (t1 - t0) * (i + offset) / (count + 1))
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


def _holdout(shots: list[dict], plan: dict, obs: Any, start: float,
             rows: list[dict]) -> list[dict]:
    """Test each shot's FROZEN region against frames it never saw.

    The regions are taken from `rows` exactly as they were computed — not
    recomputed over the held-out boxes, which would rebuild the circularity one
    level down and look like a stronger result.
    """
    from services.clipper import caption_region as cr
    from services.clipper import source_caption_observation as sco

    sw, sh = int(plan.get("src_w") or 0), int(plan.get("src_h") or 0)
    by_index = {r["shot"]: r for r in rows}
    out: list[dict] = []
    for shot in shots:
        row = by_index.get(shot.get("index"))
        frozen = ((row or {}).get("framings", {}).get("region") or {}).get("rect")
        cover = sco.coverage(obs, start + float(shot["t0"]),
                             start + float(shot["t1"]))
        got = cr.verify_frozen(frozen, cover["boxes"], sw, sh)
        out.append({"shot": shot.get("index"),
                    "held_out_samples": cover["samples"],
                    "with_text": cover["with_text"], **got})
    return out


def _print_holdout(clip_id: str, held: list[dict]) -> None:
    """The non-circular figure, with its own denominator.

    A shot whose hold-out frames carried no text is `no_held_out_observation`,
    and it is counted apart from the shots the region actually held: folding it
    in would turn "nobody looked" into "the region worked".
    """
    testable = [h for h in held if h["held_out"]]
    clipped = [h for h in testable if h["clipped"]]
    print(f"  HELD-OUT: {len(testable)} of {len(held)} shots had text in frames "
          f"that took no part in building their region")
    if not testable:
        print("  HELD-OUT: nothing was tested, which is not a pass")
        return
    boxes = sum(h["held_out"] for h in testable)
    lost = sum(h["clipped"] for h in testable)
    worst = max((h["worst_overflow_px"] or 0.0) for h in testable)
    print(f"  HELD-OUT: the frozen regions hold {boxes - lost} of {boxes} "
          f"unseen boxes across those shots; {len(clipped)} shots clip at least "
          f"one, worst overflow {worst:.0f} source px")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project")
    ap.add_argument("clips", nargs="+")
    ap.add_argument("--holdout", type=int, default=0, metavar="N",
                    help="also read N frames per shot that took NO part in "
                         "building the regions, and test the frozen regions "
                         "against them. Without this the region's "
                         "'contains the text' is circular.")
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
        times = _sample_times(shots, start, count=PER_SHOT)
        print(f"{clip_id}: reading {len(times)} frames over {len(shots)} shots "
              f"...", flush=True)
        obs = sco.observe(str(proxy), times)
        if obs["why"]:
            print(f"{clip_id}: REFUSED — {obs['why']}")
            refused += 1
            continue

        rows = [_row(s, plan, obs, start) for s in shots]
        held: Any = None
        if args.holdout:
            # DIFFERENT FRAMES, guaranteed by the phase shift rather than hoped
            # for. These take no part in building any region; they only test
            # the frozen ones.
            later = _sample_times(shots, start, count=args.holdout, offset=0.5)
            print(f"{clip_id}: reading {len(later)} HELD-OUT frames ...",
                  flush=True)
            obs2 = sco.observe(str(proxy), later)
            if obs2["why"]:
                print(f"{clip_id}: REFUSED the hold-out — {obs2['why']}")
                refused += 1
                continue
            held = _holdout(shots, plan, obs2, start, rows)
        report.append({"clip": clip_id, "shots": rows, "holdout": held,
                       "observation": {k: obs[k] for k in
                                       ("image_w", "image_h", "read",
                                        "refused", "refusals")},
                       # NOT a recommendation, for the same reason the video
                       # probe carries none: which framing is right is what is
                       # being asked, not what is being answered.
                       "verdict": None})
        _print(clip_id, rows)
        if held is not None:
            _print_holdout(clip_id, held)

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
    frames = sum(r["samples"] for r in rows)
    print(f"  over {frames} frames read across {len(rows)} shots — "
          f"{PER_SHOT} per shot, which is not temporal coverage")
    for name in ("as-shipped", "full-fit", "region"):
        held = sum(1 for r in with_text
                   if (r["framings"].get(name) or {}).get("contains_text") is True)
        unknown = sum(1 for r in with_text
                      if (r["framings"].get(name) or {}).get("contains_text") is None)
        note = ("  [CONDITIONAL: built from these same boxes]"
                if name == "region" else "")
        print(f"  {name:<11} holds the text READ in {held} of "
              f"{len(with_text)} shots with text ({unknown} unanswerable){note}")

    # THE JUMP IS BETWEEN NEIGHBOURS, not between the extremes of the clip.
    # "535..1056, a 2x jump at a cut" put a range and a claim about adjacency in
    # one sentence, and only the range was measured.
    for name in ("as-shipped", "region"):
        ratios = []
        for a, b in zip(rows, rows[1:]):
            fa = (a["framings"].get(name) or {}).get("subject_w_out_px")
            fb = (b["framings"].get(name) or {}).get("subject_w_out_px")
            if fa and fb:
                ratios.append(max(fa, fb) / min(fa, fb))
        if not ratios:
            print(f"  {name:<11} no adjacent pair could be compared")
            continue
        big = sum(1 for r in ratios if r >= 1.5)
        print(f"  {name:<11} adjacent-shot size ratio: median "
              f"{sorted(ratios)[len(ratios) // 2]:.2f}, worst {max(ratios):.2f}, "
              f"{big} of {len(ratios)} pairs at 1.5x or more")


def _n(value: Any) -> str:
    return "-" if value is None else f"{value:.0f}"


if __name__ == "__main__":
    raise SystemExit(main())
