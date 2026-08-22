"""Which moments reach the board, and what happens when too few do.

The measurement this closes. `apply_ranking` blends the judge's verdict into
`overall` for the candidates it ranked and zeroes the ones it declined — but
only inside the pool it was given. Everything outside kept an UNBLENDED
heuristic score, so two incompatible scales competed for the same board.

On the four-hour audited source, after the moment pool and verdict propagation
landed: 7 of the 10 winners were `not_evaluated`. All seven were legacy
candidates from moments never in the pool, winning on a raw number while every
judged candidate had been blended down. A candidate the judge explicitly
declined could lose to one it had never seen.
"""

from __future__ import annotations

from services.clipper import selection


def _cand(score, *, rank=None, llm=None, moment=None):
    cand = {"overall": float(score)}
    if rank is not None:
        cand["llm_rank"] = rank
    if llm is not None:
        cand["llm_score"] = llm
    if moment is not None:
        cand["moment_id"] = moment
    return cand


# ── Three states, told apart by the right field ──────────────────────────────


def test_a_ranked_candidate_penalised_to_zero_is_still_selected():
    """`apply_ranking` gives last place a rank-derived 0.0 and then subtracts a
    penalty per reject reason. Reading the status off the SCORE made a candidate
    the judge ranked and criticised look like one it never saw."""
    assert selection.status_of(_cand(50, rank=80, llm=0.0)) == selection.SELECTED


def test_the_three_states_are_distinguishable():
    assert selection.status_of(_cand(50, rank=1, llm=90.0)) == selection.SELECTED
    assert selection.status_of(_cand(50, llm=0.0)) == \
        selection.NOT_SELECTED_IN_JUDGED_POOL
    assert selection.status_of(_cand(50)) == selection.NOT_EVALUATED


def test_marking_stamps_every_candidate_and_counts():
    cands = [_cand(50, rank=1, llm=90.0), _cand(40, llm=0.0), _cand(30)]
    tally = selection.mark(cands)
    assert [c["judge_status"] for c in cands] == [
        selection.SELECTED, selection.NOT_SELECTED_IN_JUDGED_POOL,
        selection.NOT_EVALUATED]
    assert tally[selection.SELECTED] == 1


# ── The rule ─────────────────────────────────────────────────────────────────


def test_an_unjudged_candidate_cannot_beat_a_judged_one():
    """The defect, in one test. The legacy candidate has the higher raw number
    precisely BECAUSE it was never blended."""
    judged = _cand(30.0, rank=1, llm=90.0, moment="m1")
    unseen = _cand(60.7, moment="m2")     # the measured maximum among unjudged
    out = selection.board([unseen, judged], want=1, judged=True)
    assert out["winners"] == [judged]


def test_a_moment_the_judge_declined_does_not_reach_the_board():
    declined = _cand(58.0, llm=0.0, moment="m1")
    out = selection.board([declined], want=3, judged=True)
    assert out["winners"] == []
    assert out["backfilled"] == 0


def test_the_board_follows_the_judges_order_not_the_score():
    first = _cand(10.0, rank=1, llm=99.0, moment="m1")
    second = _cand(95.0, rank=2, llm=50.0, moment="m2")
    out = selection.board([second, first], want=2, judged=True)
    assert out["winners"] == [first, second]


def test_with_no_verdict_nothing_changes():
    """The rule applies to a judged run or not at all. A run with no model is
    exactly what shipped before any of this."""
    best = _cand(90.0)
    out = selection.board([_cand(10.0), best], want=1, judged=False)
    assert out["winners"] == [best]


def test_the_min_score_floor_still_never_empties_a_legacy_board():
    """Keep the best one and let the score speak for itself."""
    out = selection.board([_cand(5.0), _cand(3.0)], want=3, judged=False,
                          min_score=90.0)
    assert len(out["winners"]) == 1


# ── Backfill ─────────────────────────────────────────────────────────────────


def test_a_short_board_is_filled_from_moments_the_judge_never_saw():
    picked = _cand(20.0, rank=1, llm=90.0, moment="m1")
    unseen = _cand(70.0, moment="m2")
    out = selection.board([picked, unseen], want=2, judged=True)
    assert out["winners"] == [picked, unseen]
    assert out["backfilled"] == 1
    assert unseen["board_reason"] == selection.BACKFILL


def test_backfill_never_takes_a_moment_the_judge_refused():
    """A moment it looked at and said no to does not come back through a side
    door — that is the judgement the whole rule exists to respect."""
    picked = _cand(20.0, rank=1, llm=90.0, moment="m1")
    refused = _cand(99.0, llm=0.0, moment="m2")
    out = selection.board([picked, refused], want=5, judged=True)
    assert out["winners"] == [picked]
    assert out["backfilled"] == 0


def test_backfill_is_marked_so_it_can_be_counted_later():
    unseen = [_cand(float(i), moment=f"m{i}") for i in range(3)]
    out = selection.board(unseen, want=2, judged=True)
    assert out["backfilled"] == 2
    assert all(c.get("board_reason") == selection.BACKFILL
               for c in out["winners"])


# ── Rounds ───────────────────────────────────────────────────────────────────


def test_enough_selected_counts_moments_not_cuts():
    """Several cuts of one moment inherit its verdict, and a board cannot be
    filled with four cuts of the same joke."""
    cuts = [_cand(50, rank=1, llm=90.0, moment="m1") for _ in range(4)]
    assert not selection.enough_selected(cuts, want=2)
    assert selection.enough_selected(cuts, want=1)


def test_enough_selected_ignores_the_ones_the_judge_declined():
    cands = [_cand(50, rank=1, llm=90.0, moment="m1"),
             _cand(50, llm=0.0, moment="m2")]
    assert not selection.enough_selected(cands, want=2)


def test_the_round_cap_is_a_stated_number_not_a_loop():
    """Eighty moments a round: a 295-moment source would otherwise walk the
    whole field one pool at a time."""
    assert selection.MAX_POOL_ROUNDS == 2


# ── Nothing at all ───────────────────────────────────────────────────────────


def test_an_empty_field_is_an_empty_board():
    out = selection.board([], want=8, judged=True)
    assert out["winners"] == [] and out["backfilled"] == 0


def test_junk_in_the_field_is_ignored_rather_than_raising():
    out = selection.board([None, "nonsense", _cand(10.0, rank=1, llm=5.0)],
                          want=3, judged=True)
    assert len(out["winners"]) == 1


def test_a_judged_candidate_demoted_by_dedupe_still_reaches_the_board():
    """Found on the corpus, not by a test. `deduplicate` elects group leaders by
    `overall` — the field the verdict is blended INTO — so a judged candidate,
    blended down from its raw heuristic number, loses its own group to an
    unjudged sibling and is flagged `is_alternative`. Of 119 candidates the
    judge had selected, 3 survived that flag.
    """
    demoted = _cand(30.0, rank=1, llm=90.0, moment="m1")
    demoted["is_alternative"] = True
    loud = _cand(70.0, moment="m2")
    out = selection.board([loud, demoted], want=1, judged=True)
    assert out["winners"] == [demoted]
    assert out["backfilled"] == 0


def test_two_cuts_of_one_moment_do_not_both_win():
    """The grouping is still used — just for de-duplication, not as a filter."""
    first = _cand(50.0, rank=1, llm=90.0, moment="m1")
    same = _cand(40.0, rank=2, llm=80.0, moment="m1")
    other = _cand(30.0, rank=3, llm=70.0, moment="m2")
    out = selection.board([first, same, other], want=3, judged=True)
    assert out["winners"] == [first, other]


def test_candidates_with_no_grouping_are_never_collapsed_together():
    """A shared default key would turn an entire legacy board into one entry."""
    out = selection.board([_cand(9.0), _cand(8.0), _cand(7.0)],
                          want=3, judged=True)
    assert len(out["winners"]) == 3


# ── The five integration defects, each with the test that was missing ────────


def test_only_story_v2_orders_the_board_with_the_rule():
    """The activation was INVERTED. `judged and not shadow` applied the rule in
    story_v1 and llm_nominate - modes that never asked for it - and skipped it
    in shadow, the one mode written for it. A story_v1 user would have had
    their board silently reordered by a rule compared against nothing."""
    for mode in ("legacy", "llm_nominate", "story_v1", "story_v2_shadow"):
        assert not selection.rule_applies(mode, judged=True), mode
    assert selection.rule_applies("story_v2", judged=True)
    assert not selection.rule_applies("story_v2", judged=False), "no verdict, no rule"


def test_each_mode_ships_the_board_it_promises():
    """Behaviour, not source text: run the choice each mode makes and look at
    the winners it produces."""
    judged_low = _cand(10.0, rank=1, llm=90.0, moment="m1")
    loud_unseen = _cand(90.0, moment="m2")
    field = [loud_unseen, judged_low]

    def board_for(mode):
        return selection.board(
            field, want=1, judged=selection.rule_applies(mode, True))["winners"]

    assert board_for("story_v1") == [loud_unseen], "story_v1 keeps legacy"
    assert board_for("story_v2_shadow") == [loud_unseen], "shadow ships legacy"
    assert board_for("story_v2") == [judged_low], "only v2 applies the rule"


def test_a_rescued_winner_does_not_stay_flagged_as_an_alternative():
    """The rule exists to rescue candidates dedupe demoted, and `_write_clips`
    stores anything still flagged as an alternative - so the rescue produced a
    winner that never reached the board."""
    demoted = _cand(30.0, rank=1, llm=90.0, moment="m1")
    demoted["is_alternative"] = True
    loser = _cand(70.0, moment="m2")
    field = [loser, demoted]

    out = selection.board(field, want=1, judged=True)
    selection.apply_board(field, out["winners"])

    assert demoted["is_alternative"] is False, "the flag must be CLEARED"
    assert demoted["rank_position"] == 1
    assert loser["is_alternative"] is True
    assert loser["rank_position"] is None


def test_a_scored_answer_still_expresses_a_selection():
    """`apply_scores` set `llm_score` and never `llm_rank`, so every candidate
    in a scored pool read as `not_selected_in_judged_pool` — the judge had
    spoken and the board came out empty."""
    from services.clipper.llm_select import apply_scores

    cands = [{"overall": 50.0}, {"overall": 50.0}]
    apply_scores(cands, [{"id": 0, "score": 20}, {"id": 1, "score": 90}])
    assert selection.status_of(cands[1]) == selection.SELECTED
    assert cands[1]["llm_rank"] == 1, "the best score ranks first"
    assert cands[0]["llm_rank"] == 2


def test_a_refused_moment_cannot_return_through_one_of_its_own_cuts():
    """`propagate_verdicts` only spread a SELECTED verdict, so the other cuts of
    a refused moment stayed `not_evaluated` — and backfill takes from exactly
    that pile. The moment the judge said no to came back through a sibling."""
    from services.clipper import candidate_groups as cg

    shared = "warden diamond furnace clutch ladder anvil beacon coral drip"
    rep = {"start": 100.0, "end": 130.0, "overall": 40.0, "text": shared,
           "story": {"payoff_t": 120.0, "archetypes": [], "grounding": {}}}
    sibling = {"start": 104.0, "end": 134.0, "overall": 95.0, "text": shared,
               "story": {"payoff_t": 120.0, "archetypes": [], "grounding": {}}}
    cands = [rep, sibling]
    out = cg.build_pool(cands, duration=1000.0, budget=10)
    out["pool"][0].update({"llm_score": 0.0})      # judged, and declined

    cg.propagate_verdicts(cands, out["all_groups"], weight=0.5)
    assert selection.status_of(sibling) == selection.NOT_SELECTED_IN_JUDGED_POOL

    board = selection.board(cands, want=3, judged=True)
    assert board["winners"] == [] and board["backfilled"] == 0


def test_the_board_numbers_its_own_winners():
    """`deduplicate` assigns `rank_position` to the winners IT elected and 0 to
    everything else, and this rule elects a different set. Without its own
    numbering a rescued winner reached the board carrying `rank_position=0`, so
    the v2 order never arrived in the DB, the API or auto-export."""
    demoted = _cand(30.0, rank=1, llm=90.0, moment="m1")
    demoted["is_alternative"] = True
    demoted["rank_position"] = 0
    second = _cand(40.0, rank=2, llm=80.0, moment="m2")
    out = selection.board([second, demoted], want=2, judged=True)
    assert [c["rank_position"] for c in out["winners"]] == [1, 2]


def test_two_rounds_are_ordered_by_round_then_by_rank():
    """Each round ranks its OWN pool from 1, so a second round's #1 and a first
    round's #1 are two different claims. Sorting on the number alone interleaves
    them as though they had been compared, which they never were."""
    first_round = _cand(10.0, rank=1, llm=90.0, moment="m1")
    first_round["judge_round"] = 1
    second_round = _cand(99.0, rank=1, llm=95.0, moment="m2")
    second_round["judge_round"] = 2
    out = selection.board([second_round, first_round], want=2, judged=True)
    assert out["winners"] == [first_round, second_round]


def test_a_verdict_with_no_round_recorded_sorts_as_the_first_round():
    """Every legacy artifact and every single-round run has no `judge_round`."""
    a = _cand(10.0, rank=2, llm=80.0, moment="m1")
    b = _cand(10.0, rank=1, llm=90.0, moment="m2")
    out = selection.board([a, b], want=2, judged=True)
    assert out["winners"] == [b, a]
