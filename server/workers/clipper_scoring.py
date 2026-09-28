"""Scoring one run's candidates: what each is worth, and on which scale.

Split out of `clipper_build.py` when Batch R2's confidence propagation pushed
that file past the repo's 500-line limit. The seam is the phase itself: this is
everything between "the windows are chosen" and "the board is drawn" — feature
extraction, the local content verdict, the heuristic score, and the optional
blend with the learned ranker.

FOUR NAMES, FOUR MEANINGS, and the reason they are separate travels with the
code: `overall` used to be all of them at once — the heuristic, then the
heuristic blended with the ranker, then that blended with the judge — and by
the time a candidate reached the board nothing could tell which reading it
held.
"""

from __future__ import annotations

from typing import Sequence

from config import settings
from database import async_session
from services.clipper import candidates as cand_mod
from services.clipper import ranker, scoring
from services.clipper import segment_type as seg_type_mod
# THE canonical one, not a copy. The copy this replaced dropped the finite
# check, so a NaN `start` produced a NaN midpoint and the candidate matched no
# stretch at all — a behaviour change smuggled in by a split that was supposed
# to move code, not alter it.
from services.clipper.candidate_terms import _num


async def use_learned_ranker() -> tuple[dict | None, bool]:
    """The learned model, and whether this run may use it.

    Separate from the scoring loop because it asks the DB a question, once, and
    the loop below must stay DETERMINISTIC and DB-free — not pure: it scores
    the candidates in place, which is what every caller here expects.
    """
    model = ranker.load_model() if settings.clipper_ranker_enabled else None
    if not model:
        return None, False
    async with async_session() as session:
        from services.clipper import feedback

        rows = await feedback.training_rows(session)
    return model, ranker.should_use_learned(model, len(rows))


def score_candidates(candidates: Sequence[dict], *, transcript: dict, signals: dict,
                     duration: float, profile: str, platform: str,
                     seg_types: Sequence[dict], overridden: bool,
                     model: dict | None = None, use_learned: bool = False) -> None:
    """Score every candidate in place.

    `overridden` says a PERSON set the project's content type. It travels
    separately from the verdict because the provenance is what
    `edit_profiles.resolve` trusts in place of a confidence nobody measured.
    """
    for cand in candidates:
        features = cand_mod.extract_features(cand, transcript, signals, duration)
        # The profile of the stretch this candidate sits in, at its midpoint —
        # WITH its confidence, since R2. The edit profile refuses to grant a
        # bolder grammar to a classification nobody measured, and it can only
        # tell "low" from "never measured" if the number travels with the label.
        verdict = seg_type_mod.verdict_at(
            seg_types, (_num(cand.get("start")) + _num(cand.get("end"))) / 2.0,
            profile)
        here = verdict["content_type"]
        cand["content_type"] = here
        cand["content_confidence"] = verdict["confidence"]
        cand["content_type_origin"] = (
            seg_type_mod.FROM_OVERRIDE if overridden
            else verdict["origin"])
        scored = scoring.score_candidate(cand, features, profile=here, platform=platform)
        cand["features"] = features
        cand["sub_scores"] = scored["sub_scores"]
        cand["reason"] = scored["reason"]
        # FOUR NAMES, FOUR MEANINGS. `overall` used to be all of them at once:
        # the heuristic, then the heuristic blended with the ranker, then that
        # blended with the judge — and by the time a candidate reached the board
        # nothing could tell which reading it held. Each now has its own field
        # and keeps its value; `overall` remains the blended number every
        # existing reader expects, but it is no longer the only record.
        heuristic = float(scored["overall"])
        cand["heuristic_score"] = round(heuristic, 2)
        cand["eligibility"] = scored["eligibility"]
        if use_learned:
            # Blend rather than replace: the learned model is trained on this
            # user's taste but on a small dataset, so the transparent heuristic
            # keeps half the vote.
            learned = ranker.predict(model, features) * 100.0
            cand["learned_score"] = round(learned, 2)
            cand["overall"] = round(0.5 * heuristic + 0.5 * learned, 2)
            cand["ranker_version"] = model.get("version")
        else:
            cand["overall"] = round(heuristic, 2)
            cand["ranker_version"] = "heuristic-1"
