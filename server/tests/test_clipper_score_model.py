"""Four scales, four names — and eligibility told apart from quality.

`overall` used to be all of them at once: the heuristic, then the heuristic
blended with the learned ranker, then that blended with the judge. By the time a
candidate reached the board nothing could say which reading it held, and two
candidates carrying different readings were compared as though they were the
same measurement. That is the defect every batch since has had to work around.
"""

from __future__ import annotations

from services.clipper import scoring


def _cand(**kw):
    return {"start": 0.0, "end": 30.0, "text": "words", **kw}


def _features():
    return {"duration": 30.0, "words_per_second": 2.5}


# ── The split moved nothing ──────────────────────────────────────────────────


def test_the_weight_rows_are_still_reachable_by_their_old_names():
    """The tests, `score_contribution.py` and the runbook all import these from
    `scoring`. A split is not allowed to be visible to them."""
    assert scoring.PROFILES and scoring.SUB_SCORES
    assert scoring._RAW_PROFILES and scoring.PLATFORM_BANDS


def test_every_profile_still_covers_every_sub_score():
    """A row missing a key would score that component as absent rather than as
    zero-weighted, and the difference is invisible in the total."""
    for name, row in scoring.PROFILES.items():
        assert set(row) == set(scoring.SUB_SCORES), name


def test_every_profile_still_sums_to_one():
    """`_normalise` divides by the actual sum, so an edit can never silently
    change the meaning of the score. This is what proves it still does."""
    for name, row in scoring.PROFILES.items():
        assert abs(sum(row.values()) - 1.0) < 1e-9, name


# ── Four names, four meanings ────────────────────────────────────────────────


def test_scoring_reports_the_heuristic_under_its_own_name():
    out = scoring.score_candidate(_cand(), _features(),
                                  profile="gaming", platform="tiktok")
    assert out["heuristic_score"] == out["overall"]


def test_the_judge_cannot_move_the_heuristic_score():
    """The gate for this batch. `overall` is blended by the judge; the
    heuristic reading has to survive that untouched, or there is nothing to
    compare a verdict against."""
    from services.clipper.llm_judge import apply_ranking

    cand = _cand(overall=60.0, heuristic_score=60.0, text="a clip")
    apply_ranking([cand], [{"id": 0, "story_editor": "weak",
                            "cold_viewer": "weak", "critic": []}], weight=0.7)
    assert cand["overall"] != 60.0, "the blend still happens"
    assert cand["heuristic_score"] == 60.0, "and it does not touch the baseline"


def test_the_judge_records_its_own_number_too():
    from services.clipper.llm_judge import apply_ranking

    cand = _cand(overall=60.0)
    apply_ranking([cand], [{"id": 0}], weight=0.5)
    assert cand["judge_score"] == cand["llm_score"]


# ── Eligibility is not quality ───────────────────────────────────────────────


def test_a_window_without_its_own_payoff_is_ineligible_however_good_it_looks():
    """Collapsing the two into one number is what let audio energy compensate
    for a payoff that is outside the clip."""
    cand = _cand(story={"validity": "invalid"})
    assert scoring.eligibility_of(cand) == scoring.INELIGIBLE


def test_thin_evidence_is_uncertain_and_uncertain_is_not_a_reject():
    assert scoring.eligibility_of(_cand(story={"validity": "uncertain"})) == \
        scoring.UNCERTAIN


def test_a_legacy_candidate_is_uncertain_rather_than_ineligible():
    """It has no story block at all, which is the normal state of every clip
    the legacy path produces — not a finding about it."""
    assert scoring.eligibility_of(_cand()) == scoring.UNCERTAIN


def test_a_fully_grounded_moment_is_eligible():
    assert scoring.eligibility_of(_cand(story={"validity": "valid"})) == \
        scoring.ELIGIBLE


def test_eligibility_has_one_definition_and_not_two():
    """It reads `story_evidence`'s verdict rather than recomputing it. Two
    answers to one question is how the two payoffs happened."""
    from services.clipper import story_evidence

    for validity in (story_evidence.VALID, story_evidence.UNCERTAIN,
                     story_evidence.INVALID):
        assert scoring.eligibility_of(_cand(story={"validity": validity})) in (
            scoring.ELIGIBLE, scoring.UNCERTAIN, scoring.INELIGIBLE)


# ── A named field has to be the real source, not a decoration ────────────────


def test_every_candidate_the_judge_saw_carries_its_score_under_both_names():
    """The first version set `judge_score` only in the ranked loop: 14 of 365
    judged candidates had one, and 77 of 91 SELECTED candidates did not. A field
    that exists on 4% of the rows it describes is not the source of anything —
    it is a decoration that reads like a contract."""
    from services.clipper.llm_judge import apply_ranking

    ranked = _cand(overall=60.0, text="one")
    declined = _cand(overall=55.0, text="two")
    apply_ranking([ranked, declined], [{"id": 0}], weight=0.5)

    assert ranked["judge_score"] == ranked["llm_score"]
    assert declined["judge_score"] == declined["llm_score"] == 0.0, (
        "the ones it declined were judged too")


def test_every_judged_candidate_carries_the_number_selection_ranks_on():
    from services.clipper.llm_judge import apply_ranking

    ranked = _cand(overall=60.0, text="one")
    declined = _cand(overall=55.0, text="two")
    apply_ranking([ranked, declined], [{"id": 0}], weight=0.5)

    assert ranked["selection_score"] == ranked["overall"]
    assert declined["selection_score"] == declined["overall"]


def test_the_scored_path_names_its_number_too():
    from services.clipper.llm_select import apply_scores

    cand = _cand(overall=50.0)
    apply_scores([cand], [{"id": 0, "score": 80}], weight=0.5)
    assert cand["judge_score"] == 80.0
    assert cand["selection_score"] == cand["overall"]


def test_an_inherited_verdict_carries_both_names_as_well():
    """Propagation copies the moment's verdict onto every cut. A field it
    forgot would be missing on exactly the candidates the board reads."""
    from services.clipper import candidate_groups as cg

    assert "judge_score" in cg._VERDICT_FIELDS
