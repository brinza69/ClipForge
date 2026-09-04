"""Would a re-plan of the pilots deliver what the fixes claim — asked BEFORE encoding.

    python scripts/verify_replan.py pilotf81b pilotee0e pilot6b38 pilot2c8a
    python scripts/verify_replan.py --json pilot6b38

WHY THIS EXISTS AND WHY IT COMES FIRST. `rerender_pilots.py` REPLAYS the stored
plan: it re-encodes the mp4 from the sidecar that is already on disk. That is
why R1 landed on 29 August, removed 116 invisible cuts from the planner, and
never changed a single export — the 31 August re-render replayed plans written
on 22 August, and the corpus still measures exactly 116.

So a re-render is worth nothing until the PLAN is rebuilt, and rebuilding 58
plans and encoding them is hours. This runs the real planner over every stored
clip, compares the result against what shipped, and prints the four things a
re-render is supposed to prove — without encoding anything.

THE FOUR PROOFS, and each is a claim somebody made that this checks rather than
repeats:

    1  the 116 equivalent cuts become zero        R1, delivered 29 August
    2  the brief `fit` islands are absorbed       31 August, and the count is
                                                  compared against the 30 the
                                                  corpus measurement predicted
    3  the plan carries the shadow views          R3a/R3b/R4 — no stored sidecar
                                                  has them, so the subject check
                                                  is `unavailable` 101 times
    4  the long crop<->fit junctions are handled  UNDECIDED, and this refuses
                                                  rather than passing over it

PROOF 4 IS A REFUSAL ON PURPOSE. A human timestamped four junctions and none of
them is a brief island, so the absorption does not touch them; what to do there
— hard cut, short transition, controlled zoom — has not been chosen. A script
that quietly ignored an open decision would be reporting a re-render as ready
when the thing a person objected to is unchanged. It names it and exits
non-zero, and `--accept-junctions <name>` is how somebody records that the
decision was made.

NOTHING IS WRITTEN. No sidecar, no mp4, no database row.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))

DATA = Path(os.environ.get("CLIPFORGE_DATA_DIR") or (_ROOT / "data")) / "clipper"

#: What the corpus measurement predicted the absorption would remove, from
#: `docs/refs/human-gate-2026-08-31.md`.
#:
#: REPORTED, AND NOT ASSERTED, and the first version of this script did assert
#: it. The 30 was counted over the STORED plans: 15 interior `fit` islands under
#: the dwell, two edges each. A re-plan is a different generation — 1,341 shots
#: become 1,231 once R1's merge and the absorption both run — so its run
#: boundaries are not the same boundaries, and 24 rather than 30 is what a
#: different plan gives, not a fault. Comparing a function's output against a
#: count taken from inputs it did not have is the error this whole batch keeps
#: finding; the number stays as context and the invariant below is what is
#: checked.
PREDICTED_FROM_STORED_PLANS = 30
#: And the baseline it has to drive to zero, from R0.
BASELINE_EQUIVALENT_CUTS = 116


def _equivalent_cuts(plan: dict, src_w, src_h) -> int:
    from services.clipper import dynamic_geometry as dg

    shots = plan.get("shots") or []
    if not shots:
        return 0
    merged = dg.merge_equivalent_shots(
        {**plan, "shots": [dict(s) for s in shots]}, src_w, src_h)
    return len(shots) - len(merged["shots"])


def _junctions(plan: dict) -> int:
    comps = [s.get("composition") for s in (plan.get("shots") or [])
             if isinstance(s, dict)]
    if not comps or any(c is None for c in comps):
        return 0
    return sum(1 for a, b in zip(comps, comps[1:]) if a != b)


def _runs(plan: dict) -> list[list[dict]] | None:
    shots = [s for s in (plan.get("shots") or []) if isinstance(s, dict)]
    if not shots or any(s.get("composition") is None for s in shots):
        return None
    runs, cur = [], [shots[0]]
    for shot in shots[1:]:
        if shot.get("composition") == cur[0].get("composition"):
            cur.append(shot)
        else:
            runs.append(cur)
            cur = [shot]
    runs.append(cur)
    return runs


def _surviving_short_islands(plan: dict, *, min_dwell_s: float) -> int:
    """Interior `fit` runs under the dwell that the absorption left behind.

    THE INVARIANT THE RULE ACTUALLY PROMISES, and the one thing about the
    absorption that can be checked on the new plan alone. Zero is the only
    acceptable answer; anything else means the pass did not run or did not
    reach. Leading and trailing runs are excluded because the rule excludes
    them on purpose — the claim it makes is "the subject was there on both
    sides", and an opening or an ending has evidence on only one.
    """
    runs = _runs(plan)
    if runs is None:
        return 0
    left = 0
    for i, run in enumerate(runs):
        if not (0 < i < len(runs) - 1) or run[0].get("composition") != "fit":
            continue
        try:
            span = float(run[-1]["t1"]) - float(run[0]["t0"])
        except (KeyError, TypeError, ValueError):
            continue
        if span == span and span < min_dwell_s:
            left += 1
    return left


def _long_junctions(plan: dict, *, min_dwell_s: float) -> int:
    """Junctions the absorption cannot touch — the ones a human objected to.

    A run longer than the dwell is an EARNED `fit`, and both its edges cost the
    3.16x jump. Counting them is the whole of proof 4.
    """
    runs = _runs(plan)
    if runs is None:
        return 0
    long_edges = 0
    for i, run in enumerate(runs):
        try:
            span = float(run[-1]["t1"]) - float(run[0]["t0"])
        except (KeyError, TypeError, ValueError):
            continue
        if run[0].get("composition") == "fit" and span >= min_dwell_s:
            long_edges += (1 if i > 0 else 0) + (1 if i < len(runs) - 1 else 0)
    return long_edges


async def _replan(project_id: str) -> list[dict]:
    """Run the real planner over every stored clip of one project."""
    from sqlalchemy import select

    from database import async_session, init_db
    from models import ClipModel
    from services.clipper import storage
    from workers.clipper_render_plan import _decide_render, _load

    # The app runs this at startup and a script that touches the DB has to as
    # well: `content_confidence` is declared in `models.py` and in
    # `database.py`'s migration list, and a database nobody has opened since it
    # was added does not have the column. Idempotent by construction.
    await init_db()

    paths = storage.paths(project_id)
    async with async_session() as session:
        rows = (await session.execute(
            select(ClipModel.id).where(ClipModel.project_id == project_id)
        )).scalars().all()

    out = []
    for clip_id in rows:
        sidecar = DATA / project_id / "exports" / f"{clip_id}.json"
        if not sidecar.exists():
            continue
        try:
            stored = json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception:
            out.append({"clip": clip_id, "refused": "sidecar_unreadable"})
            continue
        clip, project = await _load(clip_id)
        try:
            decision = await _decide_render(clip, project, paths["exports_dir"])
        except Exception as exc:
            out.append({"clip": clip_id,
                        "refused": f"planner_raised_{type(exc).__name__}"})
            continue

        old_plan = stored.get("dynamic_plan") if isinstance(
            stored.get("dynamic_plan"), dict) else {}
        new_plan = decision.get("dyn") or {}
        src_w = new_plan.get("src_w") or old_plan.get("src_w")
        src_h = new_plan.get("src_h") or old_plan.get("src_h")
        out.append({
            "clip": clip_id,
            "refused": None,
            "old_shots": len(old_plan.get("shots") or []),
            "new_shots": len(new_plan.get("shots") or []),
            "old_equivalent": _equivalent_cuts(old_plan, src_w, src_h),
            "new_equivalent": _equivalent_cuts(new_plan, src_w, src_h),
            "old_junctions": _junctions(old_plan),
            "new_junctions": _junctions(new_plan),
            "long_junctions": _long_junctions(new_plan, min_dwell_s=_dwell()),
            "short_islands_left": _surviving_short_islands(
                new_plan, min_dwell_s=_dwell()),
            "has_regime_view": isinstance(decision.get("regime_view"), dict),
            "has_caption_policy": isinstance(decision.get("caption_policy"), dict),
        })
    return out


def _dwell() -> float:
    from services.clipper.dynamic_geometry import MIN_FIT_DWELL_S

    return float(MIN_FIT_DWELL_S)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("projects", nargs="+")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--accept-junctions", default="",
                    help="record who decided what to do about the long "
                         "crop<->fit junctions; without it proof 4 refuses")
    args = ap.parse_args()

    rows: list[dict] = []
    for project in args.projects:
        rows.extend({"project": project, **row}
                    for row in asyncio.run(_replan(project)))

    ok = [r for r in rows if not r["refused"]]
    refused = [r for r in rows if r["refused"]]
    total = {key: sum(r[key] for r in ok) for key in
             ("old_shots", "new_shots", "old_equivalent", "new_equivalent",
              "old_junctions", "new_junctions", "long_junctions",
              "short_islands_left")}
    views = sum(1 for r in ok if r["has_regime_view"])
    policies = sum(1 for r in ok if r["has_caption_policy"])

    failures: list[str] = []
    if not ok:
        failures.append("nothing was re-planned — a pass over nothing is not a pass")
    for row in refused:
        failures.append(f"{row['project']}/{row['clip']}: {row['refused']}")

    # 1 — the invisible cuts
    if total["new_equivalent"] != 0:
        failures.append(
            f"proof 1: {total['new_equivalent']} equivalent cuts survive the "
            f"re-plan (baseline was {total['old_equivalent']})")
    # 2 — the absorption, against the invariant the rule promises rather than
    # against a count taken from a different generation of plan
    removed = total["old_junctions"] - total["new_junctions"]
    if total["short_islands_left"]:
        failures.append(
            f"proof 2: {total['short_islands_left']} interior `fit` runs under "
            f"{_dwell():.1f}s survive the re-plan; the absorption promises none")
    # 3 — the shadow views a fresh plan is supposed to carry
    if views != len(ok) or policies != len(ok):
        failures.append(
            f"proof 3: {views}/{len(ok)} plans carry a regime view and "
            f"{policies}/{len(ok)} carry a caption policy")
    # 4 — the decision nobody has made
    if total["long_junctions"] and not args.accept_junctions:
        failures.append(
            f"proof 4: {total['long_junctions']} junctions belong to `fit` runs "
            f"longer than {_dwell():.1f}s, which the absorption does not touch "
            f"and a human objected to. Nothing has been decided for them; pass "
            f"--accept-junctions <who/what> once it has")

    report = {"clips": len(rows), "planned": len(ok), "refused": len(refused),
              "totals": total, "regime_views": views,
              "caption_policies": policies,
              "junctions_removed": removed,
              "junctions_predicted_from_stored_plans": PREDICTED_FROM_STORED_PLANS,
              "short_islands_left": total["short_islands_left"],
              "accepted_junctions": args.accept_junctions or None,
              "failures": failures, "ready_to_render": not failures}

    if args.json:
        print(json.dumps(report, indent=1))
    else:
        print(f"\nre-planned {len(ok)} clips over {len(args.projects)} projects"
              f"  ({len(refused)} refused)\n")
        print(f"  shots            {total['old_shots']:5} -> {total['new_shots']:5}")
        print(f"  equivalent cuts  {total['old_equivalent']:5} -> "
              f"{total['new_equivalent']:5}   (R0 baseline {BASELINE_EQUIVALENT_CUTS})")
        print(f"  crop<->fit       {total['old_junctions']:5} -> "
              f"{total['new_junctions']:5}   ({removed} removed)")
        print(f"  of those, long   {total['long_junctions']:5}   "
              f"(runs >= {_dwell():.1f}s, untouched by the absorption)")
        print(f"  short islands    {total['short_islands_left']:5}   "
              f"(interior `fit` under the dwell; the rule promises none)")
        print(f"  regime views     {views}/{len(ok)}")
        print(f"  caption policies {policies}/{len(ok)}")
        print()
        for line in failures:
            print(f"FAIL: {line}")
        if not failures:
            print("every proof holds; a re-render would deliver what the fixes claim")
        else:
            print("\nnothing was written. Fix the above before spending the encode.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
