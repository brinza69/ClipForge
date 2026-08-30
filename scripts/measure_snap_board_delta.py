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
- **The judge's verdict.** The grouping and the deterministic shortlist ARE
  measured, with the real `build_groups` and `build_shortlist`. What the judge
  then says about that shortlist is not reproducible offline.
- **The board.** What ships is the winners after the judge has spoken.

WHAT A CLEAN REPORT ENTITLES YOU TO SAY, and it is narrower than it looks. Not
"the board is unlikely to move" — the supported sentence is: *the snap does not
change which windows the heuristic ranks highest, nor which of them the
deterministic shortlist asks about; the effect on the judge and on what finally
ships remains unknown.* Every threshold below is a set of WINDOWS ranked by
heuristic score. The board is moments, after grouping, shortlisting and judging,
and `top_clip_count_windows` coincides with its capacity numerically and not in
population.

SEVERAL THRESHOLDS, each named for what it is. `clip_count` is the board's own
capacity, 20 is a diagnostic somebody chose, and 80 is the shortlist BUDGET —
not the judge pool, which is decided after grouping and is reported separately.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import math
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
    fitted_start, fitted_end = _fit(start, end, words, lo, hi, 0.0, ceiling)
    if abs(fitted_end - end) < 1e-6 and abs(fitted_start - start) < 1e-6:
        return None, True

    # WHICH OF `_fit`'S RULES COULD HAVE FIRED, from their preconditions rather
    # than from their effect. Checking "the end was inside a word" was not
    # enough: a window over the maximum is clamped first and the snap then runs
    # on the clamped value, so the movement is partly the clamp's. A window is
    # the snap ALONE only when nothing else had anything to do.
    snap_only = (
        bc._straddled(words, end) is not None      # the snap has work
        and bc._straddled(words, start) is None    # the start snap does not
        and lo <= end - start <= hi                # neither duration bound does
        and end <= ceiling + 1e-6
        and abs(fitted_start - start) < 1e-6       # and the start did not move
    )
    inside, _b, _a = _neighbourhood(words, fitted_start, fitted_end)
    out = copy.deepcopy(cand)
    out["start"] = round(fitted_start, 3)
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
    return out, snap_only


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
    def _picked(rows: list[dict]) -> tuple[list[int], int]:
        """`(candidate indices the judge would be asked about, group count)`.

        BY INDEX, not by `moment_id`. That id falls back to the window's
        MIDPOINT when there is no payoff, so moving an end changes it — and a
        before/after comparison keyed on it would report every moved window as a
        different moment whether or not anything about the selection changed.
        The index is what stays the same thing, and `build_groups` already
        returns its members AS indices — the first version of this tagged the
        candidates and looked for the tag on dicts that were never dicts, so it
        recovered nothing and reported "0 of 0, membership same".
        """
        groups = candidate_groups.build_groups(rows)
        picked = candidate_groups.build_shortlist(groups, duration=duration)
        out: set[int] = set()
        ordered: list[int] = []
        for group in picked.get("selected") or []:
            # REPRESENTATIVES, not members. A group of seven cuts of one moment
            # is asked about through the two the shortlist puts forward; using
            # every member measured a set the judge never sees, and made the
            # answer look reassuring by covering the whole corpus.
            for member in group.get("representatives") or []:
                if int(member) not in out:
                    ordered.append(int(member))
                out.add(int(member))
        if not out:
            # 0 against 0 compares equal and says nothing. A shortlist that put
            # nobody forward is a refusal, not a stable selection — and the
            # first version of this produced exactly that, silently.
            raise ValueError("shortlist put no window forward")
        return ordered, len(groups)

    try:
        a, groups_before = _picked(before)
        b, groups_after = _picked(after)
    except Exception as exc:
        return {"refused": type(exc).__name__}
    return {"windows_before": len(a), "windows_after": len(b),
            "groups_before": groups_before, "groups_after": groups_after,
            "membership_changed": sorted(set(a) ^ set(b)),
            # The ORDER too. The shortlist is a budget spent in order, so two
            # runs with the same members and a different order are two different
            # questions put to the judge.
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

    # REFUSED, NOT DEFAULTED. `read_artifact(...) or {}` and `duration or 0.0`
    # turn a missing input into a measurement taken against nothing: every
    # signal-derived feature reads zero, every duration-relative one divides by
    # a floor, and the report comes back with numbers that describe the absence
    # rather than the source. A project missing any of them cannot be compared
    # and says so.
    signals = storage.read_artifact(project_id, "signals")
    if not isinstance(signals, dict) or not signals:
        return {"project": project_id, "refused": "no_signals_artefact"}
    duration = float(project.duration or 0.0)
    # `NaN <= 0` is False, so a NaN sailed through a check that looks like it
    # covers everything. Finite first, then positive.
    if not math.isfinite(duration) or duration <= 0:
        return {"project": project_id, "refused": "no_duration_on_project"}
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
    if not words:
        return {"project": project_id, "refused": "transcript_has_no_word_times"}
    # Read by `remeasure` to resolve back-references. Absent on the legacy path,
    # where the story block does not exist either — but a corpus that HAS story
    # candidates and no atoms would be remeasured against nothing, on both
    # sides, and the null result would be an artefact of the missing file.
    atoms = storage.read_artifact(project_id, "atoms")
    if any(isinstance(c.get("story"), dict) for c in rows) and not atoms:
        return {"project": project_id, "refused": "story_candidates_without_atoms"}

    # BOTH SIDES REMEASURED, for the reason both sides are re-scored: the story
    # numbers stored in the artefact are whatever `story_evidence` computed when
    # it was written, and leaving them on the "before" column would attribute
    # every change that module has had since to this batch.
    before = []
    for cand in rows:
        c = copy.deepcopy(cand)
        if isinstance(c.get("story"), dict):
            story_evidence.remeasure(c, atoms=atoms)
        before.append(c)
    after = []
    moved: list[int] = []
    contaminated: list[int] = []
    for i, cand in enumerate(rows):
        refitted, snap_only = _refitted(cand, words, lo=lo, hi=hi,
                                        ceiling=ceiling, atoms=atoms)
        if refitted is None:
            # SYMMETRY. A window that did not move still has to go through the
            # same remeasure as its twin on the other side, or the two columns
            # differ by a module version on exactly the windows this batch does
            # not touch — which is the loudest possible way to fake a null
            # result and the quietest way to fake a real one.
            unmoved = copy.deepcopy(before[i])
            after.append(unmoved)
            continue
        after.append(refitted)
        moved.append(i)
        if not snap_only:
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
        # NOT the board. It coincides numerically with the board's capacity and
        # the population is different — these are windows ranked by heuristic
        # score, and the board is moments after grouping, shortlisting and
        # judging. Named for what it is.
        "top_clip_count_windows": _window(target_count),
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
        # Windows `_fit` moved where something OTHER than the snap could have
        # done it. Must be zero, or the comparison is measuring more than this
        # batch.
        "contaminated": contaminated,
        "scores_changed": len(changed),
        # A window whose end never moved and whose score did means the two
        # columns differ by something other than the snap: contamination, or a
        # scorer that is not deterministic. Worth seeing, never expected, and it
        # fails the run either way.
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
        print(f"{'':14} {'judge shortlist':16} "
              f"{sl['windows_before']}->{sl['windows_after']} representatives "
              f"in {sl['groups_before']}->{sl['groups_after']} groups, "
              f"membership "
              f"{'CHANGED' if sl['membership_changed'] else 'same':8} order "
              f"{'changed' if sl['order_changed'] else 'same'}")
    if row.get("invalid"):
        print(f"{'':14} {row['invalid']} entries are not records — the corpus is "
              f"smaller than the file")
    if row.get("contaminated"):
        print(f"{'':14} CONTAMINATED: {len(row['contaminated'])} window(s) "
              f"moved where a rule other than the snap could have done it, so "
              f"the delta is not this batch's alone")
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
    # A shortlist that could not be built is a measurement that did not happen,
    # and it was coming back green because only PROJECT-level refusals reached
    # the exit code. Every way of not knowing has to fail the run.
    unbuilt = sum(1 for r in rows if (r.get("shortlist") or {}).get("refused"))
    # A window whose SCORE changed while its end did not is proof that the two
    # columns differ by more than this batch — either the comparison is
    # contaminated or the scorer is not deterministic. It does NOT by itself
    # show the scorer reading outside its own window; that is one explanation
    # among several, and the earlier comment named it as if it were the finding.
    # Whichever it is, the answer is not this batch's, so the run fails.
    leaked = sum(len(r.get("changed_without_moving") or []) for r in rows)
    code = 2 if (refused or invalid or contaminated or unbuilt or leaked) else 0

    if args.json:
        print(json.dumps(rows, indent=2))
        return code

    for row in rows:
        _report(row)
    moved = sum(r.get("moved", 0) for r in rows)
    shifted = [r["project"] for r in rows
               if (r.get("shortlist") or {}).get("membership_changed")
               or (r.get("shortlist") or {}).get("refused")]
    # SEPARATELY, because on the pilots reordering is the whole finding and
    # membership is not — folding them together reported the quiet half.
    reordered = [r["project"] for r in rows
                 if (r.get("shortlist") or {}).get("order_changed")]
    print(f"\n{'POOLED':14} {moved} windows moved over "
          f"{sum(r.get('candidates', 0) for r in rows)}")
    print(f"{'':14} judge shortlist membership changed in "
          f"{len(shifted)} project(s){': ' + ', '.join(shifted) if shifted else ''}")
    print(f"{'':14} judge shortlist REORDERED in {len(reordered)} project(s)"
          f"{': ' + ', '.join(reordered) if reordered else ''}")
    if refused:
        print(f"{'':14} {refused} project(s) could not be measured at all")
    if invalid:
        print(f"{'':14} {invalid} unreadable entries across the corpus")
    if unbuilt:
        print(f"{'':14} {unbuilt} project(s) whose judge shortlist could not be "
              f"built at all")
    if leaked:
        print(f"{'':14} {leaked} window(s) changed score without moving — the "
              f"comparison is contaminated or the scorer is not deterministic, "
              f"and either way the delta is not this batch's alone")
    if contaminated:
        print(f"{'':14} {contaminated} end(s) moved for reasons other than the "
              f"snap — the delta is not this batch's alone")
    print(f"{'':14} This is the SCORER, the grouping and the deterministic "
          f"shortlist. The judge's verdict and the delivered board are not "
          f"measured here and cannot be without a re-score on a clone.")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
