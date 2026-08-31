"""One export's caption placement, measured — Batch R6's corpus reader.

Split out of `scripts/audit_caption_placement.py` at the 500-line limit, and
split HERE because the seam is real: this measures ONE sidecar and knows nothing
about denominators, gates or exit codes, while the script does nothing else. It
also puts the measurement somewhere a test can import, which `scripts/` is not.

WHAT IT REFUSES, and each of these is a fact that would otherwise be reported as
a clean answer:

    a sub-record that is not a record   `.get` on a list raises, out of a loop
                                        over the corpus
    an assumed output height            1920 hardcoded is a second source of
                                        truth about geometry
    unreadable face inputs              "the analysis could not be read" is not
                                        "no faces"
    a sidecar with no `source_start`    it cannot say where in the source it
                                        came from, so its own face column is
                                        unavailable — and that is NOT the same
                                        fact as unreadable analysis files,
                                        which void everyone's

THE CAPTION POSITION COMES FROM THE `.ass`, NOT FROM `caption_plan.y_pct`, and
the first version of this file had it wrong on 46 of 99 clips.

`y_pct` is the PRESET the plan asked for. The `.ass` carries the position
`resolve_position` settled on after avoiding the keep-outs, and it is what
libass burns. On 53 clips the two agree, because nothing was in the way; on 46
they do not, by as much as 933 pixels — `39c89ae2e16e/9fc63c77e3c1` stores 0.75
and burns 1141, and `pilot2c8a/003a5c53c51d` burns 507 against a stored 1440.

A plan is not the delivered artefact, the same way a `move: push` label is not
motion. Every figure this file produces about where the caption LANDS has to
come from the file that put it there.

The fallback to `y_pct` is kept for a clip with no `.ass`, and it is DECLARED:
`caption_y_source` says `ass` or `caption_plan`, so a number that rests on the
plan cannot be read as one that rests on the render.

AND THE SAMPLING IS SPARSER THAN THE SHOTS. The face detector runs about every
two seconds and a shot is typically one to four, so a shot with no sample inside
it has no face evidence: `None`, which is `unavailable`, never an empty list.
Nearly half the corpus's shots are in that state, and it is the denominator of
everything the face column says. Measured:

    samples in a shot's window    0: 951   1: 1007   2: 152   3: 13   7: 1   8: 2

A UNION OVER TIME, AND IT ANSWERS A NARROWER QUESTION THAN IT LOOKS. Where a
shot holds more than one sample — 168 of 2,126 — every sample's boxes go into
one list, so the report answers "was the caption over a face at ANY point in
this shot", not "throughout it". That is the conservative direction for a
warning and the wrong one for a claim of cleanliness, which is why it is stated
here rather than left to be inferred from the shape of the code.

AND A SAMPLE ON A BOUNDARY BELONGS TO ONE SHOT. The window used to be closed at
both ends, so a sample landing exactly on a cut counted for the shot that ended
and the shot that began — 20 of them in the corpus. The frame at that timestamp
is the one the new shot shows, so the window is half-open, and only the clip's
last shot keeps its endpoint, because nothing follows it to take the sample.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

from services.clipper import caption_choice as cc  # noqa: E402
from services.clipper import caption_placement as cp  # noqa: E402
from services.clipper import evidence_map as em  # noqa: E402

__all__ = ["POSITIONS", "measure"]

#: The preset names `_base_y_pct` knows. Anything else falls through to the
#: bottom preset, so trying more names would only find the same answer twice.
POSITIONS = ("bottom", "top", "center", "hook")


#: What the pipeline renders at, used ONLY as a declared assumption when the
#: file itself cannot be measured.
ASSUMED_OUT_H = 1920


def _rendered_height(mp4: Path) -> tuple[int, bool]:
    """`(the export's frame height, whether it had to be assumed)`.

    A PAIR, because a silent 1920 is a second source of truth about output
    geometry — the thing R0 refused outright — and an early return on a missing
    file is worse in the other direction: it took the whole row down, so a
    corpus with no renders on disk reported nothing about caption positions
    either, none of which depend on the render.

    So: measured when the file is there, assumed and SAID when it is not, and
    the assumption fails the run because the letterbox column rests on it.
    """
    try:
        import cv2
    except Exception:
        return ASSUMED_OUT_H, True
    if not mp4.exists():
        return ASSUMED_OUT_H, True
    cap = cv2.VideoCapture(str(mp4))
    try:
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    finally:
        cap.release()
    return (height, False) if height > 0 else (ASSUMED_OUT_H, True)


def _moving_crops(plan: dict) -> int:
    """Shots whose crop CHANGES SIZE across their own length.

    A PRECONDITION FOR WORK NOT YET DONE, counted here because the count is the
    thing that decides whether that work is possible the cheap way. Nothing in
    this report depends on it — the letterbox is geometry, not crop — so it does
    not fail the run.

    `shot["rect"]` looks like the crop the renderer takes and is not:
    `dynamic_geometry.visual_key` says the picture is what `build_sendcmd`
    schedules, and returns None for any shot with a multi-point size timeline.
    A mapper that projects a face through the rectangle is therefore right only
    while every timeline is a single point. Across the stored corpus every one
    of them is — but 2,024 of 2,126 shots are LABELLED `move: push` or `pull`
    and stand still only because `push_amount` is 0.0 in every stored style.
    Reading the label instead of the timeline says "95% of shots move", which is
    the opposite of the truth.
    """
    from services.clipper.dynamic_geometry import _size_timeline

    style = plan.get("style")
    style = style if isinstance(style, dict) else {}
    src_w, src_h = plan.get("src_w") or 0, plan.get("src_h") or 0
    if not (src_w and src_h):
        return 0
    moving = 0
    for shot in plan.get("shots") or []:
        if not isinstance(shot, dict):
            continue
        try:
            if len(_size_timeline(shot, style, src_w, src_h)) > 1:
                moving += 1
        except Exception:
            # Unreadable is not "static". It joins the count it would join if
            # it were moving, because the mapper could not use it either way.
            moving += 1
    return moving


#: `{\\pos(x, y)}` in an ASS override block. The y is the caption's baseline
#: anchor under `\\an5`, in `PlayResY` units, which the clipper writes as 1920.
_ASS_POS = re.compile(r"\\pos\((\d+)\s*,\s*(\d+)\)")


def _burned_y_pct(ass: Path, out_h: int) -> tuple[float | None, str]:
    """`(the position libass burned, where the number came from)`.

    ONE POSITION PER FILE, checked rather than assumed: across the corpus every
    `.ass` uses a single `\\pos` y for all its events, so a clip has one
    delivered caption position. A file that used more would be refused here
    rather than averaged, because "the caption is at 1141" would then be a
    sentence about no particular moment.
    """
    if not ass.exists() or out_h <= 0:
        return None, "no_ass"
    try:
        text = ass.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None, "ass_unreadable"
    ys = {int(m.group(2)) for line in text.splitlines()
          if line.startswith("Dialogue")
          for m in [_ASS_POS.search(line)] if m}
    if not ys:
        return None, "no_position_in_the_ass"
    if len(ys) > 1:
        return None, "more_than_one_position_in_the_ass"
    return ys.pop() / float(out_h), "ass"


def _requested_position(caption: dict) -> str | None:
    """The position name the clip's own style asked for, if it recorded one."""
    style = caption.get("style")
    if isinstance(style, str):
        try:
            style = ast.literal_eval(style)
        except Exception:
            return None
    if not isinstance(style, dict):
        return None
    name = style.get("position")
    return name if isinstance(name, str) and name else None


#: Why the delivered caption position cannot be checked against today's rule.
#: `clipper_captions` re-places the caption at RENDER time with
#: `layout_plan.safe_zones.keep_out` PLUS `panels_to_keep_out(panels, shots)`,
#: and `panels` is not stored on the sidecar. So the keep-out set the position
#: was resolved against no longer exists, and running `resolve_position` on the
#: smaller stored set answers a different question.
#:
#: THE FIRST TWO ANSWERS WERE BOTH ARTEFACTS OF THAT. Comparing the stored
#: `y_pct` — which is the PRESET, not the burned position — gave "8 of 99 today's
#: rule would not produce". Comparing the position actually burned, against the
#: same incomplete keep-outs, gave 54. Neither was a fact about the rule.
NOT_REPRODUCIBLE = "the_keep_out_set_the_caption_was_placed_against_is_not_stored"


def _explained_by(y_pct: float, layout: dict, out_h: int,
                  asked_for: str | None) -> list[str]:
    """Which preset positions TODAY'S `resolve_position` turns into this `y_pct`.

    EMPTY IS THE INTERESTING ANSWER, and it means the export was burned by a
    different rule — these sidecars predate the band scan that replaced six
    fixed ±4% nudges. Not corruption, and the difference matters in the useful
    direction: on `0c9685df852b/205a6ec12b00` the stored caption sits at 0.75
    inside a face keep-out spanning 0.229 to 0.797, and today's rule moves it to
    0.1642. So a mismatch is a clip whose placement cannot be used as evidence
    about the current rule, in either direction.
    """
    hits = []
    # The one it asked for, when it said. Otherwise all of them, and the row
    # carries `asked_for: null` so the weaker check is visible in the output.
    for name in ([asked_for] if asked_for else POSITIONS):
        told = cc.explain(name, layout, out_h=out_h)
        if told["chosen"] is not None and abs(told["chosen"] - y_pct) < 5e-4:
            hits.append(name)
    return hits


def _in_window(t: float, lo: float, hi: float, *, last: bool) -> bool:
    """Whether a sample at `t` belongs to a window `[lo, hi)`.

    HALF-OPEN, because a closed window counted a sample landing exactly on a cut
    for both the shot that ended and the shot that began — 20 of them in the
    corpus. The frame at that timestamp is the one the new shot shows. The
    clip's last shot keeps its endpoint: nothing follows it to take the sample.
    """
    return lo <= t <= hi if last else lo <= t < hi


def _face_evidence(analysis: Path, side: dict, plan: dict,
                   shots: list) -> tuple[list[dict | None], dict]:
    """Per-shot face boxes in OUTPUT pixels, and what could not be answered.

    `None` for a shot means UNAVAILABLE, and it is the common case rather than
    the exception: the detector samples about every two seconds and a shot is
    typically one to four, so most shots contain no sample at all. Reporting an
    empty list there would say "we looked at this shot and there was no face".
    """
    stats = {"shots": len(shots), "no_sample": 0, "mapped": 0,
             "refused": [], "off_frame": 0, "unreadable_inputs": None,
             "no_source_start": False, "multi_sample": 0}
    try:
        signals = json.loads((analysis / "signals.json").read_text(encoding="utf-8"))
        faces = json.loads((analysis / "faces.json").read_text(encoding="utf-8"))
    except Exception as exc:
        # NOT "no faces". The inputs could not be read, which is a different
        # answer and the one this whole plan exists to keep separate.
        stats["unreadable_inputs"] = type(exc).__name__
        return [None] * len(shots), stats

    proxy_w = (signals or {}).get("proxy_width") if isinstance(signals, dict) else None
    proxy_h = (signals or {}).get("proxy_height") if isinstance(signals, dict) else None
    samples = (faces or {}).get("samples") if isinstance(faces, dict) else None
    if not isinstance(samples, list):
        stats["unreadable_inputs"] = "faces_samples_not_a_list"
        return [None] * len(shots), stats

    # The detector's `t` is SOURCE time — it spans the whole video, not the
    # clip — so a shot's window is the clip's own start plus the shot's offset.
    start = side.get("source_start")
    if not isinstance(start, (int, float)) or isinstance(start, bool):
        # NOT the same fact as unreadable analysis files. Those are a corpus
        # problem and void the face column for everyone; this is one sidecar
        # that cannot say where in the source it came from, so its own face
        # column is unavailable and nobody else's is affected.
        stats["no_source_start"] = True
        return [None] * len(shots), stats

    style = plan.get("style") if isinstance(plan.get("style"), dict) else {}
    src_w, src_h = plan.get("src_w") or 0, plan.get("src_h") or 0

    out: list[dict | None] = []
    for index, shot in enumerate(shots):
        last = index == len(shots) - 1
        if not isinstance(shot, dict):
            out.append(None)
            continue
        t0, t1 = shot.get("t0"), shot.get("t1")
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                   for v in (t0, t1)):
            out.append(None)
            stats["no_sample"] += 1
            continue
        lo, hi = float(start) + float(t0), float(start) + float(t1)
        inside = [s for s in samples
                  if isinstance(s, dict)
                  and isinstance(s.get("t"), (int, float))
                  and not isinstance(s.get("t"), bool)
                  and _in_window(float(s["t"]), lo, hi, last=last)]
        # EVERY SAMPLE'S BOXES IN ONE LIST, which answers "was the caption over
        # a face at ANY point in this shot" rather than "throughout it". The
        # conservative direction for a warning, and the wrong one for a claim of
        # cleanliness — so the count of shots this applies to is reported.
        boxes = [b for s in inside for b in (s.get("boxes") or [])]
        if len(inside) > 1:
            stats["multi_sample"] += 1
        if not inside:
            # NO SAMPLE IN THE WINDOW. Not "no face in this shot".
            out.append(None)
            stats["no_sample"] += 1
            continue
        got = em.shot_evidence(shot, boxes_by_signal={"faces": boxes},
                               proxy_w=proxy_w or 0, proxy_h=proxy_h or 0,
                               src_w=src_w, src_h=src_h, style=style)
        stats["refused"].extend(got["refused"])
        stats["off_frame"] += got["off_frame"].get("faces", 0)
        mapped = got["evidence"]["faces"]
        if mapped is not None:
            stats["mapped"] += 1
        # The UI and source-text signals have no per-shot detection at all, so
        # they stay `None` — `unavailable`, never an empty list.
        out.append({"faces": mapped, "panels": None, "text": None})
    stats["refused"] = sorted(set(stats["refused"]))
    return out, stats


def measure(path: Path) -> dict:
    row: dict = {"project": path.parent.parent.name, "clip": path.stem}
    try:
        side = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        # A REFUSAL WITH ITS OWN ROW. A sidecar that cannot be read must never
        # drop out of the corpus; it makes every figure below a fraction of a
        # smaller thing than the header claims.
        row["refused"] = f"sidecar_unreadable: {type(exc).__name__}"
        return row
    if not isinstance(side, dict):
        row["refused"] = "sidecar_not_a_record"
        return row

    # A SUB-RECORD THAT IS NOT A RECORD IS A REFUSAL. `side.get("dynamic_plan")
    # or {}` returns the list itself for `[1]`, and `.get` on a list raises —
    # out of a loop over the corpus, so the audit reports on the part before the
    # crash and never says it crashed.
    parts = {}
    for key in ("dynamic_plan", "caption_plan", "layout_plan"):
        value = side.get(key)
        if value is None:
            parts[key] = {}
        elif isinstance(value, dict):
            parts[key] = value
        else:
            row["refused"] = f"{key}_not_a_record"
            return row
    plan, caption, layout = (parts["dynamic_plan"], parts["caption_plan"],
                             parts["layout_plan"])
    # THE DELIVERED POSITION, from the file that burned it. `y_pct` is the
    # preset the plan asked for and differs from the `.ass` on 46 of 99 clips.
    planned = caption.get("y_pct")
    burned, source = _burned_y_pct(path.with_suffix(".ass"), 1920)
    y_pct = burned if burned is not None else planned
    row["caption_y_source"] = source if burned is not None else "caption_plan"
    row["caption_y_planned"] = planned
    row["caption_y_why_not_ass"] = None if burned is not None else source

    # THE RENDERED HEIGHT, READ RATHER THAN ASSUMED. 1920 was hardcoded, which
    # is a second source of truth about output geometry. When the file is not
    # there the fallback is DECLARED rather than silent, and it fails the run:
    # the letterbox column rests on it, and nothing else in the row does.
    out_h, assumed = _rendered_height(path.with_suffix(".mp4"))
    row["out_h"] = out_h
    row["output_geometry_assumed"] = assumed

    shots = plan.get("shots")
    # THE ANALYSIS DIRECTORY IS A SIBLING OF `exports/` BY CONSTRUCTION, so it
    # is derived from the path rather than from a second `CLIPFORGE_DATA_DIR`
    # read at import time. The two constants disagreed the moment a test pointed
    # the environment somewhere else: the script saw the temp corpus and this
    # module saw the real one.
    evidence, faces = _face_evidence(
        path.parent.parent / "analysis", side, plan,
        shots if isinstance(shots, list) else [])
    row["faces"] = faces

    view = cp.placement_view(
        y_pct=y_pct,
        shots=shots,
        # THE FACE SIGNAL IS MAPPED; the other two have no per-shot detection,
        # so they stay `unavailable` and not zero. Passing empty lists would
        # report "the caption covers no UI" out of a run in which nobody looked.
        evidence=evidence or None,
        out_h=out_h,
        src_w=plan.get("src_w") or 0,
        src_h=plan.get("src_h") or 0)

    told = cc.explain("bottom", layout, out_h=out_h)
    row.update({
        "y_pct": y_pct,
        "shots": len(plan.get("shots") or []) if isinstance(
            plan.get("shots"), list) else None,
        "fit_shots": sum(1 for s in (plan.get("shots") or [])
                         if isinstance(s, dict) and s.get("composition") == "fit"),
        "moving_crops": _moving_crops(plan),
        "on_letterbox": cp.ON_LETTERBOX in view["lands_on"],
        "on_face": cp.ON_FACE in view["lands_on"],
        "worst_share": (view["worst"] or {}).get("share"),
        "worst_share_complete": view.get("worst_share_complete"),
        "placement_refused": view["refused"],
        "placement_unavailable": view["unavailable"],
        "keep_out": told["keep_out"],
        "choice_refused": told["refused"],
        # THE POSITION THE CLIP ASKED FOR, when it recorded one. Trying all
        # four presets and accepting any match would let a position produced by
        # accident from a preset nobody chose count as an explanation. Every one
        # of the 99 styles on disk says `bottom`, so today's figure does not
        # move — but a mixed corpus is exactly where the weaker check would
        # start passing things.
        "asked_for": _requested_position(caption),
        # UNANSWERABLE FROM A SIDECAR, and named rather than guessed at. See
        # `NOT_REPRODUCIBLE`.
        "position_reproducible": False,
        "why_not_reproducible": NOT_REPRODUCIBLE,
        "caption_y_moved_from_the_preset": (
            None if not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                            for v in (burned, planned))
            else abs(float(burned) - float(planned)) > 5e-4),
        # Kept as evidence, NOT as a verdict: it is what today's rule gives on
        # the keep-outs that survived, which is not the set the caption was
        # placed against.
        "explained_by_the_stored_keep_outs": (
            _explained_by(float(y_pct), layout, out_h,
                          _requested_position(caption))
            if isinstance(y_pct, (int, float))
            and not isinstance(y_pct, bool) else None),
        # AND WHETHER THE STORED POSITION SITS ON SOMETHING. For a clip today's
        # rule would not produce, this is the whole question: was the old rule
        # merely different, or was it wrong?
        "stored_covers": _coverage(y_pct, layout, out_h),
    })
    return row


def _coverage(y_pct, layout: dict, out_h: int) -> float | None:
    """How much keep-out the STORED position overlaps, or None if unmeasurable."""
    from services.clipper.captions import _iter_rects, _norm_rect, _overlap_area

    if not isinstance(y_pct, (int, float)) or isinstance(y_pct, bool):
        return None
    rects = [r for r in (_norm_rect(rc, 1080, out_h)
                         for rc in _iter_rects((layout or {}).get("safe_zones")))
             if r is not None]
    return round(_overlap_area(float(y_pct), rects), 6)
