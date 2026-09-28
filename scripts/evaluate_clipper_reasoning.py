"""Read the reasoning traces off disk and say what actually happened.

Run it before a change and after it. The point is not the numbers themselves —
it is that "this is better" stops being an assertion. Every figure the audit
that started Reasoning v2 had to reconstruct by hand from `candidates.json`
is printed here from artefacts the run itself wrote:

    how much of the field the judge ever saw
    how many story candidates reached it
    how the transcript was chunked, and whether the chunks cover the source
    which providers were tried, which failed, and what fell back
    which settings were actually in force, and how the run was launched

WHY A SCRIPT AND NOT A TEST. It reports, it does not assert. A gate that fails
on a number nobody has a baseline for teaches people to ignore the gate. Save
the JSON, change something, run it again, diff the two.

    python scripts/evaluate_clipper_reasoning.py
    python scripts/evaluate_clipper_reasoning.py slice4h00test 2d3375ee3420
    python scripts/evaluate_clipper_reasoning.py --json > before.json

Reads only. It never writes to a project, and a project missing the traces is
reported as missing rather than skipped in silence — a corpus that quietly
shrinks to the projects that happen to have artefacts is how a comparison
starts measuring the wrong thing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))

DATA = _ROOT / "data" / "clipper"


def _read(project_id: str, name: str) -> dict | None:
    path = DATA / project_id / "analysis" / f"{name}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None


def _projects(argv: list[str]) -> list[str]:
    if argv:
        return argv
    if not DATA.is_dir():
        return []
    return sorted(p.name for p in DATA.iterdir()
                  if p.is_dir() and not p.name.startswith("_"))


def _coverage(chunks: list[dict], duration: float) -> dict:
    """How much of the source the chunk plan actually reached.

    `covered_s` is None when the chunker did not record times — the current one
    splits on characters and does not know. That None is the finding, not a
    gap in this script.
    """
    timed = [c for c in chunks
             if c.get("t_start") is not None and c.get("t_end") is not None]
    if not timed:
        return {"chunks": len(chunks), "timed": 0, "covered_s": None,
                "covered_share": None, "widest_s": None}
    spans = sorted((float(c["t_start"]), float(c["t_end"])) for c in timed)
    merged: list[list[float]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    covered = sum(e - s for s, e in merged)
    return {
        "chunks": len(chunks),
        "timed": len(timed),
        "covered_s": round(covered, 1),
        "covered_share": round(covered / duration, 3) if duration > 0 else None,
        "widest_s": round(max(e - s for s, e in spans), 1),
    }


def evaluate(project_id: str) -> dict:
    run = _read(project_id, "reasoning_run")
    sel = _read(project_id, "selection_trace")
    if run is None and sel is None:
        return {"project": project_id, "status": "no_trace"}
    # Half a pair is worse than none: the totals of the missing one read as
    # zeros, so a project that lost its selection trace reports a field of 0
    # candidates and 0% judged — which looks like a finding rather than a gap.
    if run is None or sel is None:
        return {"project": project_id, "status": "partial_trace",
                "missing": "reasoning_run" if run is None else "selection_trace"}
    # Written independently, so they can be from different runs.
    if run.get("run_id") and sel.get("run_id") and run["run_id"] != sel["run_id"]:
        return {"project": project_id, "status": "mismatched_runs",
                "run_id": run.get("run_id"), "selection_run_id": sel.get("run_id")}
    totals = dict(sel.get("totals") or {})
    duration = float((run.get("versions") or {}).get("duration") or 0.0)

    judged = int(totals.get("judged") or 0)
    field = int(totals.get("candidates") or 0)
    story = int(totals.get("story_candidates") or 0)
    story_judged = int(totals.get("story_judged") or 0)

    return {
        "project": project_id,
        "status": "ok",
        "mode": run.get("reasoning_mode") or sel.get("reasoning_mode"),
        "outcome": run.get("outcome"),
        "launched_by": run.get("launched_by"),
        "settings_fingerprint": run.get("settings_fingerprint"),
        "input_fingerprint": run.get("input_fingerprint"),
        "structure_fingerprint": sel.get("structure_fingerprint"),
        "run_id": run.get("run_id"),
        "eliminated": int(totals.get("eliminated") or 0),
        "unusable_answers": len(run.get("unusable") or []),
        "exhausted_calls": len(run.get("exhausted") or []),
        "duration_s": duration,
        "field": field,
        "judged": judged,
        # The single number the whole audit turned on.
        "judged_share": round(judged / field, 3) if field else None,
        "winners": int(totals.get("winners") or 0),
        "story_candidates": story,
        "story_judged": story_judged,
        "story_judged_share": round(story_judged / story, 3) if story else None,
        "pool_rounds": int(totals.get("pool_rounds") or 0),
        "coverage": _coverage(list(run.get("chunks") or []), duration),
        # A run that reused cached anchors never chunks anything. Reported as
        # 0 chunks it reads like a finding; it is a different run shape.
        "anchors_cached": any(st.get("name") == "anchors"
                              and st.get("status") == "cached"
                              for st in (run.get("stages") or [])),
        "attempts": len(run.get("attempts") or []),
        "fallbacks": len(run.get("fallbacks") or []),
        "errors": len(run.get("errors") or []),
        "counts": dict(run.get("counts") or {}),
    }


def _line(row: dict) -> str:
    if row["status"] == "no_trace":
        return f"{row['project']:<16} no reasoning trace on disk"
    if row["status"] == "partial_trace":
        return f"{row['project']:<16} INCOMPLETE - {row['missing']} is missing"
    if row["status"] == "mismatched_runs":
        return (f"{row['project']:<16} MISMATCH - the two artefacts are from "
                f"different runs ({row['run_id']} vs {row['selection_run_id']})")
    cov = row["coverage"]
    share = "n/a" if row["judged_share"] is None else f"{row['judged_share']:.0%}"
    story = ("n/a" if row["story_judged_share"] is None
             else f"{row['story_judged']}/{row['story_candidates']}")
    covered = ("untimed" if cov["covered_share"] is None
               else f"{cov['covered_share']:.0%}")
    if row["anchors_cached"]:
        covered, chunks = "cached", "cached"
    else:
        chunks = str(cov["chunks"])
    return (f"{row['project']:<16} {str(row['mode'] or '?'):<14} "
            f"field={row['field']:<5} judged={row['judged']:<4} ({share:>4}) "
            f"story={story:<7} chunks={chunks:<6} cover={covered:<8} "
            f"fallbacks={row['fallbacks']} unusable={row['unusable_answers']} "
            f"errors={row['errors']} "
            f"[{row['outcome'] or '?'}]")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("projects", nargs="*", help="project ids (default: all)")
    parser.add_argument("--json", action="store_true",
                        help="machine-readable, for diffing two runs")
    args = parser.parse_args()

    rows = [evaluate(p) for p in _projects(args.projects)]
    if args.json:
        print(json.dumps(rows, indent=2, sort_keys=True))
        return 0

    traced = [r for r in rows if r["status"] == "ok"]
    print(f"{len(traced)} of {len(rows)} projects carry a reasoning trace\n")
    for row in rows:
        print(_line(row))

    if not traced:
        # Plain ASCII on purpose: this prints to a Windows console, where the
        # default codepage turns an em-dash into a replacement character.
        print("\nNothing to compare yet. The traces are written by the scoring "
              "stage, so a project scored before this batch has none. Re-score "
              "one, or use a project scored after this batch landed.")
        return 0

    starved = [r for r in traced if r["judged_share"] is not None
               and r["judged_share"] < 0.5]
    if starved:
        print("\nMost of the field was never judged on: "
              + ", ".join(r["project"] for r in starved))
    untimed = [r for r in traced if r["coverage"]["covered_share"] is None
               and r["coverage"]["chunks"] and not r["anchors_cached"]]
    if untimed:
        print("Chunk plan has no timing (splits on characters): "
              + ", ".join(r["project"] for r in untimed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
