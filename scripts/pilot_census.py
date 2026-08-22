"""Compare what the story engine found across sources of different kinds.

The pilot's question is whether six batches of reasoning work generalise or are
shaped by the one Minecraft stream everything was measured on. That question is
not answered by any single run's trace — it is answered by the same numbers,
side by side, on material that has nothing in common.

WHY THIS RECOMPUTES RATHER THAN READING `reasoning_run.json`. The census is a
pure function of the candidates on disk, so a run scored before the counters
existed — or before they were corrected — can be read without spending its LLM
calls again. That also means this and the trace must agree: if they ever
disagree on a project scored by the current code, one of them is wrong.

PER HOUR, NOT ABSOLUTE. A 22-minute talking-head and a 3h43m stream cannot be
compared on counts. Density is the comparable figure, and even it is not fair
across formats — edited material is denser by construction — so it separates
"the engine finds nothing here" from "this source is short".

    python scripts/pilot_census.py pilotf81b pilotee0e pilot6b38 pilot2c8a

Reads only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))

DATA = _ROOT / "data" / "clipper"


def _read(project_id: str, name: str):
    path = DATA / project_id / "analysis" / f"{name}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None


def _duration(project_id: str) -> float:
    """Source length, from whichever artefact recorded it.

    `meta` is the intended home; atoms and the transcript are the fallbacks for
    a project scored before it was written, and the last atom's end is a floor
    rather than the true length — good enough to place a moment in a quarter,
    not good enough to quote.
    """
    meta = _read(project_id, "meta") or {}
    for key in ("duration", "duration_s", "source_duration"):
        try:
            value = float(meta.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value

    ends = [float(a.get("end") or 0)
            for a in (_read(project_id, "atoms") or [])
            if isinstance(a, dict)]
    return max(ends) if ends else 0.0


def _as_scored(cands: list[dict]) -> list[dict]:
    """The field as the GROUPING saw it, not as the run left it.

    `dedupe._group` elects each group's leader by `overall`, and `apply_ranking`
    rewrites `overall` for everything the judge scored. So regrouping the saved
    candidates does not reproduce the run's grouping: on `pilotf81b` the same 77
    candidates gave 23 groups during the run and 30 afterwards, because 23 of
    them had been blended.

    This is the same hazard `build_pool`'s docstring records for a SECOND
    judging round, met here from the other side — and it is why the earlier
    audit found 294 moments in the trace and 304 by regrouping the artefact.

    Restoring `heuristic_score` reproduces the pre-judge order exactly. A
    candidate with no `heuristic_score` was never rescored, so its `overall` is
    already the heuristic one.
    """
    out = []
    for cand in cands:
        base = cand.get("heuristic_score")
        out.append(dict(cand, overall=base) if base is not None else cand)
    return out


def census(project_id: str) -> dict:
    from services.clipper import candidate_groups as cg

    cands = _read(project_id, "candidates")
    if isinstance(cands, dict):
        cands = cands.get("candidates")
    if not cands:
        return {"project": project_id, "status": "no candidates"}

    duration = _duration(project_id)
    groups = cg.build_groups(_as_scored(cands))
    out = cg.story_census(groups, duration=duration)
    out.update({
        "project": project_id,
        "status": "ok",
        "hours": round(duration / 3600.0, 2),
        "candidates": len(cands),
        "moments": len(groups),
        # The trace's own figure, to be checked against this one. A project
        # scored before the counters existed has none, which is not a mismatch.
        "trace_story_groups": ((_read(project_id, "reasoning_run") or {})
                               .get("counts", {}).get("story_groups")),
    })
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("projects", nargs="+")
    args = parser.parse_args()

    rows = [census(p) for p in args.projects]
    ok = [r for r in rows if r["status"] == "ok"]

    for row in rows:
        if row["status"] != "ok":
            print(f"{row['project']:>14}  {row['status']}")

    if not ok:
        return 1

    head = (f"{'project':>14} {'hours':>6} {'cands':>6} {'moments':>8} "
            f"{'story':>6} {'/hour':>6} {'grnd':>5} {'valid':>6} {'unc':>4} "
            f"{'inv':>4}  quarters")
    print(head)
    print("-" * len(head))
    for row in ok:
        per_hour = row["story_groups"] / row["hours"] if row["hours"] else 0.0
        spread = ("/".join(str(n) for n in row["story_quarters"])
                  if row["story_quarters"] else "unknown")
        print(f"{row['project']:>14} {row['hours']:6.2f} {row['candidates']:6d} "
              f"{row['moments']:8d} {row['story_groups']:6d} {per_hour:6.1f} "
              f"{row['story_grounded']:5d} {row['story_valid']:6d} "
              f"{row['story_uncertain']:4d} {row['story_invalid']:4d}  {spread}")

    print("")
    for row in ok:
        recorded = row["trace_story_groups"]
        if recorded is None:
            print(f"  {row['project']}: scored before the census existed; "
                  "no trace figure to check against")
        elif recorded != row["story_groups"]:
            print(f"  {row['project']}: TRACE DISAGREES — trace says "
                  f"{recorded}, the candidates say {row['story_groups']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
