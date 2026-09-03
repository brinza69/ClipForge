"""Dedupe must never compare unnamed mixtures of heuristic and judge scores."""

import math

import pytest

from services.clipper import candidate_groups, dedupe


_MISSING = object()


def _cand(start, *, overall, heuristic=_MISSING, selection=_MISSING,
          text="same words"):
    out = {
        "start": float(start), "end": float(start + 30),
        "overall": float(overall), "text": text,
    }
    if heuristic is not _MISSING:
        out["heuristic_score"] = heuristic
    if selection is not _MISSING:
        out["selection_score"] = selection
    return out


def test_selection_scale_prefers_the_propagated_verdict_not_blended_overall():
    selected = _cand(0, overall=10, heuristic=90, selection=80)
    sibling = _cand(1, overall=95, heuristic=70, selection=20)

    out = dedupe.deduplicate(
        [selected, sibling], overlap_threshold=0.4, text_threshold=0.62,
        target_count=1, winner_scale=dedupe.SELECTION_SCORE)

    assert out[0] is selected
    assert selected["is_alternative"] is False
    assert sibling["is_alternative"] is True


def test_judge_grouping_stays_on_the_frozen_heuristic_scale():
    stronger = _cand(0, overall=10, heuristic=90, selection=20)
    louder_blend = _cand(1, overall=95, heuristic=70, selection=95)

    group = candidate_groups.build_groups([stronger, louder_blend])[0]

    assert group["representatives"] == [0]
    assert group["best_score"] == 90
    assert group["score_scale"] == dedupe.HEURISTIC_SCORE


def test_selection_changes_the_leader_without_changing_group_topology():
    # A overlaps B, B overlaps C, A does not overlap C. If B's selection score
    # were allowed to lead grouping, greedy grouping would collapse all three.
    a = _cand(0, overall=90, heuristic=90, selection=10, text="a")
    b = _cand(15, overall=80, heuristic=80, selection=100, text="b")
    c = _cand(30, overall=70, heuristic=70, selection=70, text="c")

    out = dedupe.deduplicate(
        [a, b, c], overlap_threshold=0.4, text_threshold=0.99,
        target_count=3, winner_scale=dedupe.SELECTION_SCORE)
    winners = [cand for cand in out if not cand["is_alternative"]]

    assert len(winners) == 2
    assert b in winners, "selection may choose the cut inside A's stable group"
    assert c in winners, "selection must not merge C into that group"


def test_an_unjudged_candidate_falls_back_from_selection_to_heuristic():
    cand = _cand(0, overall=5, heuristic=81)

    assert dedupe.score_of(cand, scale=dedupe.SELECTION_SCORE) == 81


def test_an_old_candidate_without_named_scores_falls_back_to_overall():
    cand = _cand(0, overall=73)

    assert dedupe.score_of(cand, scale=dedupe.HEURISTIC_SCORE) == 73
    assert dedupe.score_of(cand, scale=dedupe.SELECTION_SCORE) == 73


@pytest.mark.parametrize("bad", [None, "bad", math.nan, math.inf, True])
def test_a_declared_invalid_selection_score_does_not_leak_to_another_scale(bad):
    cand = _cand(0, overall=99, heuristic=88, selection=bad)

    assert dedupe.score_of(cand, scale=dedupe.SELECTION_SCORE) == 0


def test_an_unknown_scale_is_refused_instead_of_silently_defaulted():
    with pytest.raises(ValueError, match="unknown dedupe score scale"):
        dedupe.score_of(_cand(0, overall=50), scale="blend_somehow")

    with pytest.raises(ValueError, match="unknown dedupe score scale"):
        dedupe.deduplicate(
            [], overlap_threshold=0.4, text_threshold=0.62, target_count=1,
            winner_scale="blend_somehow")


def test_a_non_record_candidate_is_refused_instead_of_removed_from_the_field():
    with pytest.raises(ValueError, match="non-record"):
        dedupe.deduplicate(
            [_cand(0, overall=50), 7], overlap_threshold=0.4,
            text_threshold=0.62, target_count=1)

    with pytest.raises(ValueError, match="non-record"):
        candidate_groups.build_groups([_cand(0, overall=50), 7])
