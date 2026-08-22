"""
ClipForge — AI Stream Clipper: what happens to a verdict after the judge gives it.

Split from `candidate_groups.py`, which crossed the repo's 500-line limit when
the story census landed. The seam is the one that module's own docstring draws:
it says "candidates in, groups and a shortlist out" — deciding WHO gets judged.
Everything here runs after the answer comes back, and it mutates the field the
board reads. Two different phases, and the smaller one moved.

Moved verbatim. The only edits are the imports it needs to stand alone.
"""

from __future__ import annotations

from typing import Any, Sequence


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# What `apply_ranking` writes onto a candidate it ranked.
_VERDICT_FIELDS = ("llm_score", "judge_score", "llm_rank", "llm_verdict",
                   "llm_reason")


def propagate_verdicts(refined: Sequence[dict], groups: Sequence[dict], *,
                       weight: float) -> int:
    """Give every cut of a judged moment the verdict its moment received.

    WITHOUT THIS the moment/variant split is only half wired, and the half that
    is missing is the one the board reads. The judge scores one representative
    per moment; dedupe and diversity then pick winners from the WHOLE field, so
    a winner that happens to be a different cut of a judged moment carries no
    verdict at all. Measured on the four-hour source before this existed: 7 of
    the 10 winners were `not_evaluated`, even though their moments had been
    judged — the pool got better and the board did not notice.

    The siblings are blended with the SAME weight against their OWN heuristic
    score, not handed the representative's blended `overall`. Copying that
    would put one cut's number on another cut, which is the scale-mixing this
    plan exists to remove.

    THE BLEND GOES TO `selection_score`, NEVER TO `overall`. `overall` is what
    the legacy board reads, and shadow mode promises that board does not move.
    Writing there made shadow perturb the very baseline it exists to be
    compared against: 285 candidates were blended down on the four-hour source
    and the two boards converged from 7 differences to 1 — not because v2 had
    got closer to legacy, but because legacy had been moved.

    Returns how many candidates inherited a verdict.
    """
    weight = min(1.0, max(0.0, float(weight)))
    touched = 0
    for group in groups or ():
        reps = [refined[i] for i in group.get("representatives") or []]
        judged = next((r for r in reps if r.get("llm_rank") is not None), None)
        if judged is None:
            # The moment was ASKED about and refused. Its verdict has to reach
            # the siblings too, or they stay `not_evaluated` and the board rule
            # treats them as never seen — which makes them eligible for the
            # backfill that is supposed to exclude exactly this. A moment the
            # judge looked at and said no to would have come back through a cut
            # of itself.
            declined = next((r for r in reps if r.get("llm_score") is not None),
                            None)
            if declined is not None:
                touched += _spread_refusal(refined, group, declined, weight)
            continue
        for index in group.get("members") or []:
            cand = refined[index]
            if cand is judged or cand.get("llm_rank") is not None:
                continue
            for field in _VERDICT_FIELDS:  # noqa: PERF203
                if judged.get(field) is not None:
                    cand[field] = judged[field]
            cand["moment_id"] = group.get("moment_id")
            cand["verdict_inherited"] = True
            cand["selection_score"] = _blend(cand, judged, weight)
            touched += 1
    return touched


def _spread_refusal(refined: Sequence[dict], group: dict, declined: dict,
                    weight: float) -> int:
    """Mark every cut of a REFUSED moment as refused, and blend it down.

    Same arithmetic as a selected moment's, against each cut's own heuristic
    score: the refusal is worth `llm_score` (zero, from `apply_ranking`), not
    the leader's blended number.
    """
    touched = 0
    for index in group.get("members") or []:
        cand = refined[index]
        if cand is declined or cand.get("llm_score") is not None:
            continue
        cand["llm_score"] = declined["llm_score"]
        cand["judge_score"] = declined.get("judge_score", declined["llm_score"])
        cand["moment_id"] = group.get("moment_id")
        cand["verdict_inherited"] = True
        cand["selection_score"] = _blend(cand, declined, weight)
        touched += 1
    return touched


def _blend(cand: dict, verdict: dict, weight: float) -> float:
    """A candidate's own heuristic score, blended with its moment's verdict.

    Reads `heuristic_score` when the run captured one and falls back to
    `overall`, which is what a legacy artifact has and what a run without a
    judge never changes.
    """
    base = cand.get("heuristic_score")
    base = _num(base if base is not None else cand.get("overall"))
    return round((1.0 - weight) * base + weight * _num(verdict.get("llm_score")), 2)
