"""Batch R5's gate: are the chosen windows complete thoughts, across the corpus.

The same shape as `audit_clipper_exports.py`, and for the same reason — a gate
nobody can rerun is a gate that was passed once. This reads the `boundary_view`
each build now records on every candidate and aggregates it; it re-measures
nothing, so the audit and the pipeline cannot disagree about what a defect is.

    python scripts/audit_clipper_boundaries.py pilotf81b pilotee0e
    python scripts/audit_clipper_boundaries.py --all --json

WHAT IT REFUSES TO AVERAGE. A candidate with no `boundary_view` is `missing`,
not "clean": the field is written by the build, and its absence means the
project predates R5 or the record failed, neither of which is evidence about
the window. Those are counted, named, and make the run exit 2 — the same rule
R0 settled on after a missing sidecar spent a session being read as a pass.

WHAT IT IS FOR, beyond the count. `TAIL_PAD_S = 0.40` is inherited from one
source's median 0.16s and p90 0.40s. This prints the corpus distribution of the
tail each window actually got, so the constant can be re-derived from the
material rather than kept because it is already written down. The plan asks for
the values to be calibrated, not hardcoded from the old PRP; this is the
measurement that calibration would start from.
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

from services.clipper.boundary_completion import (  # noqa: E402
    BLOCKING, DEFECTS, UNKNOWNS,
)

MISSING = "missing_boundary_view"
MALFORMED = "malformed_boundary_view"
#: EVERY candidate in the project lacks the view. That is not the same finding:
#: it means the project was scored before R5 existed and needs a re-score, which
#: is a thing to do rather than a bug to chase. A project where only SOME are
#: missing is the bug — the record failed on the ones it failed on.
PREDATES = "predates_r5_rescore_needed"


def _candidates(project_id: str) -> list[dict] | None:
    path = DATA / project_id / "analysis" / "candidates.json"
    if not path.exists():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if isinstance(loaded, list):
        return [c for c in loaded if isinstance(c, dict)]
    for key in ("candidates", "items"):
        if isinstance(loaded, dict) and isinstance(loaded.get(key), list):
            return [c for c in loaded[key] if isinstance(c, dict)]
    return None


def _percentile(ordered: list[float], q: float) -> float | None:
    if not ordered:
        return None
    return round(ordered[min(len(ordered) - 1,
                             max(0, int(q * (len(ordered) - 1))))], 3)


def _measure(project_id: str) -> dict:
    """One project's tally. Every count is of something, never of an absence."""
    rows = _candidates(project_id)
    if rows is None:
        return {"project": project_id, "candidates": None,
                "integrity": ["no_candidates_artefact"]}

    out: dict = {
        "project": project_id,
        "candidates": len(rows),
        "defects": {name: 0 for name in DEFECTS},
        "unknown": {name: 0 for name in UNKNOWNS},
        "eligible": 0, "ineligible": 0, "undecidable": 0,
        "repairable": 0,
        "integrity": [],
        "tails": [],
    }
    for cand in rows:
        view = cand.get("boundary_view")
        if not isinstance(view, dict):
            out["integrity"].append(MISSING)
            continue
        if view.get("schema") != "boundary_view_v1":
            out["integrity"].append(MALFORMED)
            continue
        for name in view.get("defects") or []:
            if name in out["defects"]:
                out["defects"][name] += 1
            else:
                out["integrity"].append(MALFORMED)
        for name in view.get("unknown") or []:
            if name in out["unknown"]:
                out["unknown"][name] += 1
        eligible = view.get("eligible")
        if eligible is True:
            out["eligible"] += 1
        elif eligible is False:
            out["ineligible"] += 1
        else:
            out["undecidable"] += 1
        if (view.get("repair") or {}).get("kind"):
            out["repairable"] += 1
        tail = (view.get("measurements") or {}).get("tail_s")
        if isinstance(tail, (int, float)):
            out["tails"].append(float(tail))
    return out


def _report(row: dict) -> None:
    name = row["project"]
    if row.get("candidates") is None:
        print(f"{name:12} no candidates.json — nothing to audit")
        return
    total = row["candidates"]
    print(f"{name:12} {total:4} candidates   "
          f"eligible {row['eligible']}  ineligible {row['ineligible']}  "
          f"undecidable {row['undecidable']}")
    named = {k: v for k, v in row["defects"].items() if v}
    if named:
        print(f"{'':12} defects: " + ", ".join(
            f"{k} {v}" + (" (blocking)" if k in BLOCKING else "")
            for k, v in sorted(named.items(), key=lambda kv: -kv[1])))
    unknown = {k: v for k, v in row["unknown"].items() if v}
    if unknown:
        print(f"{'':12} could not be checked: "
              + ", ".join(f"{k} {v}" for k, v in sorted(unknown.items())))
    tails = sorted(row["tails"])
    if tails:
        print(f"{'':12} tail after the last word: "
              f"median {_percentile(tails, 0.50)}s  p90 {_percentile(tails, 0.90)}s  "
              f"min {round(tails[0], 3)}s  (n={len(tails)})")
    else:
        print(f"{'':12} tail: unavailable — no window carried a measured one")
    if row["integrity"]:
        counts: dict[str, int] = {}
        for finding in row["integrity"]:
            counts[finding] = counts.get(finding, 0) + 1
        if counts.get(MISSING) == total:
            print(f"{'':12} {PREDATES}: scored before R5, re-score to measure it")
        else:
            print(f"{'':12} INTEGRITY: "
                  + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("projects", nargs="*", help="project ids to audit")
    ap.add_argument("--all", action="store_true",
                    help="every project under the data directory")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    names = list(args.projects)
    if args.all or not names:
        names = sorted(p.name for p in DATA.glob("*") if p.is_dir())
    if not names:
        print(f"no projects under {DATA}")
        return 2

    rows = [_measure(name) for name in names]
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for row in rows:
            _report(row)

        pooled_tails = sorted(t for row in rows for t in row.get("tails") or [])
        checked = sum(row.get("candidates") or 0 for row in rows)
        blocking = sum(row["defects"].get(name, 0)
                       for row in rows if row.get("defects") for name in BLOCKING)
        # "0 blocking defects" over candidates nobody checked reads as a pass,
        # and that is the shape of mistake this whole plan is written against.
        # The denominator comes first.
        judged = sum(row.get("eligible", 0) + row.get("ineligible", 0)
                     + row.get("undecidable", 0) for row in rows)
        print(f"\n{'POOLED':12} {judged} of {checked} candidates carried a verdict "
              f"over {len(rows)} project(s)")
        if judged:
            print(f"{'':12} {blocking} blocking defect(s)")
        stale = sum(1 for row in rows if row.get("candidates")
                    and row["integrity"].count(MISSING) == row["candidates"])
        if stale:
            print(f"{'':12} {stale} project(s) predate R5 and carry no verdict at "
                  f"all. Until they are re-scored the gate is NOT MEASURED, "
                  f"which is a different thing from being passed.")
        if pooled_tails:
            print(f"{'':12} tail median {_percentile(pooled_tails, 0.50)}s, "
                  f"p90 {_percentile(pooled_tails, 0.90)}s — the numbers "
                  f"TAIL_PAD_S would be calibrated from")

    # Exit 2 on anything the audit could not read, never on a defect it could.
    # A window with a real problem is the finding; a window it could not check
    # is a hole in the gate itself.
    broken = sum(1 for row in rows if row.get("integrity"))
    return 2 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
