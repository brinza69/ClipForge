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
#: Slack for the float comparisons that decide a verdict.
_EPS = 1e-6

SPEECH_KEEPS = ("face",)

#: Half-width of the transition window, in seconds.
WINDOW_S = 0.5


#: Half a proxy frame at 10 fps. Two times closer than this can be the same
#: decoded frame, and `_review_faces` carries no index to settle it with.
FACE_COLLISION_S = 0.05

NO_SIDECAR = "there_is_no_sidecar_for_this_clip"
NO_FACE_ROWS = "the_sidecar_carries_no_review_faces"


def _face_collisions(project: str, clip: str, holdout: dict) -> dict:
    """Hold-out frames that may be a face observation the regions were built on.

    REFUSES rather than returning zero when the sidecar or its face rows cannot
    be read: "no collisions found" and "nothing was looked at" are the two
    answers this whole file exists to keep apart.
    """
    out: dict[str, Any] = {"why": None, "collisions": 0, "frames": [],
                           "window_s": FACE_COLLISION_S, "face_samples": 0}
    path = DATA / project / "exports" / f"{clip}.json"
    if not path.exists():
        out["why"] = f"{NO_SIDECAR}: {path}"
        return out
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        out["why"] = f"{NO_SIDECAR}: {exc}"
        return out
    rows = ((blob.get("dynamic_plan") or {}).get("_review_faces")) or []
    if not rows:
        out["why"] = NO_FACE_ROWS
        return out
    times = []
    for row in rows:
        try:
            times.append(float(row.get("t")))
        except (TypeError, ValueError):
            continue
    if not times:
        out["why"] = NO_FACE_ROWS
        return out
    out["face_samples"] = len(times)
    for s in holdout["samples"]:
        t, f = s.get("t_decoded"), s.get("frame")
        if t is None:
            continue
        if any(abs(t - ft) < FACE_COLLISION_S for ft in times):
            out["collisions"] += 1
            out["frames"].append(f)
    return out


STALE_TIMES = "the_stored_times_predate_the_pos_msec_fix"
NO_FPS = "the_proxy_would_not_report_a_frame_rate"


def _times_match_indices(project: str, observation: dict, phases) -> dict:
    """Does every stored `t_decoded` equal its own frame's presentation time.

    A sample file written before the `POS_MSEC` fix carries a correct index
    with a time ONE FRAME EARLY, because the property was read before `read()`
    where it names the previous frame. Those files are still on disk and every
    phase assignment in this script is made from their times, so using one
    without noticing would put the old error straight back into a result that
    looks new.

    IT DOES NOT CORRECT THEM. The arithmetic is trivial — add one frame — and
    writing a computed value into a file that calls itself an observation is how
    a derivation starts reading as a measurement.

    WHAT IT REFUSES ON is not the staleness itself but its CONSEQUENCE: whether
    correcting a time would move a sample across a phase boundary. On this
    corpus every stored time is one frame early and NOT ONE sample changes
    phase, so the files are wrong labels over right assignments — a
    documentation defect, reported in full, rather than a measurement defect.
    The distinction matters because refusing on the staleness alone would block
    a lot that never reads these files, and passing silently would hide the day
    a boundary moves and one of them does cross.
    """
    import cv2

    out: dict[str, Any] = {"why": None, "checked": 0, "off": 0, "fps": None,
                           "worst": None, "would_change_phase": []}
    proxy = DATA / project / "proxy" / "proxy.mp4"
    cap = cv2.VideoCapture(str(proxy))
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) if cap.isOpened() else 0.0
    finally:
        cap.release()
    if not fps or fps <= 0:
        out["why"] = NO_FPS
        return out
    out["fps"] = fps
    half = 0.5 / fps
    edges = sorted({float(t0) for _, t0, _, _, _ in phases}
                   | {float(t1) for _, _, t1, _, _ in phases})

    def _band(x):
        return sum(1 for e in edges if x >= e)

    for sample in observation["samples"]:
        f, t = sample.get("frame"), sample.get("t_decoded")
        if f is None or t is None:
            continue
        out["checked"] += 1
        gap = t - f / fps
        if abs(gap) > half:
            out["off"] += 1
            if out["worst"] is None or abs(gap) > abs(out["worst"][1]):
                out["worst"] = (f, round(gap, 3))
            if _band(t) != _band(f / fps):
                out["would_change_phase"].append(f)
    if out["would_change_phase"]:
        out["why"] = STALE_TIMES
    return out


NOT_INDEPENDENT = "frames_in_this_lot_were_already_looked_at"


def _fresh_is_independent(wa, clip: str) -> dict:
    """Is the fresh lot actually disjoint from everything used to build.

    `select_fresh_lot` excluded these frames by construction, and "by
    construction" is the kind of guarantee this corpus has repeatedly found to
    be a claim rather than a fact. This recomputes it from the tables and the
    windows themselves, so a lot that drifted — an annotation added to WATCH
    after the lot was chosen, a boundary moved — is caught rather than trusted.
    """
    import importlib.util as _il

    spec = _il.spec_from_file_location(
        "bpr", str(_ROOT / "scripts" / "build_phase_regions.py"))
    bpr = _il.module_from_spec(spec)
    spec.loader.exec_module(bpr)

    out: dict[str, Any] = {"why": None, "checked": len(wa.FRESH), "shared": {}}
    fps = bpr.PROXY_FPS
    for f in sorted(wa.FRESH):
        if f in wa.WATCH:
            out["shared"][f] = "construction"
        elif f in wa.HOLDOUT:
            out["shared"][f] = "previous_holdout"
        else:
            for _, t0, _, _, _ in (bpr.PHASES.get(clip) or [])[1:]:
                if abs(f / fps - float(t0)) <= 0.5:
                    out["shared"][f] = f"transition_window_at_{t0}"
                    break
    if out["shared"]:
        out["why"] = NOT_INDEPENDENT
    return out


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
#: at, and a frame nobody looked at is not a frame the region held. The name is
#: completed with the lot's prefix, so `fresh` cannot silently consult the
#: hold-out's list of invisible watches and read a gap as a looked-at absence.
_NOT_VISIBLE = {"watch": "WATCH_NOT_VISIBLE", "hand": "HAND_NOT_VISIBLE"}


#: `{lot: (table attribute, not-visible attribute prefix)}`.
LOTS = {"holdout": ("HOLDOUT", "HOLDOUT_"), "fresh": ("FRESH", "FRESH_")}


def _subject_boxes(wa, frames, parts, which="holdout"):
    """`({part: [box]}, missing)` for one phase's frames of the chosen lot."""
    table = getattr(wa, LOTS[which][0])
    got: dict[str, list] = {p: [] for p in parts}
    missing: list[str] = []
    for f in frames:
        ann = table.get(f)
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
            if name and f in getattr(wa, LOTS[which][1] + name, ()):
                continue          # looked at, and there was nothing to see
            missing.append(f"f{f}:{part}")
    return got, missing


def _check_subject(cr, wa, phase, frames, parts, sw, sh, which="holdout"):
    """Every declared part of one phase against its frozen region."""
    boxes, missing = _subject_boxes(wa, frames, parts, which)
    if missing:
        return {"why": "not_looked_at", "missing": missing}, None
    rows = {}
    clipped = indeterminate = 0
    # The margins are read off a grid every 0.1 of the frame and resolve to
    # about +/-0.01 of it, and that uncertainty decides the verdict — Codex's
    # rule, per MARGIN, against THAT AXIS's tolerance:
    #
    #   held           the whole possible extent, as far as the source shows
    #                  it, fits
    #   clipped        the overflow survives even at the end of the
    #                  uncertainty most favourable to fitting
    #   indeterminate  both are compatible with the measurement
    #
    # THE FIRST VERSION WAS ASYMMETRIC and Codex caught it: any nominal
    # overflow <= 0.5 px was `held`, so the tolerance only ever applied once a
    # box had already left. The face at f2308 sits 7.2 px INSIDE the region
    # against a vertical uncertainty of +/-14.4 px — the real margin may well be
    # outside, and it was reading as held.
    #
    # AND THE AXIS IS CHOSEN PER MARGIN, NOT BY THE BIGGEST NUMBER. The
    # horizontal and vertical tolerances differ (25.6 and 14.4 source px here),
    # so taking the largest overflow in pixels first and then asking about its
    # tolerance can call a box held on the strength of a wide margin while a
    # smaller one on the other axis is already outside.
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
        worst_cut = worst_maybe = None
        for b in got:
            # A MARGIN THAT SITS ON THE SOURCE FRAME EDGE IS NOT A MEASUREMENT.
            # The annotation stops there because the thing continues outside
            # the picture, and what is out there is in no frame — so no region
            # can be sized to hold it, and there is nothing for +/-0.01 to be
            # the uncertainty OF. Codex's own wording carries this: held is
            # "the whole possible extent, VISIBLE IN THE SOURCE, fits". Giving
            # such a margin the normal tolerance made 24 boxes indeterminate
            # whose visible extent reaches the region's edge exactly.
            edge = 1e-9
            margins = [
                ("left", r["x"] - b["x0"] * sw,
                 0.0 if b["x0"] <= edge else tol_x),
                ("right", b["x1"] * sw - (r["x"] + r["w"]),
                 0.0 if b["x1"] >= 1.0 - edge else tol_x),
                ("top", r["y"] - b["y0"] * sh,
                 0.0 if b["y0"] <= edge else tol_y),
                ("bottom", b["y1"] * sh - (r["y"] + r["h"]),
                 0.0 if b["y1"] >= 1.0 - edge else tol_y)]
            verdict = "held"
            for side, over, tol in margins:
                # EPS, not 0.0: an overflow of exactly one tolerance is the
                # boundary between `clipped` and `indeterminate`, and floating
                # point put f2414's watch and hand on the wrong side of it —
                # 14.4 px against a 14.4 px tolerance reported as clipped by
                # 0.0 px. A verdict that turns on the last bit of a float is
                # not a verdict.
                if over - tol > _EPS:
                    verdict = "clipped"
                    if worst_cut is None or over - tol > worst_cut[3]:
                        worst_cut = (b["frame"], side, round(over, 1),
                                     round(over - tol, 1), round(tol, 1))
                elif over + tol > _EPS and verdict != "clipped":
                    verdict = "indeterminate"
                    if worst_maybe is None or over > worst_maybe[2]:
                        worst_maybe = (b["frame"], side, round(over, 1),
                                       round(tol, 1))
            if verdict == "clipped":
                cut += 1
            elif verdict == "indeterminate":
                maybe += 1
            else:
                held += 1
        rows[part] = {"why": None, "held_out": len(got), "held": held,
                      "clipped": cut, "indeterminate": maybe,
                      "worst_clip": worst_cut, "worst_indeterminate": worst_maybe}
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
    ap.add_argument("--set", dest="which", default="holdout",
                    choices=("holdout", "fresh"),
                    help="which lot judges the candidate. `holdout` is the "
                         "26 frames from before the replay correction — they "
                         "are DIAGNOSTIC AND REGRESSION material for the "
                         "corrected candidate, not independent confirmation of "
                         "it, because they were corrected in the same pass that "
                         "moved the regions. `fresh` is the lot chosen by "
                         "`select_fresh_lot.py` before anyone looked at it")
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

    ann = _annotations()
    lines = ann.LINES.get(f"{args.clip}.holdout")
    if args.which == "holdout" and not lines:
        print(f"REFUSED: no annotated hold-out lines for {args.clip}")
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
    # AND THE CAPTION SAMPLES ARE NOT THE ONLY THING THE REGIONS WERE BUILT
    # FROM. Codex: the speech region also used `_review_faces` at 4 Hz, and on
    # `6053a598cf06` five hold-out times coincide with one of those samples.
    # A time coincidence does not by itself prove the decoded frames are the
    # same — `_review_faces` keeps the time and the boxes and no frame index,
    # so the question cannot be settled from what is stored. That is exactly
    # why they must not be counted as independent observations of the subject:
    # they stay in the report, and out of the denominator.
    collisions = _face_collisions(args.project, args.clip, b)
    # BEFORE ANY OF IT, do the stored times name the frames they sit next to.
    import importlib.util as _il

    _spec = _il.spec_from_file_location(
        "bpr_phases", str(_ROOT / "scripts" / "build_phase_regions.py"))
    _bpr = _il.module_from_spec(_spec)
    _spec.loader.exec_module(_bpr)
    _phases = _bpr.PHASES.get(args.clip) or []
    for which, obs in (("construction", a), ("hold-out", b)):
        stale = _times_match_indices(args.project, obs, _phases)
        if stale["why"]:
            print(f"REFUSED: the {which} sample file is not usable — "
                  f"{stale['why']}, and {len(stale['would_change_phase'])} "
                  f"sample(s) change phase once corrected: "
                  f"{stale['would_change_phase'][:8]}")
            return 2
        if stale["off"]:
            # Reported in full every run, never quietly tolerated: the labels
            # are wrong, and the only reason this is not a refusal is that no
            # assignment moves when they are made right.
            print(f"  NOTE: {stale['off']} of {stale['checked']} {which} times "
                  f"predate the POS_MSEC fix (worst f{stale['worst'][0]} by "
                  f"{stale['worst'][1]}s) — 0 change phase once corrected")
    if apart["disjoint"] is not True:
        # A hold-out that shares frames with the construction set reports the
        # circularity as escaped, which is worse than not running at all.
        print(f"REFUSED: the hold-out is not disjoint — {apart}")
        return 2

    # EVERY hold-out sample must be accounted for, as an annotated line or as a
    # frame confirmed to carry none. A frame nobody annotated is not a frame the
    # region held.
    no_line = set(ann.NO_LINE.get(f"{args.clip}.holdout", ()))
    if args.which == "holdout":
        times = [s["t_decoded"] for s in b["samples"]
                 if s.get("t_decoded") is not None]
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
    if args.which == "fresh":
        # The fresh lot is addressed by index alone: no caption samples were
        # taken for it, so its time is the frame's own presentation time.
        frames_at = [(f / 10.0, f) for f in sorted(wa.FRESH)]
        indep = _fresh_is_independent(wa, args.clip)
        if indep["why"]:
            # A "fresh" lot that shares frames with construction reports the
            # circularity as escaped, which is worse than not running at all.
            print(f"REFUSED: {indep['why']} — {indep['shared']}")
            return 2
        print(f"{args.clip}  FRESH lot, {indep['checked']} frames, none shared "
              f"with construction, the previous hold-out or a transition window")
    else:
        print(f"{args.clip}  hold-out {apart['holdout']} frames, "
              f"{apart['shared']} shared with construction, "
              f"disjoint={apart['disjoint']}, {len(no_line)} carry no line")
        if collisions["why"]:
            print(f"  REFUSED: the face observations could not be read — "
                  f"{collisions['why']}")
            return 2
        print(f"  {collisions['collisions']} hold-out frame(s) fall within "
              f"{collisions['window_s']}s of a `_review_faces` sample used to "
              f"build a region: {collisions['frames']}")
    rows = []
    clipped_total = 0
    indeterminate_total = 0
    unlooked = []
    for phase in wanted:
        t0, t1 = float(phase["t0"]), float(phase["t1"])
        if args.which == "fresh":
            # THE LINE HALF IS NOT VERIFIED BY THIS LOT and is not carried over
            # from the previous one. Those 63 lines were held by regions that
            # have since moved; reusing the number would attach a result to a
            # candidate it was never measured on, which is the whole failure
            # this file exists to prevent. Annotating the caption band on these
            # 36 frames is a separate round of looking, and it has not happened.
            got = {"why": "not_annotated_for_the_fresh_lot", "held": None,
                   "clipped": None, "held_out": 0, "worst_overflow_px": None}
            unlooked.append(f"{phase['phase']}/lines")
            print(f"  {phase['phase']:<12} LINES not verified by this lot — "
                  f"the caption band on these frames has not been read")
        else:
            inside = {t: xx for t, xx in lines.items() if t0 <= t < t1}
            boxes = [{"x0": x0, "x1": x1, "y0": LINE_Y0,
                      "y1": min(1.0, LINE_Y1 + pad)}
                     for x0, x1 in inside.values()]
            got = cr.verify_frozen(phase["region"], boxes, sw, sh)
            clipped_total += got["clipped"] or 0
            print(f"  {phase['phase']:<12} LINES {got['held']} held, "
                  f"{got['clipped']} clipped of {got['held_out']}; worst "
                  f"overflow {got['worst_overflow_px']} source px")

        # THE SUBJECT HALF. A region that holds every subtitle and loses the
        # watch has kept the caption and thrown the clip away.
        parts = keeps.get(phase["phase"])
        mine = [f for t, f in frames_at if t0 <= t < t1]
        if parts is None:
            sub = {"why": f"no_keep_list_declared_for_{phase['phase']}"}
        elif not mine:
            sub = {"why": f"no_{args.which}_frame_falls_in_this_phase"}
        else:
            sub, _ = _check_subject(cr, wa, phase, mine, parts, sw, sh,
                                    args.which)
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
            line = (f"  {'':<12} {part:<7} {r['held']} held, {r['clipped']} "
                    f"clipped, {r['indeterminate']} indeterminate of "
                    f"{r['held_out']}")
            if r["worst_clip"]:
                f, side, over, past, tol = r["worst_clip"]
                line += (f"; worst clip f{f} {side} {over}px, {past}px past the "
                         f"{tol}px tolerance")
            elif r["worst_indeterminate"]:
                f, side, over, tol = r["worst_indeterminate"]
                line += (f"; closest f{f} {side} {over}px against a {tol}px "
                         f"tolerance")
            print(line)

    # THE TRANSITIONS. Only meaningful across the WHOLE phase list: restricting
    # the run to one phase leaves every boundary with nothing on its far side.
    tr = []
    if args.which == "fresh":
        # By construction there is no fresh frame within 0.5 s of a boundary —
        # they were excluded as already inspected. Printing an empty
        # transitions section would read as "the cuts were checked and nothing
        # was wrong", so it says what it is instead.
        print("\n  transitions: NOT CHECKED BY THIS LOT — the +/-0.5 s windows "
              "were excluded from it as already inspected; the transition "
              "result stands from the hold-out run, on regions that have since "
              "moved")
        unlooked.append("transitions")
    elif not args.phase:
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

    out = {"clip": args.clip, "lot": args.which, "disjoint": apart,
           "phases": rows,
           "transitions": tr,
           "held_out_frames": [s.get("frame") for s in b["samples"]],
           "verdict": None}
    (DATA / args.project / "phase_regions"
     / f"{args.clip}.verification.{args.which}.json").write_text(
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
