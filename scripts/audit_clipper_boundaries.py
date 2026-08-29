"""Batch R5's gate: are the chosen windows complete thoughts, across the corpus.

The same shape as `audit_clipper_exports.py`, and for the same reason — a gate
nobody can rerun is a gate that was passed once. This reads the `boundary_view`
each build now records on every candidate and aggregates it; it re-measures
nothing, so the audit and the pipeline cannot disagree about what a defect is.

    python scripts/audit_clipper_boundaries.py pilotf81b pilotee0e
    python scripts/audit_clipper_boundaries.py --all --recompute
    python scripts/audit_clipper_boundaries.py --all --json

TWO MODES, AND THE DIFFERENCE MATTERS. By default it reads the verdict the
build recorded — which is what a gate should check, since it is the artefact
that ships. `--recompute` loads the transcript from the database and runs the
SAME `boundary_completion.attach` over the stored windows, which measures the
rule on a corpus scored before this batch existed WITHOUT re-scoring it and
without touching a score or a board. It is a measurement of the rule, not of the
pipeline: a green `--recompute` still leaves the end-to-end integration
unproven, and the report says so rather than letting one stand for the other.

AND THE WINDOWS ARE NOT ALL THE SAME AGE. `--recompute` runs TODAY'S rule over
windows produced by whatever the boundary code was when each project was scored,
so a pooled figure mixes vintages. Measured: `slice4h00test`'s artefact is from
2026-08-14 and `_keep_release` landed the next day in 4a136de, which is the
whole of its 880-of-920 `clipped_release` — the pad never ran for it. The four
pilots are from 2026-08-22 and carry no such excuse. Check the artefact's date
before reading a per-project number as a defect in the current code.

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
    BLOCKING, DEFECTS, REFUSALS, REPAIR_EXTEND, TECHNICAL, UNKNOWNS,
)

#: Every key `boundary_view_v1` promises. A view missing one is MALFORMED, not
#: a view with fewer findings: reading `view.get("defects") or []` off a record
#: that never carried the key counts a hole as a clean window, which is the one
#: conclusion this audit exists to prevent.
REQUIRED_KEYS = ("defects", "blocking", "unknown", "measurements", "repair",
                 "eligible")

MISSING = "missing_boundary_view"
MALFORMED = "malformed_boundary_view"
#: EVERY candidate in the project lacks the view. That is not the same finding:
#: it means the project was scored before R5 existed and needs a re-score, which
#: is a thing to do rather than a bug to chase. A project where only SOME are
#: missing is the bug — the record failed on the ones it failed on.
PREDATES = "predates_r5_rescore_needed"


def _recompute(project_id: str, rows: list[dict]) -> str | None:
    """Measure the rule on stored windows. Returns a refusal reason, or None.

    Read-only: it loads the transcript and the project's own clip bounds, runs
    the canonical function, and attaches the result to the in-memory rows. The
    artefact on disk is not rewritten — a script that edited `candidates.json`
    would be re-scoring by the back door.
    """
    import asyncio

    from config import settings
    from database import async_session
    from models import ProjectModel, TranscriptModel
    from sqlalchemy import select

    from services.clipper import boundary_completion
    from services.clipper.candidate_terms import _words_for

    async def _load() -> tuple[dict | None, float, float]:
        async with async_session() as session:
            row = (await session.execute(
                select(TranscriptModel)
                .where(TranscriptModel.project_id == project_id).limit(1)
            )).scalar_one_or_none()
            project = await session.get(ProjectModel, project_id)
        if not row or not row.segments:
            return None, 0.0, 0.0
        cfg = (project.clipper_settings if project else None) or {}
        return ({"language": row.language, "segments": row.segments},
                float(cfg.get("max_clip_s") or settings.clipper_max_clip_s),
                float((project.duration if project else 0) or 0.0))

    transcript, max_s, duration = asyncio.run(_load())
    if not transcript:
        return "no_transcript_in_db"
    words = _words_for({}, transcript)
    if not words:
        return "transcript_has_no_word_times"
    boundary_completion.attach(rows, words, max_s=max_s,
                               duration=duration or None)
    return None


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


def _well_formed(view: dict) -> bool:
    """Whether the record says everything `boundary_view_v1` promises to say.

    Checked BEFORE anything is counted. `view.get("defects") or []` on a record
    that never carried the key reads as a clean window, and a corpus of holes
    then aggregates into a pass — the exact conclusion R0 spent a session
    learning to refuse.
    """
    if view.get("schema") != "boundary_view_v1":
        return False
    if any(key not in view for key in REQUIRED_KEYS):
        return False
    if not isinstance(view["measurements"], dict):
        return False
    if view["eligible"] not in (True, False, None):
        return False
    for key in ("defects", "blocking", "unknown"):
        if not isinstance(view[key], list):
            return False
    # EACH LIST AGAINST ITS OWN VOCABULARY, not against the union. Checking the
    # union let an unknown appear as a defect and a defect as an unknown, which
    # is the one distinction the whole batch is built on.
    if not all(isinstance(n, str) and n in DEFECTS for n in view["defects"]):
        return False
    if not all(isinstance(n, str) and n in UNKNOWNS for n in view["unknown"]):
        return False
    # `blocking` is a SUBSET of what was found. A record claiming a blocking
    # defect it does not list is a record that cannot be reconciled with itself.
    if not set(view["blocking"]) <= set(view["defects"]):
        return False
    repair = view["repair"]
    if not isinstance(repair, dict) or "kind" not in repair:
        return False
    if repair["kind"] is None:
        if repair.get("refused") not in REFUSALS:
            return False
    elif repair["kind"] != REPAIR_EXTEND:
        return False
    # A measurement is a number or an honest absence — never a string that
    # would sort, compare and average as if it meant something.
    return all(v is None or (isinstance(v, (int, float))
                             and not isinstance(v, bool))
               for v in view["measurements"].values())


def _percentile(ordered: list[float], q: float) -> float | None:
    if not ordered:
        return None
    return round(ordered[min(len(ordered) - 1,
                             max(0, int(q * (len(ordered) - 1))))], 3)


def _measure(project_id: str, *, recompute: bool = False) -> dict:
    """One project's tally. Every count is of something, never of an absence."""
    rows = _candidates(project_id)
    if rows is None:
        return {"project": project_id, "candidates": None,
                "integrity": ["no_candidates_artefact"]}
    recomputed = False
    if recompute:
        refused = _recompute(project_id, rows)
        if refused:
            return {"project": project_id, "candidates": len(rows),
                    "integrity": [refused]}
        recomputed = True

    out: dict = {
        "project": project_id,
        "candidates": len(rows),
        "defects": {name: 0 for name in DEFECTS},
        "unknown": {name: 0 for name in UNKNOWNS},
        "eligible": 0, "ineligible": 0, "undecidable": 0,
        "repairable": 0,
        "integrity": [],
        "tails": [],
        # Said out loud on every row: a number measured from the stored windows
        # is not the same evidence as one the pipeline actually wrote.
        "source": "recomputed" if recomputed else "recorded",
    }
    out["refused"] = {name: 0 for name in REFUSALS}
    out["technical"] = 0
    for cand in rows:
        view = cand.get("boundary_view")
        if not isinstance(view, dict):
            out["integrity"].append(MISSING)
            continue
        if not _well_formed(view):
            out["integrity"].append(MALFORMED)
            continue
        for name in view["defects"]:
            out["defects"][name] += 1
        for name in view["unknown"]:
            out["unknown"][name] += 1
        if [d for d in view["defects"] if d in TECHNICAL]:
            out["technical"] += 1
        eligible = view["eligible"]
        if eligible is True:
            out["eligible"] += 1
        elif eligible is False:
            out["ineligible"] += 1
        else:
            out["undecidable"] += 1
        repair = view["repair"]
        if repair.get("kind"):
            out["repairable"] += 1
        elif repair.get("refused") in out["refused"]:
            out["refused"][repair["refused"]] += 1
        tail = view["measurements"].get("tail_s")
        if isinstance(tail, (int, float)) and not isinstance(tail, bool):
            out["tails"].append(float(tail))
    return out


def _report(row: dict) -> None:
    name = row["project"]
    if row.get("candidates") is None:
        print(f"{name:12} no candidates.json — nothing to audit")
        return
    total = row["candidates"]
    judged = row["eligible"] + row["ineligible"] + row["undecidable"]
    print(f"{name:12} {judged:4} of {total} judged ({row.get('source', 'recorded')})   "
          f"eligible {row['eligible']}  ineligible {row['ineligible']}  "
          f"undecidable {row['undecidable']}")
    # THE NUMBERS HAVE TO ADD UP ON THE ROW. A candidate the audit could not
    # read was skipped before it reached any of the three counts, so a project
    # with fifty unreadable records printed a verdict for the other fifty and
    # the missing half simply was not there.
    if judged != total:
        print(f"{'':12} {total - judged} not measured at all — see the "
              f"integrity line below, and do not read the counts above as a "
              f"share of {total}")
    if row.get("technical"):
        print(f"{'':12} technical (a render pad fixes, R7 decides if it blocks): "
              f"{row['technical']}")
    refused = {k: v for k, v in (row.get("refused") or {}).items() if v}
    if refused:
        print(f"{'':12} repairs refused: "
              + ", ".join(f"{k} {v}" for k, v in sorted(refused.items(),
                                                        key=lambda kv: -kv[1])))
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
    ap.add_argument("--recompute", action="store_true",
                    help="measure the rule on stored windows using the "
                         "transcript from the database, without re-scoring")
    args = ap.parse_args()

    names = list(args.projects)
    if args.all or not names:
        names = sorted(p.name for p in DATA.glob("*") if p.is_dir())
    if not names:
        print(f"no projects under {DATA}")
        return 2

    rows = [_measure(name, recompute=args.recompute) for name in names]
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
                  f"which is a different thing from being passed. "
                  f"`--recompute` measures the RULE on their stored windows; it "
                  f"does not measure the pipeline.")
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
