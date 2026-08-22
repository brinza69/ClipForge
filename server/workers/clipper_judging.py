"""
ClipForge — AI Stream Clipper: running the judge over pools of moments.

Split from clipper_build.py, which crossed the repo's 500-line limit again as
Batch 5 and Batch 2b added the moment pool and the rounds. One concern: WHICH
moments get asked about, how many times, and what happens to the verdict
afterwards.

The pool is a budget, not a slice. `sorted(cands, -overall)[:80]` ranked
variants rather than moments and ranked them by the score the judge exists to
correct; measured on the four-hour source it showed the judge 9% of the field
and one story moment out of 75.
"""

from __future__ import annotations

import logging
from typing import Any

from config import settings
from job_queue import JobCancelledError
from services.clipper import selection

logger = logging.getLogger("clipforge.clipper.build")


def _judge_pool(refined: list[dict], duration: float, trace: Any,
                *, exclude: set[str] | None = None,
                groups: list[dict] | None = None) -> dict | None:
    """The moments the judge is asked about. None means "use the old slice".

    A shortlist that could break the judging pass would be a bad trade: the
    pass it replaces still works, it is just worse.
    """
    from services.clipper import candidate_groups as groups_mod
    from services.clipper import llm_select

    try:
        out = groups_mod.build_pool(
            refined, duration=duration, budget=llm_select.MAX_JUDGE_CLIPS,
            overlap_threshold=float(settings.clipper_overlap_threshold),
            text_threshold=float(settings.clipper_text_similarity_threshold),
            exclude=exclude, groups=groups)
    except Exception:  # noqa: BLE001
        logger.warning("shortlist failed; judging the top scores instead",
                       exc_info=True)
        return None

    if trace is not None:
        trace.note_count("moment_groups", out["groups"])
        trace.note_count("judge_pool_moments", len(out["pool"]))
        trace.note_count("judge_pool_story", out["story"])
        # The denominators for the line above, and where the story moments sit
        # on the clock. Recorded for every run, not only when someone thinks to
        # measure: `judge_pool_story` alone cannot distinguish "the field held
        # 20 story moments and 19 were judged" from "it held 200".
        trace.note_count("story_groups", out["story_groups"])
        trace.note_count("story_grounded", out["story_grounded"])
        trace.note_count("story_uncertain", out["story_uncertain"])
        # A distribution, so it goes where the shortlist categories go —
        # `counts` holds scalars, and widening it for one list would be a new
        # trace API for a single caller.
        trace.note_stage("story_spread", "measured",
                         "/".join(str(n) for n in out["story_quarters"]))
        trace.note_stage("shortlist", "built", ", ".join(
            f"{k}={v}" for k, v in sorted(out["categories"].items())))
    logger.info("clipper: %d moments from %d candidates, %d in the judge pool "
                "(%s)", out["groups"], len(refined), len(out["pool"]),
                out["categories"])
    return out


async def _judge_rounds(refined: list[dict], duration: float, want: int,
                        cfg: dict, trace: Any, queue, job_id: str) -> bool:
    """Judge pools until enough moments are chosen, or the cap is reached.

    Returns whether any verdict exists at all. A second round asks about
    moments the first pool never reached — the shortlist is a budget, and a
    budget that came up short is a reason to spend another one, not a reason to
    fall back to comparing raw scores.

    Capped at `selection.MAX_POOL_ROUNDS`, because the alternative on a
    295-moment source is walking the whole field eighty at a time.
    """
    from services.clipper import candidate_groups as groups_mod
    from services.clipper import llm_select

    weight = float(settings.clipper_llm_weight)
    # The legacy score, frozen before a single verdict exists. Everything the
    # judge writes goes to its own fields; this is what the legacy board reads
    # and what shadow promises not to move.
    for cand in refined:
        cand.setdefault("heuristic_score", cand.get("overall"))
    seen: set[str] = set()
    groups: list[dict] | None = None
    judged = False

    for round_index in range(selection.MAX_POOL_ROUNDS):
        # The grouping is computed ONCE, before any verdict exists, and reused.
        # `dedupe._group` elects leaders by `overall`, which `apply_ranking`
        # rewrites for the first pool — so regrouping between rounds can give a
        # moment a different leader and, for a legacy candidate, a different id
        # halfway through its own run.
        chosen = _judge_pool(refined, duration, trace, exclude=seen,
                             groups=groups)
        if not chosen or not chosen["pool"]:
            break
        try:
            hit = await llm_select.judge(
                refined, weight=weight, want=want,
                model=(cfg.get("llm_judge_model")
                       or settings.clipper_llm_judge_model or None),
                trace=trace, shortlist=chosen["pool"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM judging failed; keeping the heuristic ranking",
                           exc_info=True)
            trace.note_error("judge", exc)
            break
        if not hit:
            # No verdict at all. Atomic: the field keeps its heuristic order
            # rather than half of it moving to a different scale.
            break
        judged = True
        groups = chosen["all_groups"]
        groups_mod.propagate_verdicts(refined, groups, weight=weight)
        # Which round produced this verdict. Every round ranks its own pool
        # from 1, so the numbers are only comparable within a round; the board
        # sorts on `(judge_round, llm_rank)` for exactly that reason.
        for cand in refined:
            if cand.get("llm_score") is not None and not cand.get("judge_round"):
                cand["judge_round"] = round_index + 1
        seen |= {g["moment_id"] for g in chosen["selected_groups"]
                 if g.get("moment_id")}
        trace.note_count("pool_rounds", round_index + 1)
        if selection.enough_selected(refined, want):
            break
        # Inlined rather than importing `_guard` from clipper_build: that
        # module imports this one, and the check is two lines.
        if queue.is_cancelled(job_id):
            raise JobCancelledError("Cancelled by user.")
        await queue.update_progress(job_id, 0.50, "Judging a second pool")
    return judged
