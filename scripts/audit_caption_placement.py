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

    a sidecar that cannot be read           the corpus is smaller than it looks
    a stored y_pct no position reproduces   the export was made by a DIFFERENT
                                            placement rule, so nothing about
                                            it is evidence about today's
    a keep-out rectangle that is not finite it moves the delivered caption and
                                            nothing says so
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


def _explained_by(y_pct: float, layout: dict, out_h: int) -> list[str]:
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
    for name in POSITIONS:
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

    plan = side.get("dynamic_plan") or {}
    caption = side.get("caption_plan") or {}
    layout = side.get("layout_plan") or {}
    y_pct = caption.get("y_pct")

    view = cp.placement_view(
        y_pct=y_pct,
        shots=plan.get("shots"),
        # NOTHING MAPS THE OTHER THREE SIGNALS YET, so they are unavailable and
        # not zero. Passing empty lists here would report "the caption covers no
        # faces" out of a run in which nobody looked for one.
        evidence=None,
        out_h=1920,
        src_w=plan.get("src_w") or 0,
        src_h=plan.get("src_h") or 0)

    told = cc.explain("bottom", layout, out_h=1920)
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
        "explained_by": (_explained_by(float(y_pct), layout, 1920)
                         if isinstance(y_pct, (int, float))
                         and not isinstance(y_pct, bool) else None),
        # AND WHETHER THE STORED POSITION SITS ON SOMETHING. For a clip today's
        # rule would not produce, this is the whole question: was the old rule
        # merely different, or was it wrong?
        "stored_covers": _coverage(y_pct, layout, 1920),
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
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", action="append", default=[])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    paths = sorted(DATA.glob("*/exports/*.json"))
    if args.project:
        wanted = set(args.project)
        paths = [p for p in paths if p.parent.parent.name in wanted]

    rows = [_measure(p) for p in paths]
    out = _report(rows)
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
