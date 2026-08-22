"""Moments rather than variants, and who gets asked about.

The measurement. `judge` saw `sorted(cands, -overall)[:80]`. On the four-hour
audited source that was 80 of 909 and ONE story candidate out of 38. After the
chunk planner landed it became 80 of 957 and one of 75 — raising recall makes
the ceiling more binding, not less.

Two defects live in that one line. It ranks VARIANTS, so one anchor's four cuts
can spend four of the eighty slots arguing with itself. And it ranks by the
HEURISTIC score, which is the thing the judge exists to correct.
"""

from __future__ import annotations

import random

from services.clipper import candidate_groups as cg


# Distinct vocabulary per candidate. Fixtures that all say the same thing
# collapse into one group and prove nothing: text similarity is one of the
# three axes the grouping compares on, and it is doing its job.
_WORDS = ("warden diamond furnace clutch ladder anvil beacon coral drip ember "
          "flint glow harbor ingot jungle kelp lava mossy nether orb portal "
          "quartz redstone slime torch vine wither xp yield zombie").split()


def _talk(seed: int) -> str:
    """Nine words drawn from a 30-word vocabulary, seeded.

    A formula that indexes the list arithmetically only produced 30 distinct
    strings for 300 candidates, so the grouping folded them into 3 — correctly,
    because they really did say the same things. Sampling gives ~14 million
    combinations, which is what a transcript looks like.
    """
    return " ".join(random.Random(seed).sample(_WORDS, 9))


def _cand(start, end, *, score=50.0, payoff=None, thread=None, kinds=(),
          grounded=False, text=None):
    cand = {"start": float(start), "end": float(end), "overall": score,
            "text": text if text is not None else _talk(int(start) + int(end))}
    if payoff is not None:
        cand["story"] = {
            "payoff_t": float(payoff),
            "archetypes": list(kinds),
            "thread_id": thread,
            "payoff_grounded": grounded,
            "grounding": {"payoff": grounded, "context_total": 0,
                          "context_grounded": 0},
        }
    return cand


# ── Moment ids ───────────────────────────────────────────────────────────────


def test_two_cuts_of_one_anchor_are_one_moment():
    """The story path proposes two to four cuts of one anchor. Ranked as
    variants they compete with each other for the pool."""
    tight = _cand(95.0, 120.0, payoff=110.0)
    wide = _cand(60.0, 125.0, payoff=110.0)
    assert cg.moment_id(tight) == cg.moment_id(wide)


def test_ordinary_model_jitter_does_not_split_a_moment():
    """A model naming the same payoff twice rarely picks the same second."""
    assert cg.moment_id(_cand(0, 30, payoff=100.4)) == \
        cg.moment_id(_cand(0, 30, payoff=101.9))


def test_a_payoff_far_enough_away_is_a_different_moment():
    assert cg.moment_id(_cand(0, 30, payoff=100.0)) != \
        cg.moment_id(_cand(0, 30, payoff=140.0))


def test_a_legacy_candidate_still_gets_an_id():
    """No anchor, so the window's midpoint is all there is."""
    assert cg.moment_id(_cand(0.0, 30.0))
    assert cg.moment_id(_cand(0.0, 30.0)) == cg.moment_id(_cand(1.0, 29.0))


def test_the_id_is_the_same_on_a_rerun():
    """Stable within a run exactly, and across runs as far as the payoff it is
    built from is stable. That is the honest limit."""
    cand = _cand(0, 30, payoff=100.0)
    assert cg.moment_id(cand) == cg.moment_id(dict(cand))


# ── Grouping ─────────────────────────────────────────────────────────────────


def test_two_hundred_variants_of_forty_moments_collapse():
    """The shape the pool has to survive: five cuts each of forty anchors."""
    cands = []
    for moment in range(40):
        payoff = 100.0 + moment * 300.0
        for cut in range(5):
            # Same words within a moment, different between moments: exactly
            # what four cuts of one anchor look like.
            cands.append(_cand(payoff - 30 - cut * 5, payoff + 10,
                               payoff=payoff, score=50.0 + cut,
                               text=_talk(moment)))
    groups = cg.build_groups(cands)
    assert len(groups) == 40, f"{len(groups)} groups from 40 moments"
    assert sum(len(g["members"]) for g in groups) == 200


def test_a_group_shows_the_judge_one_cut_not_all_of_them():
    """The judge's question is WHICH MOMENT deserves a slot. The variant is
    chosen afterwards, by the parts that know about rendering."""
    cands = [_cand(90, 120, payoff=110.0, score=40.0),
             _cand(60, 125, payoff=110.0, score=70.0)]
    group = cg.build_groups(cands)[0]
    assert len(group["representatives"]) == 1
    assert group["representatives"][0] == 1, "the best-scoring cut represents it"


def test_a_group_carries_what_the_budget_needs_to_spend_on():
    group = cg.build_groups(
        [_cand(0, 30, payoff=10.0, thread="t1", kinds=("FAIL",),
               grounded=True)])[0]
    assert group["is_story"] and group["grounded"]
    assert group["thread_id"] == "t1" and group["archetypes"] == ["FAIL"]


def test_grouping_nothing_is_not_a_crash():
    assert cg.build_groups([]) == []
    assert cg.build_groups(None) == []


def test_two_moments_that_quantise_together_are_not_merged():
    """An id collision is not a reason to overrule the grouping, which looked
    at the text and the overlap and said these are different."""
    groups = cg.build_groups([_cand(0, 20, text="one thing entirely"),
                              _cand(500, 520, text="another thing entirely")])
    ids = [g["moment_id"] for g in groups]
    assert len(set(ids)) == len(ids)


# ── The budget ───────────────────────────────────────────────────────────────


def _many(count, **kw):
    return [_cand(i * 100.0, i * 100.0 + 30.0, score=float(count - i),
                  text=_talk(i), **kw) for i in range(count)]


def test_a_short_source_puts_everything_in():
    """Nothing to choose between, and inventing an ordering would only add a
    place for the choice to go wrong."""
    groups = cg.build_groups(_many(20))
    out = cg.build_shortlist(groups, budget=80, duration=2000.0)
    assert len(out["selected"]) == len(groups)
    assert out["categories"] == {"all": len(groups)}


def test_a_long_source_fills_the_budget_and_no_more():
    groups = cg.build_groups(_many(300))
    out = cg.build_shortlist(groups, budget=80, duration=30000.0)
    assert len(out["selected"]) == 80


def test_every_category_is_represented():
    """The gate for this batch. A budget whose story or coverage share never
    fires is the old score slice wearing a different name."""
    groups = cg.build_groups(
        _many(200)
        + [_cand(1000.0 + i, 1030.0 + i, score=1.0, payoff=1010.0 + i * 50,
                 grounded=True, thread=f"t{i}", kinds=("FAIL",),
                 text=_talk(500 + i)) for i in range(30)])
    out = cg.build_shortlist(groups, budget=80, duration=30000.0)
    assert set(out["categories"]) >= {cg.CATEGORY_HEURISTIC, cg.CATEGORY_STORY}
    assert out["categories"][cg.CATEGORY_STORY] > 0


def test_a_grounded_story_moment_the_heuristic_hates_still_gets_in():
    """The recall the judge is being paid to reach: evidence the heuristic
    cannot see at all."""
    groups = cg.build_groups(
        _many(200) + [_cand(50.0, 80.0, score=0.1, payoff=60.0,
                            grounded=True, text=_talk(999))])
    out = cg.build_shortlist(groups, budget=80, duration=30000.0)
    picked = {g["moment_id"] for g in out["selected"]}
    lonely = next(g for g in groups if g.get("grounded"))
    assert lonely["moment_id"] in picked


def test_an_unused_share_is_redistributed_not_wasted():
    """A source with no grounded story moments must still fill its pool."""
    groups = cg.build_groups(_many(200))
    out = cg.build_shortlist(groups, budget=80, duration=30000.0)
    assert len(out["selected"]) == 80


def test_coverage_reaches_the_tail_of_a_long_source():
    """`_diversify` was handed the wrong duration once already. A pool that
    never leaves the first hour cannot be corrected by anything downstream."""
    groups = cg.build_groups(_many(300))
    out = cg.build_shortlist(groups, budget=80, duration=30000.0)
    latest = max(g["start"] for g in out["selected"])
    assert latest > 15000.0, "nothing from the second half of the source"


def test_every_selected_moment_says_why_it_is_there():
    groups = cg.build_groups(_many(200))
    out = cg.build_shortlist(groups, budget=80, duration=20000.0)
    for group in out["selected"]:
        assert out["reason"][group["moment_id"]] in cg.CATEGORIES + ("all",)


def test_a_budget_of_nothing_selects_nothing():
    assert cg.build_shortlist(cg.build_groups(_many(10)), budget=0)["selected"] == []


# ── The packet ───────────────────────────────────────────────────────────────


def test_the_packet_names_the_payoff_instead_of_hiding_it_in_prose():
    """The old packet was id, archetype, start and the first 900 characters, so
    the judge had to infer where the payoff was from the same text it was being
    asked to rule on. Truncation was never the problem: the median packet was
    423 characters and 2 of 80 hit the limit."""
    cand = _cand(100.0, 140.0, payoff=130.0, kinds=("FAIL",), grounded=True)
    cand["story"]["payoff_evidence"] = {"quote": "y'all just sat there"}
    cand["story"]["required_context"] = [
        {"t": 105.0, "fact": "chat told him to look left", "grounded": True}]
    cand["story"]["hook_latency"] = 1.2
    group = cg.build_groups([cand])[0]

    packet = cg.packet_for(cand, group)
    assert packet["payoff"]["t"] == 130.0
    assert packet["payoff"]["at"] == 0.75, "three quarters of the way in"
    assert packet["payoff"]["grounded"] is True
    assert packet["context"][0]["fact"].startswith("chat told him")
    assert packet["metrics"]["hook_latency"] == 1.2


def test_a_legacy_candidate_packet_says_there_is_no_payoff():
    """Printing an empty heading would teach the model that "no payoff" is a
    formatting artefact rather than a finding."""
    cand = _cand(0.0, 30.0)
    packet = cg.packet_for(cand, cg.build_groups([cand])[0])
    assert packet["payoff"] is None
    assert packet["context"] == []


def test_the_rendered_packet_carries_the_markers():
    from services.clipper.llm_judge import judge_prompt

    cand = _cand(100.0, 140.0, payoff=130.0, kinds=("FAIL",), grounded=True)
    cand["story"]["required_context"] = [
        {"t": 105.0, "fact": "chat trolled him", "grounded": False}]
    cand["_packet"] = cg.packet_for(cand, cg.build_groups([cand])[0])

    prompt = judge_prompt([cand], want=3)
    assert "PAYOFF at 130s" in prompt
    assert "NEEDS at 105s (unverified): chat trolled him" in prompt
    assert "TRANSCRIPT:" in prompt


def test_a_candidate_with_no_packet_renders_the_way_it_always_did():
    from services.clipper.llm_judge import judge_prompt

    prompt = judge_prompt([_cand(10.0, 40.0, text="plain words")], want=3)
    assert "0. [10s] plain words" in prompt


def test_a_moments_story_identity_survives_a_better_legacy_cut():
    """A moment can hold a legacy window and a story window — they overlap, so
    the grouping puts them together. Reading `is_story` off the highest-scoring
    cut made such a moment invisible to the budget's story share exactly when
    the heuristic preferred the loud, tidy cut, which is most of the time."""
    legacy = _cand(100.0, 130.0, score=90.0, text="shared words for one moment")
    story = _cand(102.0, 132.0, score=10.0, payoff=120.0, grounded=True,
                  kinds=("FAIL",), text="shared words for one moment")
    group = cg.build_groups([legacy, story])[0]

    assert group["is_story"] and group["grounded"]
    assert group["archetypes"] == ["FAIL"]
    assert group["best_score"] == 90.0, "the score is still the moment's best"
    assert group["representatives"] == [1], (
        "the judge is shown the cut that carries the evidence")


# ── The verdict belongs to the moment, not to one cut of it ──────────────────


def _pool_of(cands, **kw):
    return cg.build_pool(cands, duration=kw.pop("duration", 10000.0),
                         budget=kw.pop("budget", 80), **kw)


def test_every_cut_of_a_judged_moment_inherits_its_verdict():
    """Measured before this existed: 7 of the 10 winners on the four-hour
    source were `not_evaluated`, even though their moments had been judged.
    The judge scores one representative; dedupe and diversity then pick winners
    from the WHOLE field, so the pool got better and the board did not notice."""
    shared = "warden diamond furnace clutch ladder anvil beacon coral drip"
    cands = [_cand(100.0, 130.0, score=90.0, payoff=120.0, text=shared),
             _cand(105.0, 132.0, score=40.0, payoff=120.0, text=shared)]
    out = _pool_of(cands)
    rep = out["pool"][0]
    rep.update({"llm_score": 80.0, "llm_rank": 1,
                "llm_verdict": {"cold_viewer": "strong"}})

    spread = cg.propagate_verdicts(cands, out["all_groups"], weight=0.5)
    assert spread == 1
    sibling = next(c for c in cands if c is not rep)
    assert sibling["llm_rank"] == 1
    assert sibling["moment_id"] == rep["moment_id"]
    assert sibling["verdict_inherited"] is True


def test_a_sibling_is_blended_against_its_own_score_not_handed_the_leaders():
    """Copying the representative's blended `overall` would put one cut's
    number on another cut — the scale-mixing this plan exists to remove."""
    shared = "warden diamond furnace clutch ladder anvil beacon coral drip"
    cands = [_cand(100.0, 130.0, score=90.0, payoff=120.0, text=shared),
             _cand(105.0, 132.0, score=40.0, payoff=120.0, text=shared)]
    out = _pool_of(cands)
    rep = out["pool"][0]
    rep.update({"llm_score": 80.0, "llm_rank": 1})

    before = next(c for c in cands if c is not rep)["overall"]
    cg.propagate_verdicts(cands, out["all_groups"], weight=0.5)
    sibling = next(c for c in cands if c is not rep)
    # 0.5 * its own 40 + 0.5 * the moment's 80
    assert sibling["selection_score"] == 60.0
    # ...and `overall` is UNTOUCHED. It is what the legacy board reads, and
    # shadow promises that board does not move. Writing there made shadow
    # perturb the very baseline it exists to be compared against.
    assert sibling["overall"] == before


def test_an_unjudged_moment_spreads_nothing():
    cands = [_cand(100.0, 130.0, payoff=120.0), _cand(105.0, 132.0, payoff=120.0)]
    out = _pool_of(cands)
    assert cg.propagate_verdicts(cands, out["all_groups"], weight=0.5) == 0
    assert all(c.get("llm_rank") is None for c in cands)


def test_a_cut_the_judge_ranked_itself_is_not_overwritten():
    """Both cuts in the pool is possible with REPRESENTATIVES > 1. The one the
    judge actually looked at keeps its own verdict."""
    shared = "warden diamond furnace clutch ladder anvil beacon coral drip"
    cands = [_cand(100.0, 130.0, score=90.0, payoff=120.0, text=shared),
             _cand(105.0, 132.0, score=40.0, payoff=120.0, text=shared)]
    out = _pool_of(cands)
    out["pool"][0].update({"llm_score": 80.0, "llm_rank": 1})
    cands[1].update({"llm_score": 10.0, "llm_rank": 40})

    cg.propagate_verdicts(cands, out["all_groups"], weight=0.5)
    assert cands[1]["llm_rank"] == 40, "its own verdict stands"


# ── Rounds reuse the grouping ────────────────────────────────────────────────


def test_a_second_round_asks_about_moments_the_first_one_did_not():
    """The exclusion is what the round ASKED about, not the whole field.
    Excluding every group after round one would make round two ask about
    nothing — a second pool that is always empty is worse than none, because it
    looks like the cap was reached."""
    cands = _many(120)
    first = cg.build_pool(cands, duration=20000.0, budget=20)
    asked = {g["moment_id"] for g in first["selected_groups"]}
    assert len(asked) == 20

    second = cg.build_pool(cands, duration=20000.0, budget=20,
                           exclude=asked, groups=first["all_groups"])
    again = {g["moment_id"] for g in second["selected_groups"]}
    assert again and not (again & asked)


def test_reusing_the_grouping_survives_the_blend():
    """`dedupe._group` elects leaders by `overall`, and `apply_ranking` rewrites
    `overall` for everything in the first pool. Regrouping afterwards can hand a
    moment a different leader — and a legacy moment's id comes from its leader's
    own span, so the same moment can come back under a different id halfway
    through its own run."""
    cands = _many(40)
    first = cg.build_pool(cands, duration=8000.0, budget=10)
    for cand in first["pool"]:          # what the judge's blend does
        cand["overall"] = 1.0

    reused = cg.build_pool(cands, duration=8000.0, budget=10,
                           groups=first["all_groups"])
    assert ([g["moment_id"] for g in reused["all_groups"]]
            == [g["moment_id"] for g in first["all_groups"]])


# ── The census the pilot reads ───────────────────────────────────────────────


def test_the_pool_reports_how_many_story_moments_it_chose_from():
    """`story` needs a denominator or it cannot be read.

    "19 story moments went to the judge" is a success if the field held 20 and
    a failure if it held 200, and the run trace recorded only the numerator.
    That is the number the non-Minecraft pilot turns on: a source where the
    engine finds almost no story moments fails differently from one where it
    finds plenty and the shortlist spends its budget elsewhere.
    """
    cands = ([_cand(i * 100, i * 100 + 30, payoff=i * 100 + 20, grounded=i < 3)
              for i in range(8)]
             + [_cand(2000 + i * 100, 2030 + i * 100) for i in range(5)])

    out = cg.build_pool(cands, duration=3000.0, budget=80)

    assert out["story_groups"] == 8
    assert out["story_grounded"] == 3
    # The denominator is the FIELD, so it can never be smaller than the part
    # of it the judge was asked about.
    assert out["story"] <= out["story_groups"]


def test_the_census_says_where_on_the_clock_the_story_moments_are():
    """Batch 4 found the first three hours of a four-hour stream unread, and it
    was visible only because someone plotted payoff times by hand. A source
    whose story moments all sit in one quarter is the same failure wearing a
    different source, so the shape is recorded on every run now.
    """
    late = [_cand(3000 + i * 60, 3040 + i * 60, payoff=3020 + i * 60)
            for i in range(6)]

    census = cg.story_census(cg.build_groups(late), duration=4000.0)

    assert census["story_quarters"][3] == 6
    assert sum(census["story_quarters"][:3]) == 0


def test_a_moment_is_placed_by_its_payoff_not_by_where_the_cut_starts():
    """The moment IS its payoff — `moment_id` is built from it. A cut carrying
    long context can open a whole quarter before the thing it is about, and
    placing by `start` would report where the camera rolled rather than where
    the moment happened.
    """
    census = cg.story_census(
        cg.build_groups([_cand(10.0, 990.0, payoff=950.0)]), duration=1000.0)

    assert census["story_quarters"] == [0, 0, 0, 1]


def test_all_three_validity_buckets_are_recorded():
    """"4 uncertain" cannot say whether the other two are valid or invalid, and
    `grounded` is a different axis — it is about the payoff's evidence, not the
    window — so it is not the complement of anything here.
    """
    cands = [_cand(0, 30, payoff=20), _cand(200, 230, payoff=220),
             _cand(400, 430, payoff=420)]
    groups = cg.build_groups(cands)
    for group, verdict in zip(groups, ("valid", "uncertain", "invalid")):
        group["validity"] = verdict

    census = cg.story_census(groups, duration=1000.0)

    assert (census["story_valid"], census["story_uncertain"],
            census["story_invalid"]) == (1, 1, 1)


def test_a_moment_at_the_very_end_lands_inside_the_last_quarter():
    """`start / duration * 4` is exactly 4 for a moment starting at `duration`,
    which indexes off the end of a four-element list. Clamped, because a
    boundary case that raises would take down a run for a counter.
    """
    census = cg.story_census(cg.build_groups([_cand(100.0, 130.0, payoff=120.0)]),
                             duration=100.0)

    assert census["story_quarters"] == [0, 0, 0, 1]


def test_an_unknown_duration_reports_no_spread_rather_than_a_flat_one():
    """Duration comes from a probe that can fail. Counting must survive it, but
    four zeros is a SHAPE — the flattest one — and a reader comparing sources
    would take it as "evenly spread, nothing anywhere". `None` says unknown.
    """
    census = cg.story_census(cg.build_groups([_cand(0, 30, payoff=20)]),
                             duration=0.0)

    assert census["story_groups"] == 1
    assert census["story_quarters"] is None
