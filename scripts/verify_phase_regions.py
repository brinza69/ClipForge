"""Do the FROZEN regions hold what they never saw.

    python scripts/verify_phase_regions.py pilotf81b 6053a598cf06

THE ONLY NON-CIRCULAR TEST. `build_phase_regions` makes a region from the
confirmed observations of a phase, so of course it contains them. This takes
frames that took NO part in that — chosen at the midpoints between construction
samples, addressed by frame index, and shown to be disjoint on the decoded index
rather than on the times that produced it — and asks whether the frozen
rectangle still holds them.

AND IT COMPARES AGAINST THE COMPLETE LINES, not the detector's boxes. Codex:
"otherwise even a 100% result could mean only that we kept all the fragments the
detector managed to see". The hold-out frames are annotated the same way the
construction frames were, off the same magnified band sheets with the same
ruler, which is why this costs a second round of looking rather than a second
call.

WHAT IT CANNOT DO. It cannot be re-run after the region is adjusted and still
be a hold-out: the moment a region is changed because of what this reported,
these frames become construction data and the next verification owes new ones.
Nothing in code can enforce that; `held_out` names the set so a reader can see
which frames a claim rests on.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))

DATA = Path(os.environ.get("CLIPFORGE_DATA_DIR") or (_ROOT / "data")) / "clipper"

#: The same pad the regions were built with — measured, not chosen: on 52
#: annotated frames the text never reaches above the detector's box and reaches
#: below it on 11, by at most 0.6 proxy pixels.
Y_PAD_PROXY_PX = 0.6
PROXY_H = 270.0
LINE_Y0, LINE_Y1 = 0.9037, 0.9704


def _annotations():
    spec = importlib.util.spec_from_file_location(
        "ann", str(_ROOT / "scripts" / "apply_line_annotations.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m



# --- the SUBJECT half --------------------------------------------------------
#
# The line half above asks whether the region holds the subtitle. That is not
# the whole promise: a watch phase's region has to hold the WATCH, and until
# this existed the script printed "SUBJECT NOT VERIFIED" and exited 2 rather
# than pretending otherwise. These read the hold-out annotation and check each
# part the phase declares it keeps, one part at a time, so the report says
# WHICH part a region clips instead of only how many boxes it lost.

#: What the SPEECH phase keeps, in the same shape as `build_phase_regions`'s
#: table. It is declared here and not there because the builder never consults
#: it — the speech region comes from the plan's faces — but the verification
#: still has to name what it is checking. `_keeps` asserts the phases the two
#: share are identical, so the two tables cannot drift apart in silence.
SPEECH_KEEPS = ("face",)

#: Half-width of the transition window, in seconds.
WINDOW_S = 0.5


def _watch_annotations():
    spec = importlib.util.spec_from_file_location(
        "wa", str(_ROOT / "scripts" / "watch_annotations.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _keeps():
    """`{phase: parts}` for every phase, with the shared ones proven identical."""
    spec = importlib.util.spec_from_file_location(
        "bpr", str(_ROOT / "scripts" / "build_phase_regions.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    out = dict(m.PHASE_KEEPS)
    if "speech" in out and tuple(out["speech"]) != SPEECH_KEEPS:
        # A verification checking a different list from the one the region was
        # built for is not a verification of that region.
        raise SystemExit("REFUSED: the two keep lists disagree about `speech`")
    out["speech"] = SPEECH_KEEPS
    return out


#: A part that is neither annotated nor declared invisible has not been looked
#: at, and a frame nobody looked at is not a frame the region held.
_NOT_VISIBLE = {"watch": "HOLDOUT_WATCH_NOT_VISIBLE",
                "hand": "HOLDOUT_HAND_NOT_VISIBLE"}


def _subject_boxes(wa, frames, parts):
    """`({part: [box]}, missing)` for the hold-out frames of one phase."""
    got: dict[str, list] = {p: [] for p in parts}
    missing: list[str] = []
    for f in frames:
        ann = wa.HOLDOUT.get(f)
        if ann is None:
            missing.append(f"f{f}:unannotated")
            continue
        for part in parts:
            box = ann.get(part)
            if box is not None:
                x0, x1, y0, y1 = box
                got[part].append({"x0": x0, "x1": x1, "y0": y0, "y1": y1,
                                  "frame": f})
                continue
            name = _NOT_VISIBLE.get(part)
            if name and f in getattr(wa, name, ()):
                continue          # looked at, and there was nothing to see
            missing.append(f"f{f}:{part}")
    return got, missing


def _check_subject(cr, wa, phase, frames, parts, sw, sh):
    """Every declared part of one phase against its frozen region."""
    boxes, missing = _subject_boxes(wa, frames, parts)
    if missing:
        return {"why": "not_looked_at", "missing": missing}, None
    rows = {}
    clipped = indeterminate = 0
    # The margins are read off a grid every 0.1 of the frame and resolve to
    # about +/-0.01 of it. An overflow SMALLER than that is neither a clip nor a
    # pass: the instrument cannot tell the two apart. It gets its own state,
    # counted apart from both, and it does NOT clear the exit code — a thing
    # that could not be measured must never read as a thing that was fine.
    tol_x = wa.MARGIN_UNCERTAINTY * sw
    tol_y = wa.MARGIN_UNCERTAINTY * sh
    for part, got in boxes.items():
        if not got:
            # Not a pass either: the phase declares it keeps this and no
            # hold-out frame shows it.
            rows[part] = {"why": "no_holdout_box_for_this_part"}
            continue
        r = phase["region"]
        held = cut = maybe = 0
        worst = None
        for b in got:
            overs = [(r["x"] - b["x0"] * sw, tol_x),
                     (b["x1"] * sw - (r["x"] + r["w"]), tol_x),
                     (r["y"] - b["y0"] * sh, tol_y),
                     (b["y1"] * sh - (r["y"] + r["h"]), tol_y)]
            over, tol = max(overs, key=lambda o: o[0])
            if over <= 0.5:
                held += 1
                continue
            if over <= tol:
                maybe += 1
            else:
                cut += 1
            if worst is None or over > worst[1]:
                worst = (b["frame"], round(over, 1), round(tol, 1))
        rows[part] = {"why": None, "held_out": len(got), "held": held,
                      "clipped": cut, "indeterminate": maybe,
                      "worst_overflow_px": round(worst[1], 1) if worst else 0.0,
                      "worst_frame": worst[0] if worst else None,
                      "tolerance_px": round(worst[2], 1) if worst else None}
        clipped += cut
        indeterminate += maybe
    unseen = [p for p, r in rows.items() if r.get("why")]
    return {"why": None, "parts": rows, "clipped": clipped,
            "indeterminate": indeterminate, "parts_with_no_box": unseen}, clipped


def _transitions(cr, wa, phases, frames_at, sw, sh):
    """At each boundary, what the framing change gains and what it cuts.

    The per-phase check above asks whether each frame is held by ITS OWN
    region. This asks the other question, and it is the one the user asked for:
    on the frames either side of a cut, would the region on the far side have
    held them. A part held before the cut and clipped after it is a gesture the
    edit walks off, whatever the phase averages say.
    """
    out = []
    for a, b in zip(phases, phases[1:]):
        edge = float(b["t0"])
        # Codex: all the frames within about half a second either side, and
        # widen it if the gesture starts earlier or runs on past it. The
        # 223.90-224.20 gesture interval fits inside this window at 224.20.
        near = [(t, f) for t, f in frames_at if abs(t - edge) <= WINDOW_S]
        if not near:
            out.append({"boundary": edge,
                        "why": f"no_holdout_frame_within_{WINDOW_S}s"})
            continue
        rows = []
        for t, f in sorted(near):
            ann = wa.HOLDOUT.get(f)
            if ann is None:
                rows.append({"frame": f, "t": t, "why": "unannotated"})
                continue
            side = a if t < edge else b
            other = b if t < edge else a
            for part, box in sorted(ann.items()):
                one = [{"x0": box[0], "x1": box[1], "y0": box[2], "y1": box[3]}]
                here = cr.verify_frozen(side["region"], one, sw, sh)
                there = cr.verify_frozen(other["region"], one, sw, sh)
                rows.append({"frame": f, "t": t, "part": part,
                             "phase": side["phase"], "held_here": here["held"] == 1,
                             "held_by_the_other_side": there["held"] == 1,
                             "overflow_px": here["worst_overflow_px"]})
        out.append({"boundary": edge, "from": a["phase"], "to": b["phase"],
                    "why": None, "frames": sorted({r["frame"] for r in rows}),
                    "rows": rows})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project")
    ap.add_argument("clip")
    ap.add_argument("--phase", default="", help="one phase, or all of them")
    args = ap.parse_args()

    from services.clipper import caption_region as cr
    from services.clipper import source_caption_observation as sco

    frames_dir = DATA / args.project / "caption_frames"
    regions_path = (DATA / args.project / "phase_regions"
                    / f"{args.clip}.regions.json")
    if not regions_path.exists():
        print(f"REFUSED: no frozen regions at {regions_path}")
        return 2
    blob = json.loads(regions_path.read_text(encoding="utf-8"))
    sw, sh = int(blob["src_w"]), int(blob["src_h"])
    wanted = [p for p in blob["phases"] if p.get("region")
              and (not args.phase or p["phase"] == args.phase)]
    if not wanted:
        print(f"REFUSED: no built region for {args.phase or 'any phase'}")
        return 2

    key = f"{args.clip}.holdout"
    ann = _annotations()
    lines = ann.LINES.get(key)
    if not lines:
        print(f"REFUSED: no annotated hold-out lines for {key}")
        return 2

    build = frames_dir / f"{args.clip}.samples.json"
    held = frames_dir / f"{args.clip}.holdout.samples.json"
    for path in (build, held):
        if not path.exists():
            print(f"REFUSED: {path} is not there")
            return 2
    a = json.loads(build.read_text(encoding="utf-8"))["observation"]
    b = json.loads(held.read_text(encoding="utf-8"))["observation"]
    apart = sco.disjoint(a, b)
    if apart["disjoint"] is not True:
        # A hold-out that shares frames with the construction set reports the
        # circularity as escaped, which is worse than not running at all.
        print(f"REFUSED: the hold-out is not disjoint — {apart}")
        return 2

    # EVERY hold-out sample must be accounted for, as an annotated line or as a
    # frame confirmed to carry none. A frame nobody annotated is not a frame the
    # region held.
    no_line = set(ann.NO_LINE.get(key, ()))
    times = [s["t_decoded"] for s in b["samples"] if s.get("t_decoded") is not None]
    missing = [t for t in times if t not in lines and t not in no_line]
    if missing:
        print(f"REFUSED: {len(missing)} hold-out frames are not annotated: "
              f"{missing[:8]}")
        return 2

    pad = Y_PAD_PROXY_PX / PROXY_H
    wa = _watch_annotations()
    keeps = _keeps()
    # `{decoded time: frame index}` for the hold-out samples, so a phase's
    # frames are the ones the decoder actually returned and not the ones asked
    # for. Both are needed: the phase window is on the clock, the annotation is
    # by index.
    frames_at = [(s["t_decoded"], s["frame"]) for s in b["samples"]
                 if s.get("t_decoded") is not None and s.get("frame") is not None]
    print(f"{args.clip}  hold-out {apart['holdout']} frames, "
          f"{apart['shared']} shared with construction, "
          f"disjoint={apart['disjoint']}, {len(no_line)} carry no line")
    rows = []
    clipped_total = 0
    indeterminate_total = 0
    unlooked = []
    for phase in wanted:
        t0, t1 = float(phase["t0"]), float(phase["t1"])
        inside = {t: xx for t, xx in lines.items() if t0 <= t < t1}
        boxes = [{"x0": x0, "x1": x1, "y0": LINE_Y0,
                  "y1": min(1.0, LINE_Y1 + pad)} for x0, x1 in inside.values()]
        got = cr.verify_frozen(phase["region"], boxes, sw, sh)
        clipped_total += got["clipped"] or 0
        print(f"  {phase['phase']:<12} LINES {got['held']} held, "
              f"{got['clipped']} clipped of {got['held_out']}; worst overflow "
              f"{got['worst_overflow_px']} source px")

        # THE SUBJECT HALF. A region that holds every subtitle and loses the
        # watch has kept the caption and thrown the clip away.
        parts = keeps.get(phase["phase"])
        mine = [f for t, f in frames_at if t0 <= t < t1]
        if parts is None:
            sub = {"why": f"no_keep_list_declared_for_{phase['phase']}"}
        elif not mine:
            sub = {"why": "no_holdout_frame_falls_in_this_phase"}
        else:
            sub, _ = _check_subject(cr, wa, phase, mine, parts, sw, sh)
        rows.append({"phase": phase["phase"], "lines": got, "subject": sub,
                     "subject_frames": mine})
        if sub["why"]:
            unlooked.append(phase["phase"])
            print(f"  {'':<12} SUBJECT REFUSED: {sub['why']} "
                  f"{sub.get('missing', '')}")
            continue
        clipped_total += sub["clipped"]
        indeterminate_total += sub["indeterminate"]
        for part in (parts or ()):
            r = sub["parts"][part]
            if r.get("why"):
                unlooked.append(f"{phase['phase']}/{part}")
                print(f"  {'':<12} {part:<7} {r['why']}")
                continue
            print(f"  {'':<12} {part:<7} {r['held']} held, {r['clipped']} "
                  f"clipped, {r['indeterminate']} indeterminate of "
                  f"{r['held_out']}; worst {r['worst_overflow_px']} source px"
                  + (f" at f{r['worst_frame']} (tolerance "
                     f"{r['tolerance_px']}px)" if r["worst_frame"] else ""))

    # THE TRANSITIONS. Only meaningful across the WHOLE phase list: restricting
    # the run to one phase leaves every boundary with nothing on its far side.
    tr = []
    if not args.phase:
        tr = _transitions(cr, wa, wanted, frames_at, sw, sh)
        print("\n  transitions")
        for t in tr:
            if t["why"]:
                print(f"    {t['boundary']:>8.2f}  REFUSED: {t['why']}")
                unlooked.append(f"boundary@{t['boundary']}")
                continue
            lost = [r for r in t["rows"] if r.get("why") is None
                    and not r["held_here"]]
            # NOTE the transition rows still use verify_frozen's two-state
            # answer: they name WHICH side of a cut holds a part, and an
            # overflow of a few pixels does not change that reading.
            cut = [r for r in t["rows"] if r.get("why") is None
                   and r["held_here"] and not r["held_by_the_other_side"]]
            print(f"    {t['boundary']:>8.2f}  {t['from']} -> {t['to']}  "
                  f"frames {t['frames']}: {len(lost)} part(s) outside their own "
                  f"region, {len(cut)} the other side would have lost")
            for r in lost:
                print(f"      f{r['frame']} {r['t']:.2f}s {r['part']:<7} "
                      f"outside {r['phase']} by {r['overflow_px']} source px")
            for r in cut:
                print(f"      f{r['frame']} {r['t']:.2f}s {r['part']:<7} held by "
                      f"{r['phase']}, NOT by the other side of the cut")

    out = {"clip": args.clip, "disjoint": apart, "phases": rows,
           "transitions": tr,
           "held_out_frames": [s.get("frame") for s in b["samples"]],
           "verdict": None}
    (DATA / args.project / "phase_regions"
     / f"{args.clip}.verification.json").write_text(
        json.dumps(out, indent=1, default=str), encoding="utf-8")
    if unlooked:
        print(f"\n  {len(unlooked)} thing(s) nobody looked at: {unlooked}")
    # A clipped box is a real finding, and so is a part nobody checked: neither
    # may leave a clean exit code behind. `clipped_total` counts LINES and
    # SUBJECT parts together on purpose — a region that keeps every subtitle
    # and loses the watch has not passed.
    print(f"\n  {clipped_total} clipped box(es) over lines and subjects, "
          f"{indeterminate_total} indeterminate, {len(unlooked)} unchecked")
    # An indeterminate box is not a pass. It is an overflow the instrument
    # cannot separate from zero, and the answer to it is a better instrument,
    # not a green exit code.
    return 0 if not (clipped_total or indeterminate_total or unlooked) else 2


if __name__ == "__main__":
    raise SystemExit(main())
