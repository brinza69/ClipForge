"""One constant region per PHASE, from confirmed observations only.

    python scripts/build_phase_regions.py pilotf81b 6053a598cf06

WHAT A PHASE IS. Codex's definition, and it is not the planner's: a stretch over
which the VISUAL PRIORITY is the same. `6053a598cf06` is one — a single
continuous view of one person talking. `b23c14c41495` is four: the speech, then
the watch worn and indicated, then its removal and presentation, then the screen
shown to the lens. Three semantic phases do not oblige three framings, so
consecutive phases whose regions are close enough are reported as MERGEABLE and
the cost of merging is printed rather than assumed.

WHAT GOES INTO A REGION. The confirmed full subtitle lines for the phase, from
`apply_line_annotations`, and the confirmed SUBJECT. Nothing else, and in
particular not `dynamic_plan["subject"]["face"]`, which is a clip-wide average
and has no authority over any phase.

AND A PHASE WITHOUT A CONFIRMED SUBJECT IS REFUSED. It does not fall back to the
average, it does not fall back to the previous phase's rectangle, and it does
not quietly become a region built from the subtitle alone — the watch phases
need the watch's geometry and that is an agent annotation nobody has made yet.
`REFUSED` is the answer until it exists, because a framing decision resting on
an absence is what this whole batch is shaped against.

THE FACE DETECTIONS ARE PROPOSALS. §A: accept local detections as proposals and
require visual confirmation on the construction frames. Codex counted, in the
samples before 224.2 s of `b23c14c41495`, 163 observations of which 34 carry
MORE THAN ONE candidate face and one carries none — so the boxes cannot be
unioned and the result called "the speaker". This takes the largest box per
sample as the proposal, records how many samples were ambiguous, and refuses a phase in which any
detection ELSEWHERE in the frame is at least as large as the one being chosen.

MEASURED, on `b23c14c41495` before 224.2 s: 163 samples, 34 with more than one
candidate, 1 with none, 38 runner-up boxes of which 19 are entirely disjoint
from the chosen one — false positives on the railing, the chairs and the
speaker's own hand, at 8-43% of its area. None rivals it. That, plus the
inspection of every sampled frame of both clips and all 15 windows of the
project, which found ONE person throughout, is what confirms the choice; the
`rivals` count is what would withdraw it on a source where a second person
exists.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))

DATA = Path(os.environ.get("CLIPFORGE_DATA_DIR") or (_ROOT / "data")) / "clipper"

#: Two regions are close enough to merge when neither dimension of their union
#: exceeds EITHER of them by more than this — not the larger of them, which is
#: how the first version called a merge that shrank one phase by a third
#: "mergeable at 4.4%". It is a REPORTING threshold, not a decision: the cost is
#: printed either way, in delivered scale as well as in growth, and the choice
#: is the editor's.
MERGE_SLACK = 0.08

#: Confirmed sub-pixel shortfall of the detector's y, measured on 52 annotated
#: frames of `6053a598cf06`: the text never reaches above the box and reaches
#: below it on 11 of them, by at most 0.6 proxy pixels. Added to the bottom of
#: every line box so a region cannot clip a descender it was never shown.
Y_PAD_PROXY_PX = 0.6
PROXY_H = 270.0

#: `{clip: [(name, t0, t1, what must be kept, subject source)]}`, on the SOURCE
#: clock. The demonstration boundaries come from
#: `docs/refs/pilotf81b-source-observations-2026-09-05.md`; the gesture one is
#: the later end of the observed interval [223.90, 224.20] and that choice is an
#: EDITING choice, not a detected moment — the observation is the interval.
#: Which annotated parts each phase must hold, from Codex's table. `screen` is
#: the display alone and is what the last phase keeps COMPLETE; `face` is the
#: speaker as context, which is why it is in the removal phase too.
PHASE_KEEPS: dict[str, tuple[str, ...]] = {
    "watch-worn": ("face", "hand", "watch"),
    "removal": ("face", "hand", "watch"),
    "screen": ("screen", "watch", "hand", "face"),
}

PHASES: dict[str, list[tuple[str, float, float, str, str]]] = {
    "6053a598cf06": [
        ("speech", 1147.75, 1204.58,
         "the speaker and the complete subtitle", "face"),
    ],
    "b23c14c41495": [
        ("speech", 183.62, 224.20,
         "the speaker and the complete subtitle", "face"),
        ("watch-worn", 224.20, 230.90,
         "the speaker, the gesture toward the wrist, the watch, the subtitle",
         "watch"),
        ("removal", 230.90, 239.40,
         "the hands, the watch along the presentation path, the speaker's "
         "context, the subtitle", "watch"),
        ("screen", 239.40, 242.42,
         "the complete screen as primary target, the subtitle, and the "
         "withdrawal movement", "watch"),
    ],
}

NO_SUBJECT_ANNOTATION = ("the_watch_geometry_is_an_agent_annotation_and_none_"
                         "has_been_made_for_this_phase")


def _watch_subject(clip: str, phase: str, t0: float, t1: float):
    """The annotated parts for a watch phase, or `(None, why)`.

    The FRAMES are the construction samples, addressed by index — the
    annotation was made on those frames and a region built from a different set
    would be built from a different picture.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "watchann", str(_ROOT / "scripts" / "watch_annotations.py"))
    wa = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wa)

    keeps = PHASE_KEEPS.get(phase)
    if keeps is None:
        return None, f"no_keep_list_declared_for_the_phase_{phase}"
    samples = _samples_for(clip)
    frames = [f for t, f in samples if t0 <= t < t1]
    # The withdrawal frames are not construction samples and are added
    # deliberately: the clip runs to 242.42 s and 241.8 is only the last thing
    # the sampler reached, so a region built without them would be built from
    # the close-up alone.
    if phase == "screen":
        frames += [f for f in (2424, 2425) if f in wa.WATCH]
    if not frames:
        return None, "no_construction_frame_in_this_phase"
    missing = [f for f in frames if f not in wa.WATCH]
    if missing:
        return None, f"no_watch_annotation_for_frames_{missing}"

    boxes = []
    for f in frames:
        for what in keeps:
            box = wa.WATCH[f].get(what)
            if box is None:
                continue
            x0, x1, y0, y1 = box
            boxes.append({"x0": x0, "x1": x1, "y0": y0, "y1": y1})
    if not boxes:
        return None, "the_annotation_holds_none_of_the_parts_this_phase_keeps"
    widths = [b["x1"] - b["x0"] for f in frames
              for k in ("screen", "watch")
              if (b := (lambda v: {"x0": v[0], "x1": v[1]} if v else None)(
                  wa.WATCH[f].get(k)))]
    return {"boxes": boxes, "frames": frames,
            "not_visible": sum(1 for f in frames if f in wa.WATCH_NOT_VISIBLE),
            "clipped": sum(1 for f in frames if f in wa.CLIPPED_BY_FRAME),
            "uncertainty": wa.MARGIN_UNCERTAINTY,
            # The widest annotated watch or screen in the phase, as the size
            # whose delivered pixels are worth reporting.
            "watch_w": (max(widths) if widths else 0.0) * 2560}, None


def _samples_for(clip: str) -> list[tuple[float, int]]:
    """`[(decoded time, frame index)]` for the construction samples."""
    import json as _json

    path = DATA / "pilotf81b" / "caption_frames" / f"{clip}.samples.json"
    blob = _json.loads(path.read_text(encoding="utf-8"))
    return [(s["t_decoded"], s["frame"]) for s in blob["observation"]["samples"]
            if s.get("frame") is not None and s.get("t_decoded") is not None]


def _annotations():
    spec = importlib.util.spec_from_file_location(
        "ann", str(_ROOT / "scripts" / "apply_line_annotations.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


async def _plan_for(project_id: str, clip_id: str):
    from database import init_db
    from workers.clipper_render_plan import _load

    await init_db()
    clip, project = await _load(clip_id)
    sidecar = DATA / project_id / "exports" / f"{clip_id}.json"
    return clip, project, json.loads(sidecar.read_text(encoding="utf-8"))


def _overlaps(a, b) -> bool:
    """Do two proxy boxes touch at all. A runner-up INSIDE the chosen box is
    the same face detected twice; one somewhere else is a different thing."""
    ax0, ay0, aw, ah = a
    bx0, by0, bw, bh = b
    return not (ax0 + aw <= bx0 or bx0 + bw <= ax0
                or ay0 + ah <= by0 or by0 + bh <= ay0)


def _face_proposals(sidecar: dict, t0: float, t1: float,
                    proxy_w: float, proxy_h: float) -> dict:
    """The speaker's box per sample, as a PROPOSAL, with the ambiguity counted.

    `_review_faces` is on the SOURCE clock and in proxy pixels. The largest box
    in a sample is taken as the speaker — he fills the frame on this source —
    and every sample with more than one candidate is counted so the caller can
    see how much of the phase rests on that choice rather than on a confirmed
    single detection.
    """
    plan = sidecar.get("dynamic_plan") or {}
    out: dict[str, Any] = {"boxes": [], "samples": 0, "ambiguous": 0,
                           "empty": 0, "disjoint_runner_ups": 0, "rivals": 0}
    for row in plan.get("_review_faces") or []:
        try:
            t = float(row.get("t"))
        except (TypeError, ValueError):
            continue
        if not (t0 <= t < t1):
            continue
        out["samples"] += 1
        boxes = [b for b in (row.get("boxes") or []) if len(b) >= 4]
        if not boxes:
            out["empty"] += 1
            continue
        ordered = sorted(boxes, key=lambda b: -b[2] * b[3])
        if len(ordered) > 1:
            out["ambiguous"] += 1
            for other in ordered[1:]:
                if not _overlaps(ordered[0], other):
                    out["disjoint_runner_ups"] += 1
                    # A RIVAL is a detection somewhere else that is at least as
                    # big as the one being chosen. Nothing smaller can unseat
                    # it, and nothing overlapping is a second person. This is
                    # not a tuned threshold — it is "does anything rival the
                    # box I am about to call the speaker".
                    if other[2] * other[3] >= ordered[0][2] * ordered[0][3]:
                        out["rivals"] += 1
        x, y, w, h = ordered[0]
        out["boxes"].append({"x0": x / proxy_w, "x1": (x + w) / proxy_w,
                             "y0": y / proxy_h, "y1": (y + h) / proxy_h})
    return out


def _lines_in(clip: str, t0: float, t1: float, ann) -> list[dict]:
    """The confirmed full lines inside the phase, with the measured y padding."""
    pad = Y_PAD_PROXY_PX / PROXY_H
    out = []
    for t, (x0, x1) in (ann.LINES.get(clip) or {}).items():
        if not (t0 <= t < t1):
            continue
        out.append({"x0": x0, "x1": x1, "y0": 0.9037,
                    "y1": min(1.0, 0.9704 + pad)})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project")
    ap.add_argument("clip")
    args = ap.parse_args()

    from services.clipper import caption_region as cr

    phases = PHASES.get(args.clip)
    if phases is None:
        print(f"REFUSED: no phases declared for {args.clip}")
        return 2
    ann = _annotations()
    if args.clip not in ann.LINES:
        print(f"REFUSED: no confirmed line extents for {args.clip}")
        return 2

    clip, project, sidecar = asyncio.run(_plan_for(args.project, args.clip))
    plan = sidecar.get("dynamic_plan") or {}
    sw, sh = int(plan["src_w"]), int(plan["src_h"])
    proxy_w, proxy_h = 480.0, 270.0
    face_w = float(((plan.get("subject") or {}).get("face") or {}).get("w") or 0)

    rows: list[dict] = []
    refused = 0
    for name, t0, t1, keep, source in phases:
        lines = _lines_in(args.clip, t0, t1, ann)
        row: dict[str, Any] = {"phase": name, "t0": t0, "t1": t1,
                               "keep": keep, "subject_source": source,
                               "lines": len(lines), "region": None,
                               "why": None}
        if not lines:
            row["why"] = "no_confirmed_subtitle_line_in_this_phase"
            rows.append(row)
            refused += 1
            print(f"  {name:<12} REFUSED — {row['why']}")
            continue
        if source == "watch":
            subject, why = _watch_subject(args.clip, name, t0, t1)
            if subject is None:
                row["why"] = why
                rows.append(row)
                refused += 1
                print(f"  {name:<12} REFUSED — {why}")
                continue
            row["subject"] = {"frames": len(subject["frames"]),
                              "boxes": len(subject["boxes"]),
                              "not_visible": subject["not_visible"],
                              "clipped_by_frame": subject["clipped"],
                              "margin_uncertainty": subject["uncertainty"]}
            got = cr.region_for(lines, None, sw, sh,
                                subject_boxes=subject["boxes"])
            if got["rect"] is None:
                row["why"] = got["why"]
                rows.append(row)
                refused += 1
                print(f"  {name:<12} REFUSED — {got['why']}")
                continue
            sizes = cr.delivered_sizes(got["rect"], subject_w=subject["watch_w"],
                                       band_h=(0.9704 - 0.9037) * sh)
            row.update({"region": got["rect"], "sizes": sizes,
                        "aspect": got["aspect"], "built_from": got["built_from"]})
            rows.append(row)
            print(f"  {name:<12} region {got['rect']}  aspect {got['aspect']}")
            print(f"  {'':<12} scale {sizes['scale']}, watch "
                  f"{sizes['subject_w_out_px']}px, band "
                  f"{sizes['band_h_out_px']}px, bars "
                  f"{sizes['letterbox_bars_px']}/{sizes['pillarbox_bars_px']}px")
            print(f"  {'':<12} from {len(lines)} confirmed lines and "
                  f"{len(subject['boxes'])} annotated parts over "
                  f"{len(subject['frames'])} frames "
                  f"({subject['not_visible']} with the watch out of frame, "
                  f"{subject['clipped']} clipped by an edge)")
            continue

        faces = _face_proposals(sidecar, t0, t1, proxy_w, proxy_h)
        row["subject"] = {k: faces[k] for k in
                          ("samples", "ambiguous", "empty",
                           "disjoint_runner_ups", "rivals")}
        if faces["rivals"]:
            # A detection elsewhere in the frame that is at least as large as
            # the one being chosen. Calling either "the speaker" without
            # looking at that frame is exactly what §A forbids.
            row["why"] = (f"{faces['rivals']}_samples_have_a_detection_"
                          f"elsewhere_at_least_as_large_as_the_chosen_one")
            rows.append(row)
            refused += 1
            print(f"  {name:<12} REFUSED — {row['why']}")
            continue
        if not faces["boxes"]:
            row["why"] = "no_face_detection_in_this_phase"
            rows.append(row)
            refused += 1
            print(f"  {name:<12} REFUSED — {row['why']}")
            continue

        got = cr.region_for(lines, None, sw, sh, subject_boxes=faces["boxes"])
        if got["rect"] is None:
            row["why"] = got["why"]
            rows.append(row)
            refused += 1
            print(f"  {name:<12} REFUSED — {got['why']}")
            continue
        sizes = cr.delivered_sizes(got["rect"], subject_w=face_w,
                                   band_h=(0.9704 - 0.9037) * sh)
        row["region"] = got["rect"]
        row["sizes"] = sizes
        row["aspect"] = got["aspect"]
        row["built_from"] = got["built_from"]
        rows.append(row)
        print(f"  {name:<12} region {got['rect']}  aspect {got['aspect']}")
        print(f"  {'':<12} scale {sizes['scale']}, subject "
              f"{sizes['subject_w_out_px']}px, band "
              f"{sizes['band_h_out_px']}px, bars "
              f"{sizes['letterbox_bars_px']}/{sizes['pillarbox_bars_px']}px")
        print(f"  {'':<12} from {len(lines)} confirmed lines and "
              f"{len(faces['boxes'])} face proposals "
              f"({faces['ambiguous']} ambiguous, {faces['empty']} empty)")

    # MERGEABLE, reported and not applied. Two consecutive regions that fit
    # inside a common rectangle without either dimension growing by more than
    # `MERGE_SLACK` cost little to unite; whether to is the editor's call.
    built = [r for r in rows if r.get("region")]
    for a, b in zip(built, built[1:]):
        ra, rb = a["region"], b["region"]
        w = max(ra["x"] + ra["w"], rb["x"] + rb["w"]) - min(ra["x"], rb["x"])
        h = max(ra["y"] + ra["h"], rb["y"] + rb["h"]) - min(ra["y"], rb["y"])
        # AGAINST EACH REGION, not against the larger of the two. The first
        # version divided by the larger, which reports a merge that leaves the
        # big phase untouched and shrinks the small one by a third as "4.4%":
        # the cost of a merge is what it does to the phase that loses, and the
        # phase that loses is always the smaller one.
        each = [max(w / r["w"], h / r["h"]) - 1.0 for r in (ra, rb)]
        # The delivered consequence, which is what a reader can judge: the
        # scale each phase would be rendered at before and after.
        scales = [min(cr.OUT_W / r["w"], cr.OUT_H / r["h"]) for r in (ra, rb)]
        merged_scale = min(cr.OUT_W / w, cr.OUT_H / h)
        print(f"  merge {a['phase']}+{b['phase']}: union grows "
              f"{each[0] * 100:.1f}% over the first and {each[1] * 100:.1f}% "
              f"over the second  -> "
              f"{'mergeable' if max(each) <= MERGE_SLACK else 'costly'}")
        print(f"  {'':<12} scale {scales[0]:.3f}/{scales[1]:.3f} -> "
              f"{merged_scale:.3f} for both")

    out_dir = DATA / args.project / "phase_regions"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{args.clip}.regions.json").write_text(
        json.dumps({"clip": args.clip, "src_w": sw, "src_h": sh,
                    "phases": rows, "verdict": None}, indent=1, default=str),
        encoding="utf-8")
    print(f"\n{len(built)} region(s) built, {refused} phase(s) refused")
    return 0 if not refused else 2


if __name__ == "__main__":
    raise SystemExit(main())
