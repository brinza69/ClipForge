"""Batch R6: where the delivered captions actually sit, across every export.

`caption_placement` and `caption_choice` were built against fixtures. This runs
them over the sidecars on disk — the same 101 exports the R0 audit reads — and
asks the two questions the batch exists to answer:

    can the delivered position be EXPLAINED at all, and does the caption land
    on the letterbox band?

AND SINCE `evidence_map` EXISTS, THE FACE SIGNAL IS ANSWERED TOO. It maps the
detector's proxy-pixel boxes through the renderer's own chain — proxy to source,
pad, crop, scale — and refuses a shot whose crop moves rather than approximating
it. The UI and source-text signals stay `unavailable`: `regions` are detected
once for the whole source rather than per shot, and nothing detects the source's
own text at all.

THE SAMPLING IS SPARSER THAN THE SHOTS, and that decides most of the answer. The
face detector runs on a grid of about one sample every two seconds while a shot
is typically one to four; a shot with no sample inside it has no face evidence,
which is `unavailable` and not "no face". The count of those is printed, because
it is the denominator of everything the face column says.

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
    (a position that cannot be checked is NOT one of these: the keep-out set it
     was resolved against is not on the sidecar, so no run will ever answer it.
     A permanently red gate for a property of the format buries the findings
     that are about clips. It is printed, loudly, and left out of the verdict.)
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

from services.clipper import caption_placement as cp  # noqa: E402
from services.clipper.caption_corpus import measure  # noqa: E402

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

    # NOT A COUNT OF ANYTHING. The keep-out set the caption was placed against
    # is not stored, so no sidecar can answer whether today's rule would produce
    # its position. Two earlier versions of this line answered it anyway and
    # gave 8 and then 54, neither a fact about the rule.
    unreproducible = [r for r in placed if r.get("position_reproducible") is False]
    disagrees = [r for r in placed if not r["explained_by_the_stored_keep_outs"]]
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
        "position_not_reproducible": len(unreproducible),
        "why_not_reproducible": (unreproducible[0]["why_not_reproducible"]
                                 if unreproducible else None),
        # Evidence, not a verdict: what today's rule gives on the keep-outs that
        # survived into the sidecar.
        "differs_from_todays_rule_on_the_stored_keep_outs": len(disagrees),
        "with_a_non_finite_keep_out": len(not_finite),
        "with_an_unreadable_keep_out": len(unreadable_rects),
        # COMPUTED AND WIRED, both of them. A shot the report refused was not
        # measured, and an unmeasured shot has never been a clean one anywhere
        # else in this plan.
        "shots_with_a_multi_point_size_timeline": sum(
            r.get("moving_crops") or 0 for r in read),
        # THE FACE COLUMN AND ITS OWN DENOMINATORS.
        "shots_total": sum((r.get("faces") or {}).get("shots", 0) for r in read),
        "shots_with_no_face_sample": sum(
            (r.get("faces") or {}).get("no_sample", 0) for r in read),
        "shots_with_mapped_faces": sum(
            (r.get("faces") or {}).get("mapped", 0) for r in read),
        # WHERE THE ANSWER IS A UNION OVER TIME. More than one sample in a
        # shot's window means the report says "at any point in this shot", not
        # "throughout it".
        "shots_with_more_than_one_sample": sum(
            (r.get("faces") or {}).get("multi_sample", 0) for r in read),
        "face_boxes_off_frame": sum(
            (r.get("faces") or {}).get("off_frame", 0) for r in read),
        "clips_with_unreadable_face_inputs": len(
            [r for r in read if (r.get("faces") or {}).get("unreadable_inputs")]),
        "clips_with_no_source_start": len(
            [r for r in read if (r.get("faces") or {}).get("no_source_start")]),
        "map_refusals": sorted({why for r in read
                                for why in (r.get("faces") or {}).get("refused", [])}),
        "caption_on_a_face": len([r for r in placed if r.get("on_face")]),
        # AND WHETHER ANY OF IT IS ESTABLISHED. `worst_share_complete` is true
        # only when every share that took part is a measurement, and two of the
        # three occlusion signals are never measured at all — so this is 0 by
        # construction today, and printing it is the only thing that stops
        # "26 clips" being read as "26 clips, and the other 73 are clean".
        "worst_case_fully_measured": len(
            [r for r in placed if r.get("worst_share_complete") is True]),
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
    print(f"  whose position can be checked against")
    print(f"    today's rule                      "
          f"{out['with_a_caption_position'] - out['position_not_reproducible']}")
    if out["why_not_reproducible"]:
        print(f"    the rest cannot: {out['why_not_reproducible']}")
    print(f"    (on the stored keep-outs alone, "
          f"{out['differs_from_todays_rule_on_the_stored_keep_outs']} differ —")
    print(f"     evidence about an incomplete input, not about the rule)")
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
    # PRINTED AND NOT WIRED, deliberately, and the difference from the rule
    # matters: nothing in this report depends on it. It is the precondition for
    # the evidence mapper that does not exist yet, and the day it stops being
    # zero, that mapper cannot be built on `shot["rect"]`.
    # NAMED FOR WHAT IT CHECKS. "shots whose crop moves" claimed more: a
    # single-point timeline proves constant SIZE, not that the delivered window
    # equals `shot["rect"]` — 837 of the corpus's 1,965 crops differ from it.
    print(f"  shots with a multi-point size")
    print(f"    timeline (constant size is not the")
    print(f"    same as the planner's rectangle)   "
          f"{out['shots_with_a_multi_point_size_timeline']}")
    print(f"the face signal, per shot")
    print(f"  shots                               {out['shots_total']}")
    print(f"    with no sample in their window    "
          f"{out['shots_with_no_face_sample']}")
    print(f"    with faces mapped into the output {out['shots_with_mapped_faces']}")
    print(f"    with MORE than one sample, so the")
    print(f"      answer is \"at any point\"          "
          f"{out['shots_with_more_than_one_sample']}")
    print(f"  face boxes that miss the crop       {out['face_boxes_off_frame']}")
    print(f"  clips whose face inputs are unreadable "
          f"{out['clips_with_unreadable_face_inputs']}")
    print(f"  clips that do not say where in the")
    print(f"    source they came from             "
          f"{out['clips_with_no_source_start']}")
    for why in out["map_refusals"]:
        print(f"    map refused: {why}")
    print(f"  clips whose caption lands on a face {out['caption_on_a_face']}")
    print(f"    ^ A FLOOR, on every axis: {out['shots_with_no_face_sample']} "
          f"shots have no sample,")
    print(f"      and the UI and source-text signals are never measured.")
    print(f"  clips whose worst case is established "
          f"{out['worst_case_fully_measured']}")
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
    # NOT A FAILURE, and the distinction is the same one the missing caption
    # plan gets: this is a property of the stored FORMAT, not of any clip. The
    # sidecar does not record the keep-out set the caption was placed against,
    # so no run of this script will ever answer the question — and failing 99
    # clips for it every time would bury the clip-level findings under a
    # permanently red gate. It is printed at the top instead.
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
    if out["clips_with_unreadable_face_inputs"]:
        bad.append(f"{out['clips_with_unreadable_face_inputs']} clip(s) whose "
                   "face inputs could not be read — that is not 'no faces'")
    if out["map_refusals"]:
        bad.append("the evidence mapper refused: "
                   + ", ".join(out["map_refusals"]))
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

    rows = [measure(p) for p in paths]
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
