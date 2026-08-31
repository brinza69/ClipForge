"""Batch R6: where the delivered captions actually sit, across every export.

`caption_placement` and `caption_choice` were built against fixtures. This runs
them over the sidecars on disk — the same 101 exports the R0 audit reads — and
asks the two questions the batch exists to answer:

    can the delivered position be EXPLAINED at all, and does the caption land
    on the letterbox band?

WHY THE SECOND QUESTION IS THE ONLY ONE WITH AN ANSWER HERE. The face, UI and
source-text evidence has to be MAPPED into the output frame per shot, and
nothing maps it yet — `panels_to_keep_out` deliberately skips face shots, so
there is no mapper for the faces and none for the source text. Those three
signals therefore come back `unavailable`, which is what they are. The letterbox
needs only geometry, so it is measured.

    python scripts/audit_caption_placement.py
    python scripts/audit_caption_placement.py --json
    python scripts/audit_caption_placement.py --project pilotf81b

WHAT FAILS THE RUN, and each of these is a thing that would VOID the report
rather than merely be a bad result:

    a project asked for that is not there   the request vanished from the
                                            denominator, and the run reads as a
                                            pass over the ones that were found
    a sidecar that cannot be read           the corpus is smaller than it looks
    a sub-record that is not a record       `.get` on a list raises, and an
                                            audit that dies mid-corpus reports
                                            on the part before the crash
    a shot the placement report refused     it was not measured, so it is not
                                            clean
    a keep-out rectangle nobody could read  it was dropped by the placement rule
                                            itself, which is the same hole as a
                                            non-finite one
    a stored y_pct no position reproduces   the export was made by a DIFFERENT
                                            placement rule, so nothing about
                                            it is evidence about today's
    a keep-out rectangle that is not finite it moves the delivered caption and
                                            nothing says so
    an assumed output height                the letterbox column rests on it
    an empty corpus                         a pass over nothing is not a pass

The denominator is printed BEFORE every count, and a refusal gets its own row.
Both rules are in `CLAUDE.md` because this plan has broken them seven times.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))

DATA = Path(os.environ.get("CLIPFORGE_DATA_DIR") or (_ROOT / "data")) / "clipper"

from services.clipper import caption_choice as cc  # noqa: E402
from services.clipper import caption_placement as cp  # noqa: E402

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


def _requested_position(caption: dict) -> str | None:
    """The position name the clip's own style asked for, if it recorded one."""
    import ast

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


def _measure(path: Path) -> dict:
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
    y_pct = caption.get("y_pct")

    # THE RENDERED HEIGHT, READ RATHER THAN ASSUMED. 1920 was hardcoded, which
    # is a second source of truth about output geometry. When the file is not
    # there the fallback is DECLARED rather than silent, and it fails the run:
    # the letterbox column rests on it, and nothing else in the row does.
    out_h, assumed = _rendered_height(path.with_suffix(".mp4"))
    row["out_h"] = out_h
    row["output_geometry_assumed"] = assumed

    view = cp.placement_view(
        y_pct=y_pct,
        shots=plan.get("shots"),
        # NOTHING MAPS THE OTHER THREE SIGNALS YET, so they are unavailable and
        # not zero. Passing empty lists here would report "the caption covers no
        # faces" out of a run in which nobody looked for one.
        evidence=None,
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
        "on_letterbox": cp.ON_LETTERBOX in view["lands_on"],
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
        "explained_by": (_explained_by(float(y_pct), layout, out_h,
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


def _report(rows: list[dict]) -> dict:
    """The figures, with the denominator computed before any of the counts."""
    total = len(rows)
    refused = [r for r in rows if r.get("refused")]
    read = [r for r in rows if not r.get("refused")]

    # THE SECOND DENOMINATOR, and it is not the first. A clip with no caption
    # plan cannot land on the letterbox or fail to be explained; folding it into
    # the same fraction as the ones that could would make the rate look better
    # for a reason that has nothing to do with placement.
    placed = [r for r in read if isinstance(r.get("y_pct"), (int, float))
              and not isinstance(r.get("y_pct"), bool)]
    geometric = [r for r in placed
                 if cp.NO_GEOMETRY not in r["placement_unavailable"]
                 and r.get("fit_shots")]

    unexplained = [r for r in placed if not r["explained_by"]]
    not_finite = [r for r in read if (r.get("keep_out") or {}).get("not_finite")]
    unreadable_rects = [r for r in read
                        if (r.get("keep_out") or {}).get("unreadable")]
    on_letterbox = [r for r in geometric if r["on_letterbox"]]

    return {
        "sidecars": total,
        "refused": len(refused),
        "refusals": [f"{r['project']}/{r['clip']}: {r['refused']}"
                     for r in refused],
        "read": len(read),
        "with_a_caption_position": len(placed),
        # AND THE ONES WITHOUT, under their own name. An export can genuinely
        # carry no captions, and a sidecar can have lost the plan, and nothing
        # here can tell the two apart — so this is `unavailable` rather than a
        # failure, and it is printed so "99 of 99 explained" is never read as a
        # statement about 101 renders.
        "without_a_caption_position": len(read) - len(placed),
        "with_a_fit_shot_and_known_geometry": len(geometric),
        "caption_on_the_letterbox": len(on_letterbox),
        "position_no_preset_reproduces": len(unexplained),
        "unexplained": [f"{r['project']}/{r['clip']} y={r['y_pct']} "
                        f"covers {r['stored_covers']} of the keep-out"
                        for r in unexplained],
        "unexplained_and_covered": len(
            [r for r in unexplained if (r["stored_covers"] or 0) > 0]),
        "with_a_non_finite_keep_out": len(not_finite),
        "with_an_unreadable_keep_out": len(unreadable_rects),
        # COMPUTED AND WIRED, both of them. A shot the report refused was not
        # measured, and an unmeasured shot has never been a clean one anywhere
        # else in this plan.
        "placement_refusals": len([r for r in read if r.get("placement_refused")]),
        "choice_refusals": len([r for r in read if r.get("choice_refused")]),
        "output_geometry_assumed": len([r for r in read
                                        if r.get("output_geometry_assumed")]),
    }


def _print(out: dict) -> None:
    print(f"sidecars found                        {out['sidecars']}")
    # THE REFUSAL ROW IS ALWAYS PRINTED, including when it is zero. A row that
    # only appears when it is non-zero teaches the reader that its absence means
    # nothing went wrong, which is exactly the inference it cannot support.
    print(f"  refused                             {out['refused']}")
    for line in out["refusals"]:
        print(f"    {line}")
    print(f"  read                                {out['read']}")
    print(f"of those, with a caption position     {out['with_a_caption_position']}")
    print(f"  without one (unavailable, not a fail) "
          f"{out['without_a_caption_position']}")
    print(f"  today's rule would not produce it   "
          f"{out['position_no_preset_reproduces']}")
    print(f"    ...and it sits on a keep-out      "
          f"{out['unexplained_and_covered']}")
    for line in out["unexplained"][:20]:
        print(f"    {line}")
    print(f"of those, `fit` + known geometry      "
          f"{out['with_a_fit_shot_and_known_geometry']}")
    print(f"  caption lands on the letterbox      "
          f"{out['caption_on_the_letterbox']}")
    print(f"of the sidecars read")
    print(f"  the placement report refused        "
          f"{out['placement_refusals']}")
    print(f"  the explanation refused             {out['choice_refusals']}")
    print(f"  rendered height assumed, not read   "
          f"{out['output_geometry_assumed']}")
    print(f"of the sidecars read, keep-out rects")
    print(f"  with a non-finite rectangle         "
          f"{out['with_a_non_finite_keep_out']}")
    print(f"  with an unreadable rectangle        "
          f"{out['with_an_unreadable_keep_out']}")


def _failures(out: dict) -> list[str]:
    """What VOIDS the report, as opposed to what is merely a bad result.

    Every one of these has to reach the exit code. `changed_without_moving` was
    computed, printed, and wired to nothing in an earlier script in this plan —
    the instrument found the single fact that would void its own answer and
    returned 0.
    """
    bad = []
    for name in out.get("projects_not_found") or []:
        bad.append(f"project asked for and not found: {name}")
    if not out["sidecars"]:
        bad.append("no sidecars found — a pass over nothing is not a pass")
    if out["refused"]:
        bad.append(f"{out['refused']} sidecar(s) could not be read")
    if out["position_no_preset_reproduces"]:
        bad.append(f"{out['position_no_preset_reproduces']} caption position(s) "
                   "today's rule would not produce — those exports were burned "
                   "by a different rule and are evidence about neither")
    if out["with_a_non_finite_keep_out"]:
        bad.append(f"{out['with_a_non_finite_keep_out']} clip(s) have a "
                   "non-finite keep-out rectangle, which moves the delivered "
                   "caption")
    # PRINTED AND WIRED. A rectangle the placement rule threw away without a
    # word invalidates the explanation exactly as a non-finite one does: the
    # caption was positioned over geometry nobody could read. Computing a
    # diagnostic, printing it and leaving it out of the exit code is the failure
    # `changed_without_moving` already committed once in this plan.
    if out["with_an_unreadable_keep_out"]:
        bad.append(f"{out['with_an_unreadable_keep_out']} clip(s) have a "
                   "keep-out rectangle the placement rule dropped without a "
                   "word")
    if out["placement_refusals"]:
        bad.append(f"{out['placement_refusals']} clip(s) the placement report "
                   "refused — a shot it could not measure is not a clean one")
    if out["choice_refusals"]:
        bad.append(f"{out['choice_refusals']} clip(s) the placement EXPLANATION "
                   "refused")
    if out["output_geometry_assumed"]:
        bad.append(f"{out['output_geometry_assumed']} clip(s) whose rendered "
                   "height could not be measured, so the letterbox answer "
                   "rests on an assumed 1920")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", action="append", default=[])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    paths = sorted(DATA.glob("*/exports/*.json"))
    # A PROJECT ASKED FOR AND NOT FOUND IS A REFUSAL, not a smaller corpus.
    # Filtering silently made `--project pilotf81b --project typo` identical to
    # `--project pilotf81b`, exit 0 — the request disappeared from the
    # denominator and the run read as a pass over what happened to be there.
    missing: list[str] = []
    if args.project:
        wanted = list(dict.fromkeys(args.project))
        present = {p.parent.parent.name for p in paths}
        missing = [name for name in wanted if name not in present]
        paths = [p for p in paths if p.parent.parent.name in set(wanted)]

    rows = [_measure(p) for p in paths]
    out = _report(rows)
    out["projects_not_found"] = missing
    bad = _failures(out)
    out["failures"] = bad

    if args.json:
        print(json.dumps(out, indent=1))
    else:
        _print(out)
        print()
        for line in bad:
            print(f"FAIL: {line}")
        if not bad:
            print("no result that would void this report")
    # THE EXIT CODE BELONGS TO THE RUN, not to how it was printed. `--json`
    # returning 0 unconditionally is on the list in `CLAUDE.md` because it
    # happened.
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
