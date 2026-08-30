"""Batch R5a: what the final-end snap does to the SCORES, without a re-score.

`measure_boundary_snap.py` answers where the 261 truncated windows move.
It says nothing about what that costs, and the honest sentence it ends on is
that the consequence needs a re-score on a clone. This gets most of the way
there without one: it re-runs the real scorer over the stored windows twice —
once as they are, once with the snap applied — and reports what changed.

    python scripts/measure_snap_board_delta.py --all
    python scripts/measure_snap_board_delta.py pilotf81b --top 20

BOTH SIDES ARE RE-SCORED, and that is the whole reason this is trustworthy.
Comparing the scores stored in `candidates.json` against fresh ones would
attribute every change the scorer has had since those artefacts were written to
this batch — the same vintage trap `--recompute` had to be documented against.
The "before" column is today's scorer on today's windows; the "after" column is
today's scorer on the same windows with their ends snapped. Only the boundary
differs.

WHAT IT STILL CANNOT SEE, and a clean report here is not permission to skip it:

- **The judge.** An LLM pass is not reproducible offline, so the shortlist that
  reaches it is measured and its verdict is not.
- **Dedupe groups.** Two windows whose ends move can merge or split, and this
  reports the ordering rather than the grouping.
- **The board.** What ships is the winners after dedupe, capping and judging.

WHAT A CLEAN REPORT ENTITLES YOU TO SAY, and it is narrower than it looks. Not
"the board is unlikely to move" — the supported sentence is: *the snap produced
no crossings over these thresholds at the level of the windows' heuristic score;
the effect on the judge and on what finally ships remains unknown.* Grouping can
merge two windows whose ends moved even when the same indices stay in the top N,
which is why the shortlist is built with the real `build_groups` and
`build_shortlist` rather than argued about — and even then the judge's verdict
on that shortlist is not reproducible here.

SEVERAL THRESHOLDS, each named for what it is. `clip_count` is the board's own
capacity, 20 is a diagnostic somebody chose, and 80 is the shortlist BUDGET —
not the judge pool, which is decided after grouping and is reported separately.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))

DATA = Path(os.environ.get("CLIPFORGE_DATA_DIR") or (_ROOT / "data")) / "clipper"

from config import settings  # noqa: E402
from database import async_session  # noqa: E402
from models import ProjectModel, TranscriptModel  # noqa: E402
from sqlalchemy import select  # noqa: E402

from services.clipper import boundary_completion as bc  # noqa: E402
from services.clipper import candidate_groups, storage, story_evidence  # noqa: E402
from services.clipper.candidate_boundaries import _fit  # noqa: E402
from services.clipper.candidate_terms import (  # noqa: E402
    _neighbourhood, _num, _text_of, _words_for,
)
from services.clipper.serialize import effective_content_type  # noqa: E402
from workers import clipper_scoring  # noqa: E402
from workers.clipper_cache import _segment_types  # noqa: E402

#: How many of the top-scoring windows to compare. A REPORTING window, not a
#: decision: nothing here gates anything and the full ordering is compared as
#: well. The default is the shortlist cap the judge really sees — 80 moments,
#: from `reasoning_run.json` — because a window narrower than the board's own is
#: a stability claim about a set nobody ships.
TOP_N = 80


async def _load(project_id: str):
    async with async_session() as session:
        row = (await session.execute(
            select(TranscriptModel)
            .where(TranscriptModel.project_id == project_id).limit(1)
        )).scalar_one_or_none()
        project = await session.get(ProjectModel, project_id)
    if not row or not row.segments or project is None:
        return None, None
    return {"language": row.language, "segments": row.segments}, project


def _refitted(cand: dict, words, *, lo: float, hi: float, ceiling: float,
              atoms) -> tuple[dict | None, bool]:
    """`(the candidate as `_fit` would leave it, whether its end was in a word)`.

    THE CANONICAL FUNCTION, not a replica of its guards. An earlier version of
    this script re-implemented the minimum, the maximum and the media check, and
    a measurement whose rule is a copy of the rule it is measuring can drift
    from it in exactly the cases that matter.

    Calling `_fit` means the "after" column is what the planner would really
    produce. It also means the script has to prove that the ONLY thing `_fit`
    changes on these windows is the snap — which is what the second return
    value is for: a window whose end is not inside a word must come back
    untouched, and every one that does not is reported as contamination rather
    than as an effect of this batch.
    """
    start, end = _num(cand.get("start")), _num(cand.get("end"))
    straddled = bc._straddled(words, end) is not None
    _fitted_start, fitted_end = _fit(start, end, words, lo, hi, 0.0, ceiling)
    if abs(fitted_end - end) < 1e-6:
        return None, straddled
    inside, _b, _a = _neighbourhood(words, start, fitted_end)
    out = copy.deepcopy(cand)
    out["end"] = round(fitted_end, 3)
    out["words"] = list(inside)
    out["text"] = _text_of(inside) or str(cand.get("text") or "")
    # THE STORY BLOCK MOVES WITH THE WINDOW. `refine_boundaries` calls
    # `remeasure` after every step that can move an edge, and a measurement that
    # snapped the end and scored against the OLD context/payoff/reaction numbers
    # would be comparing a new window against an old moment — which is the exact
    # failure `remeasure` was added to stop, reproduced in the tool built to
    # check for it.
    if isinstance(out.get("story"), dict):
        story_evidence.remeasure(out, atoms=atoms)
    return out, straddled


def _score(rows: list[dict], *, transcript, signals, duration, profile,
           platform, seg_types, overridden) -> None:
    clipper_scoring.score_candidates(
        rows, transcript=transcript, signals=signals, duration=duration,
        profile=profile, platform=platform, seg_types=seg_types,
        overridden=overridden, model=None, use_learned=False)


def _order(rows: list[dict]) -> list[int]:
    """Indices, best first. Ties broken by index so the order is total."""
    return [i for i, _r in sorted(enumerate(rows),
                                  key=lambda p: (-_num(p[1].get("overall")), p[0]))]


def _shortlist_delta(before: list[dict], after: list[dict],
                     duration: float) -> dict:
    """Which MOMENTS the judge would be asked about, before and after.

    The windows are cuts of moments; the judge sees moments. Grouping can merge
    two windows whose ends moved, or split one, and a stable ordering of windows
    says nothing about that — which is why this runs the real
    `build_groups` + `build_shortlist` rather than reasoning about them.
    """
    def _ids(rows: list[dict]) -> list[str]:
        groups = candidate_groups.build_groups(rows)
        picked = candidate_groups.build_shortlist(groups, duration=duration)
        return [str(m.get("moment_id") or m.get("id") or "")
                for m in (picked.get("selected") or [])]

    try:
        a, b = _ids(before), _ids(after)
    except Exception as exc:
        return {"refused": type(exc).__name__}
    return {"before": len(a), "after": len(b),
            "membership_changed": sorted(set(a) ^ set(b)),
            "order_changed": a != b}


def _measure(project_id: str, top_n: int = TOP_N) -> dict:
    path = DATA / project_id / "analysis" / "candidates.json"
    if not path.exists():
        return {"project": project_id, "refused": "no_candidates_artefact"}
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = [c for c in raw if isinstance(c, dict)]
    # Counted, not filtered. Scoring the readable subset and reporting green
    # over it is the same hole this plan has now found eight times.
    invalid = len(raw) - len(rows)
    transcript, project = asyncio.run(_load(project_id))
    if transcript is None:
        return {"project": project_id, "refused": "no_transcript_or_project"}

    signals = storage.read_artifact(project_id, "signals") or {}
    duration = float(project.duration or 0.0)
    cfg = project.clipper_settings or {}
    lo = float(cfg.get("min_clip_s") or settings.clipper_min_clip_s)
    hi = float(cfg.get("max_clip_s") or settings.clipper_max_clip_s)
    target_count = int(cfg.get("clip_count") or settings.clipper_default_clip_count)
    profile = effective_content_type(project)
    platform = cfg.get("platform") or "tiktok"
    seg_types = _segment_types(project_id, duration, transcript)
    overridden = bool(project.content_type_override)
    words = _words_for({}, transcript)
    ceiling = duration or (_num(words[-1].get("end")) if words else 0.0)
    # Read by `remeasure` to resolve back-references. Absent on the legacy path,
    # where the story block does not exist either.
    atoms = storage.read_artifact(project_id, "atoms")

    before = [copy.deepcopy(c) for c in rows]
    after = []
    moved: list[int] = []
    contaminated: list[int] = []
    for i, cand in enumerate(rows):
        refitted, straddled = _refitted(cand, words, lo=lo, hi=hi,
                                        ceiling=ceiling, atoms=atoms)
        after.append(refitted if refitted is not None else copy.deepcopy(cand))
        if refitted is not None:
            moved.append(i)
            if not straddled:
                # `_fit` moved an end that was NOT inside a word, so something
                # other than the snap is at work and the delta is not this
                # batch's alone.
                contaminated.append(i)

    kw = dict(transcript=transcript, signals=signals, duration=duration,
              profile=profile, platform=platform, seg_types=seg_types,
              overridden=overridden)
    _score(before, **kw)
    _score(after, **kw)

    deltas = [round(_num(after[i].get("overall")) - _num(before[i].get("overall")), 4)
              for i in range(len(rows))]
    changed = [i for i, d in enumerate(deltas) if abs(d) > 1e-9]
    order_before, order_after = _order(before), _order(after)

    def _window(n: int) -> dict:
        a, b = order_before[:n], order_after[:n]
        return {"n": n, "set_changed": sorted(set(a) ^ set(b)),
                "order_changed": a != b}

    # SEVERAL THRESHOLDS, each named for what it actually is. One number invited
    # the reading that a stable set at that number says something about the
    # board, and it does not: the board is `clip_count` after grouping,
    # shortlisting and judging, and 20 is a diagnostic somebody chose.
    windows = {
        "clip_count": _window(target_count),
        "arbitrary_20": _window(20),
        # NOT the judge pool. 80 is the shortlist BUDGET, and which 80 moments
        # reach the judge is decided after grouping — measured separately below.
        "top_80_windows": _window(80),
        "requested": _window(top_n),
    }

    # And the one that is about moments rather than windows: the shortlist the
    # judge would really be asked about, after the same grouping the run uses.
    shortlist = _shortlist_delta(before, after, duration)
    return {
        "project": project_id,
        "candidates": len(raw),
        "invalid": invalid,
        "moved": len(moved),
        # Ends `_fit` moved that were not inside a word. Must be zero, or the
        # comparison is measuring more than the snap.
        "contaminated": contaminated,
        "scores_changed": len(changed),
        # A window whose end never moved and whose score did is the scorer
        # reaching outside its own window — worth seeing, never expected.
        "changed_without_moving": sorted(set(changed) - set(moved)),
        "max_delta": max((abs(d) for d in deltas), default=0.0),
        "mean_abs_delta": round(sum(abs(d) for d in deltas) / max(1, len(deltas)), 5),
        "order_changed": order_before != order_after,
        "top_n": top_n,
        "windows": windows,
        "shortlist": shortlist,
        # Stamped, because it will stop being true. The learned ranker is
        # enabled in config with no model and no training rows, so
        # `use_learned=False` reproduces today — and the day it is switched on
        # this report would silently start describing a different scorer.
        "scorer": "heuristic_only",
    }


def _report(row: dict) -> None:
    if row.get("refused"):
        print(f"{row['project']:14} {row['refused']}")
        return
    print(f"{row['project']:14} {row['candidates']:5} candidates   "
          f"moved {row['moved']:4}   scores changed {row['scores_changed']:4}   "
          f"max |delta| {row['max_delta']}   [{row['scorer']}]")
    for name, w in row["windows"].items():
        print(f"{'':14} {name:16} n={w['n']:<4} membership "
              f"{'CHANGED' if w['set_changed'] else 'same':8} order "
              f"{'changed' if w['order_changed'] else 'same'}")
    sl = row["shortlist"]
    if sl.get("refused"):
        print(f"{'':14} shortlist: could not be built ({sl['refused']})")
    else:
        print(f"{'':14} {'judge shortlist':16} {sl['before']}->{sl['after']} "
              f"moments, membership "
              f"{'CHANGED' if sl['membership_changed'] else 'same':8} order "
              f"{'changed' if sl['order_changed'] else 'same'}")
    if row.get("invalid"):
        print(f"{'':14} {row['invalid']} entries are not records — the corpus is "
              f"smaller than the file")
    if row.get("contaminated"):
        print(f"{'':14} CONTAMINATED: `_fit` moved {len(row['contaminated'])} "
              f"end(s) that were not inside a word, so the delta is not the "
              f"snap alone")
    if row["changed_without_moving"]:
        print(f"{'':14} {len(row['changed_without_moving'])} window(s) changed "
              f"score WITHOUT their end moving — the scorer is reading outside "
              f"its own window")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("projects", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--top", type=int, default=TOP_N,
                    help=f"how many of the top-scoring windows to compare "
                         f"(default {TOP_N}, the judge's own shortlist cap)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    names = list(args.projects)
    if args.all or not names:
        names = sorted(p.name for p in DATA.glob("*") if p.is_dir()
                       if (p / "analysis" / "candidates.json").exists())
    if not names:
        print(f"no projects with candidates under {DATA}")
        return 2

    rows = [_measure(n, args.top) for n in names]
    assert len(rows) == len(names), "a project left the run without saying so"
    refused = sum(1 for r in rows if r.get("refused"))
    invalid = sum(r.get("invalid", 0) for r in rows)
    contaminated = sum(len(r.get("contaminated") or []) for r in rows)
    code = 2 if (refused or invalid or contaminated) else 0

    if args.json:
        print(json.dumps(rows, indent=2))
        return code

    for row in rows:
        _report(row)
    moved = sum(r.get("moved", 0) for r in rows)
    shifted = [r["project"] for r in rows
               if (r.get("shortlist") or {}).get("membership_changed")]
    print(f"\n{'POOLED':14} {moved} windows moved over "
          f"{sum(r.get('candidates', 0) for r in rows)}")
    print(f"{'':14} judge shortlist membership changed in "
          f"{len(shifted)} project(s){': ' + ', '.join(shifted) if shifted else ''}")
    if refused:
        print(f"{'':14} {refused} project(s) could not be measured at all")
    if invalid:
        print(f"{'':14} {invalid} unreadable entries across the corpus")
    if contaminated:
        print(f"{'':14} {contaminated} end(s) moved for reasons other than the "
              f"snap — the delta is not this batch's alone")
    print(f"{'':14} This is the SCORER. The judge, the dedupe groups and the "
          f"board are not measured here and cannot be without a re-score on a "
          f"clone.")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
