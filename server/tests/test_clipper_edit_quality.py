"""The R0 evaluator: what it counts, and what it refuses to count.

These tests exist because the audit they replace was done by hand. A metric
that quietly returns 0 where it means "I could not tell" would reproduce the
baseline for the wrong reason and pass the gate anyway, so most of what is
pinned here is the difference between zero and `unavailable`.

The equivalence rule is deliberately narrow — contiguous `fit` next to `fit`,
nothing else — and `_crop_fit_fit_crop` below is the case it was written for.
Do not widen it here; R1 owns the perceptual half and needs a measurement
before it gets one.
"""

from __future__ import annotations

from pathlib import Path

from services.clipper import edit_quality as eq
from services.clipper import edit_quality_totals as totals


def _shot(t0: float, t1: float, composition: str | None = "crop", **extra) -> dict:
    shot = {"t0": t0, "t1": t1, "camera": "face", "rect": {"x": 0, "y": 0, "w": 1, "h": 1}}
    if composition is not None:
        shot["composition"] = composition
    shot.update(extra)
    return shot


def _sidecar(shots: list[dict] | None, *, duration: float | None = 10.0,
             words: list[dict] | None = None, **extra) -> dict:
    out: dict = {"clip_id": "c1", "project_id": "p1", "duration": duration}
    if shots is not None:
        out["dynamic_plan"] = {"duration": duration, "shots": shots}
    if words is not None:
        out["caption_plan"] = {"chunks": [{"text": "x", "words": words}]}
    out.update(extra)
    return out


def _crop_fit_fit_crop() -> dict:
    """The shape the whole rule is about: four shots, one invisible cut.

    The two `fit` shots deliver one uninterrupted full-frame image, so the cut
    between them exists only in the plan. The crop→fit and fit→crop boundaries
    are real cuts and must survive.
    """
    return _sidecar([
        _shot(0.0, 2.0, "crop"),
        _shot(2.0, 4.0, "fit"),
        _shot(4.0, 6.0, "fit"),
        _shot(6.0, 10.0, "crop"),
    ])


def test_only_the_fit_to_fit_boundary_is_equivalent():
    report = eq.clip_report(_crop_fit_fit_crop())
    assert report["equivalent_cuts"] == 1
    assert report["non_contiguous_boundaries"] == 0
    assert report["composition"] == {"crop": 2, "fit": 2}
    assert report["shots"] == 4


def test_crop_between_two_fits_is_not_equivalent():
    """Adjacency is required. `fit, crop, fit` shows a different image in the
    middle, so neither boundary is invisible."""
    report = eq.clip_report(_sidecar([
        _shot(0.0, 2.0, "fit"),
        _shot(2.0, 4.0, "crop"),
        _shot(4.0, 6.0, "fit"),
    ]))
    assert report["equivalent_cuts"] == 0


def test_a_gap_between_two_fits_is_a_gap_not_an_equivalence():
    """Contiguity first. Two `fit` shots with a hole between them are not one
    image — something else was on screen — and the hole is the finding."""
    report = eq.clip_report(_sidecar([
        _shot(0.0, 2.0, "fit"),
        _shot(3.5, 6.0, "fit"),
    ]))
    assert report["equivalent_cuts"] == 0
    assert report["non_contiguous_boundaries"] == 1


def test_shot_order_is_read_as_persisted_not_sorted():
    """A plan written out of order is corrupt, and sorting would hide it."""
    report = eq.clip_report(_sidecar([
        _shot(4.0, 6.0, "fit"),
        _shot(0.0, 4.0, "fit"),
    ]))
    assert report["non_contiguous_boundaries"] == 1
    assert report["equivalent_cuts"] == 0


def test_an_old_plan_without_composition_cannot_be_judged_at_all():
    """Plans predating `composition` are UNDECIDABLE, not clean.

    Reporting 0 equivalences here was the first draft's behaviour and it is the
    worst possible answer: it says the audit checked and found nothing, on a
    corpus the rule cannot be applied to. One undecidable boundary makes the
    count a lower bound, and a lower bound reported as a total is the failure
    this module is a reaction to."""
    report = eq.clip_report(_sidecar([
        _shot(0.0, 2.0, None),
        _shot(2.0, 4.0, None),
    ]))
    assert report["equivalent_cuts"] == eq.UNAVAILABLE
    assert report["undecidable_boundaries"] == 1
    assert report["composition"] == {eq.UNAVAILABLE: 2}


def test_one_undecidable_boundary_poisons_the_count_not_just_itself():
    """Three real cuts and one that cannot be judged: the total is unavailable,
    and the demonstrable equivalence is NOT quietly reported as the answer."""
    report = eq.clip_report(_sidecar([
        _shot(0.0, 2.0, "fit"),
        _shot(2.0, 4.0, "fit"),
        _shot(4.0, 6.0, None),
        _shot(6.0, 10.0, "crop"),
    ]))
    assert report["equivalent_cuts"] == eq.UNAVAILABLE
    assert report["undecidable_boundaries"] == 2


def test_a_static_export_has_no_shot_metrics_and_says_so():
    report = eq.clip_report(_sidecar(None))
    assert report["static_export"] is True
    for key in ("shots", "shots_per_minute", "min_shot_s", "equivalent_cuts"):
        assert report[key] == eq.UNAVAILABLE, key


def test_an_empty_dynamic_plan_is_a_finding_not_a_static_export():
    report = eq.clip_report(_sidecar([]))
    assert report["static_export"] is False
    assert "empty_dynamic_plan" in report["defects"]
    assert report["shots"] == 0
    assert report["equivalent_cuts"] == eq.UNAVAILABLE


def test_a_malformed_shot_list_is_not_repaired_into_a_static_export():
    """`shots` that is not a list at all. Reading it as "no dynamic plan" filed
    a broken artifact under a legitimate state and lost it."""
    report = eq.clip_report(_sidecar(None, dynamic_plan={"shots": "nope"}))
    assert "malformed_shots" in report["defects"]
    assert report["static_export"] is False
    assert report["shots"] == eq.UNAVAILABLE


def test_a_shot_list_with_junk_in_it_is_refused_whole():
    """Dropping the bad entries would renumber every boundary after them and
    report a shot count for a plan that does not exist."""
    report = eq.clip_report(_sidecar(None, dynamic_plan={
        "shots": [_shot(0.0, 2.0, "fit"), "junk", _shot(2.0, 4.0, "fit")]}))
    assert "malformed_shots" in report["defects"]
    assert report["shots"] == eq.UNAVAILABLE
    assert report["equivalent_cuts"] == eq.UNAVAILABLE


def test_a_dynamic_plan_that_is_not_a_dict_is_a_defect():
    report = eq.clip_report(_sidecar(None, dynamic_plan=["shots"]))
    assert "malformed_dynamic_plan" in report["defects"]
    assert report["static_export"] is False


def test_zero_duration_refuses_to_divide_and_is_a_defect():
    """Absent is unknown; present and unusable is corruption. A zero-length
    export used to become "no duration" and pass the gate beside the exports
    that simply never recorded one."""
    report = eq.clip_report(_sidecar([_shot(0.0, 1.0)], duration=0))
    assert report["duration_s"] == eq.UNAVAILABLE
    assert report["shots_per_minute"] == eq.UNAVAILABLE
    assert "invalid_duration" in report["defects"]


def test_an_infinite_duration_is_not_a_duration():
    report = eq.clip_report(_sidecar([_shot(0.0, 1.0)], duration=float("inf")))
    assert "invalid_duration" in report["defects"]


def test_a_missing_duration_is_unknown_not_corrupt():
    report = eq.clip_report(_sidecar([_shot(0.0, 1.0)], duration=None))
    assert report["defects"] == []
    assert report["duration_s"] == eq.UNAVAILABLE


def test_a_composition_that_is_present_but_not_a_string_is_corruption():
    """`null` and `7` are claims that cannot be true. Folding them into "no
    composition" filed them under age instead — the same confusion one layer
    down from the one the gate already catches."""
    for value in (None, 7, ""):
        # Built by hand: `_shot(..., None)` OMITS the key, which is the legacy
        # case. The point here is a key that is present and holds nonsense.
        bad = {"t0": 3.0, "t1": 6.0, "composition": value}
        report = eq.clip_report(_sidecar([_shot(0.0, 3.0, "fit"), bad], duration=6.0))
        assert "invalid_composition" in report["defects"], value


def test_lead_in_and_tail_come_from_the_word_times():
    report = eq.clip_report(_sidecar(
        [_shot(0.0, 10.0)], duration=10.0,
        words=[{"word": "a", "start": 0.0, "end": 0.4},
               {"word": "b", "start": 6.0, "end": 9.98}]))
    assert report["lead_in_s"] == 0.0
    assert abs(report["tail_s"] - 0.02) < 1e-9


def test_no_captions_means_no_lead_in_measurement():
    report = eq.clip_report(_sidecar([_shot(0.0, 10.0)]))
    assert report["lead_in_s"] == eq.UNAVAILABLE
    assert report["tail_s"] == eq.UNAVAILABLE


def test_duplicate_captions_are_unavailable_because_nothing_declares_them():
    """The audit's 15/15 was a human watching the files. Nothing in the sidecar
    says the SOURCE already carried burned subtitles, and reporting 0 would turn
    "never asked" into "checked and clean". R6 owns the signal."""
    assert eq.clip_report(_crop_fit_fit_crop())["captions_duplicate_declared"] == (
        eq.UNAVAILABLE)


def test_unstamped_sidecars_report_unavailable_not_the_current_version():
    report = eq.clip_report(_crop_fit_fit_crop())
    assert report["render_version"] == eq.UNAVAILABLE
    assert report["input_fingerprint"] == eq.UNAVAILABLE


def test_a_stamped_sidecar_carries_its_own_version_through():
    report = eq.clip_report(_sidecar(
        [_shot(0.0, 10.0)], render_version="render_v3_letterbox",
        input_fingerprint="0123456789abcdef"))
    assert report["render_version"] == "render_v3_letterbox"
    assert report["input_fingerprint"] == "0123456789abcdef"


def test_two_runs_of_the_same_project_stay_distinguishable():
    """Artifacts from two different renders must not be silently merged into one
    baseline — the tally is what makes a mixed corpus visible."""
    rows = [
        eq.clip_report(_sidecar([_shot(0.0, 10.0)], render_version="render_v2_subject_aware")),
        eq.clip_report(_sidecar([_shot(0.0, 10.0)], render_version="render_v3_letterbox")),
    ]
    report = totals.project_report("p1", rows)
    assert report["render_versions"] == {
        "render_v2_subject_aware": 1, "render_v3_letterbox": 1}


def test_aggregates_never_turn_unavailable_into_zero():
    """One clip measurable, one not. The sum is over what exists and `coverage`
    says how many clips it is made of — 2 shots over 1 clip, not over 2."""
    rows = [eq.clip_report(_sidecar([_shot(0.0, 2.0), _shot(2.0, 4.0)], duration=4.0)),
            eq.clip_report(_sidecar(None))]
    report = totals.project_report("p1", rows)
    assert report["clips"] == 2
    assert report["shots"] == 2
    assert report["coverage"]["shots"] == 1
    assert report["static_exports"] == 1


def test_a_project_with_nothing_measurable_reports_unavailable():
    report = totals.project_report("p1", [eq.clip_report(_sidecar(None))])
    assert report["shots"] == eq.UNAVAILABLE
    assert report["shots_per_minute_pooled"] == eq.UNAVAILABLE
    assert report["min_shot_s"] == eq.UNAVAILABLE
    assert report["clips"] == 1


def test_the_macro_is_built_from_clips_not_from_project_aggregates():
    """A project whose duration is unknown would otherwise drop its shots out of
    the corpus total as well; each metric keeps its own coverage."""
    measurable = eq.clip_report(_sidecar([_shot(0.0, 3.0)], duration=3.0))
    no_duration = eq.clip_report(_sidecar([_shot(0.0, 3.0)], duration=None))
    projects = [totals.project_report("a", [measurable]),
                totals.project_report("b", [no_duration])]
    macro = totals.macro_report(projects, [measurable, no_duration])
    assert macro["projects"] == 2
    assert macro["shots"] == 2
    assert macro["coverage"]["duration_s"] == 1


def test_the_shortest_shot_is_the_shortest_anywhere_in_the_corpus():
    rows = [eq.clip_report(_sidecar([_shot(0.0, 2.0)], duration=2.0)),
            eq.clip_report(_sidecar([_shot(0.0, 0.605)], duration=0.605))]
    assert abs(totals.project_report("p1", rows)["min_shot_s"] - 0.605) < 1e-9


def test_a_composition_nobody_knows_is_undecidable_not_a_real_cut():
    """The list is closed. `composition="banana"` used to produce a `real`
    boundary and sit in the distribution looking like a measurement."""
    report = eq.clip_report(_sidecar([
        _shot(0.0, 2.0, "fit"),
        _shot(2.0, 4.0, "banana"),
        _shot(4.0, 6.0, "fit"),
    ]))
    assert report["equivalent_cuts"] == eq.UNAVAILABLE
    assert report["undecidable_boundaries"] == 2
    # Still visible under its own name: folding it into `unavailable` would lose
    # the one thing worth seeing about a plan nobody can read.
    assert report["composition"] == {"fit": 2, "unknown:banana": 1}


def test_a_malformed_caption_plan_yields_no_timing_and_a_finding():
    """Skipping the bad chunk computed a lead-in and a tail from a SUBSET of the
    captions and reported them as the clip's."""
    report = eq.clip_report(_sidecar(
        [_shot(0.0, 10.0)],
        caption_plan={"chunks": [{"words": [{"start": 0.0, "end": 0.4}]}, "junk"]}))
    assert "malformed_caption_plan" in report["defects"]
    assert report["lead_in_s"] == eq.UNAVAILABLE
    assert report["tail_s"] == eq.UNAVAILABLE


def test_the_rate_is_never_built_from_two_different_clips():
    """`sum(shots) / sum(durations)` read two independent lists: a clip with
    shots but no duration and a clip with duration but no shots produced a
    confident rate belonging to neither of them."""
    shots_only = eq.clip_report(_sidecar([_shot(0.0, 2.0), _shot(2.0, 4.0)],
                                         duration=None))
    duration_only = eq.clip_report(_sidecar(None, duration=10.0))
    report = totals.project_report("p1", [shots_only, duration_only])
    assert report["shots"] == 2
    assert report["duration_s"] == 10.0
    assert report["shots_per_minute_pooled"] == eq.UNAVAILABLE
    assert report["coverage"]["shots_per_minute"] == 0


def test_an_aggregate_equivalence_is_a_total_or_it_is_nothing():
    """One clip nobody can judge makes the sum a lower bound. Reporting it as
    the total reintroduced, in the aggregate, exactly the failure the per-clip
    rule was written to prevent."""
    known = eq.clip_report(_crop_fit_fit_crop())
    unknowable = eq.clip_report(_sidecar([_shot(0.0, 2.0, None), _shot(2.0, 4.0, None)]))
    report = totals.project_report("p1", [known, unknowable])
    assert report["equivalent_cuts"] == eq.UNAVAILABLE
    assert report["equivalent_cuts_known_lower_bound"] == 1
    assert report["coverage"]["equivalent_cuts"] == 1

    whole = totals.project_report("p1", [known, eq.clip_report(_crop_fit_fit_crop())])
    assert whole["equivalent_cuts"] == 2


def test_a_shot_whose_times_cannot_be_read_refuses_the_whole_shot_half():
    """Skipping it left the plan looking like a clean edit: the count still
    included it, its length vanished from the minimum, and its two boundaries
    became `gap`s — jump cuts nothing in the plan asked for."""
    report = eq.clip_report(_sidecar([
        _shot(0.0, 2.0, "fit"),
        {"t0": "oops", "t1": None, "composition": "fit"},
        _shot(4.0, 6.0, "fit"),
    ]))
    assert "malformed_shot_times" in report["defects"]
    for key in ("shots", "min_shot_s", "equivalent_cuts",
                "non_contiguous_boundaries", "composition"):
        assert report[key] == eq.UNAVAILABLE, key


def test_a_zero_length_shot_is_unreadable_too():
    """`t1 == t0` is not a shot. It used to be dropped from the lengths and
    counted everywhere else."""
    report = eq.clip_report(_sidecar([_shot(0.0, 2.0, "fit"), _shot(2.0, 2.0, "fit")]))
    assert "malformed_shot_times" in report["defects"]


def test_a_word_whose_times_cannot_be_read_refuses_the_timing():
    """The surviving words still produced a lead-in and a tail, and the report
    presented them as the clip's."""
    report = eq.clip_report(_sidecar(
        [_shot(0.0, 10.0)],
        words=[{"word": "a", "start": 0.0, "end": 0.5},
               {"word": "b", "start": "x", "end": "y"}]))
    assert "malformed_word_times" in report["defects"]
    assert report["lead_in_s"] == eq.UNAVAILABLE
    assert report["tail_s"] == eq.UNAVAILABLE


def test_a_clip_can_carry_more_than_one_defect():
    """The singular field reported whichever was found first, and the second
    left no trace at all."""
    report = eq.clip_report(_sidecar(
        None, dynamic_plan={"shots": "nope"},
        caption_plan={"chunks": "nope"}))
    assert sorted(report["defects"]) == ["malformed_caption_plan", "malformed_shots"]


def test_an_unknown_composition_is_corruption_a_missing_one_is_age():
    """The distinction the gate turns on. Both are undecidable for the
    equivalence rule; only one of them is a defect."""
    invalid = eq.clip_report(_sidecar([
        _shot(0.0, 3.0, "fit"), _shot(3.0, 6.0, "banana")], duration=6.0))
    assert "invalid_composition" in invalid["defects"]
    # And the value stays visible: it is the evidence for the finding.
    assert invalid["composition"] == {"fit": 1, "unknown:banana": 1}

    legacy = eq.clip_report(_sidecar([
        _shot(0.0, 3.0, None), _shot(3.0, 6.0, None)], duration=6.0))
    assert legacy["defects"] == []
    assert legacy["undecidable_boundaries"] == 1


def test_a_time_that_parses_is_not_a_time_that_happened():
    """Shots starting before the clip did, and a shot longer than the whole
    export, both read as clean edits because the numbers were floats."""
    before = eq.clip_report(_sidecar(
        [_shot(-5.0, -1.0, "fit"), _shot(-1.0, 3.0, "fit")], duration=10.0))
    assert "malformed_shot_times" in before["defects"]

    past_end = eq.clip_report(_sidecar([_shot(0.0, 900.0, "fit")], duration=10.0))
    assert "malformed_shot_times" in past_end["defects"]
    assert past_end["min_shot_s"] == eq.UNAVAILABLE


def test_infinity_is_not_a_measurement():
    """NaN was rejected from the start; the infinities were not, and `inf` read
    as a perfectly good number all the way into `min_shot_s`."""
    report = eq.clip_report(_sidecar(
        [_shot(0.0, float("inf"), "fit")], duration=10.0))
    assert "malformed_shot_times" in report["defects"]


def test_a_zero_length_word_is_normal_and_a_backwards_one_is_not():
    """156 of the 8.249 words in the four pilot exports have `end <= start` —
    the transcriber emits them for very short tokens. Requiring `end > start`
    would condemn 58 of 58 clips and take the baseline with them."""
    ok = eq.clip_report(_sidecar(
        [_shot(0.0, 10.0)], duration=10.0,
        words=[{"start": 0.0, "end": 0.0}, {"start": 1.0, "end": 9.9}]))
    assert ok["defects"] == []
    assert ok["lead_in_s"] == 0.0

    backwards = eq.clip_report(_sidecar(
        [_shot(0.0, 10.0)], duration=10.0,
        words=[{"start": 5.0, "end": 1.0}]))
    assert "malformed_word_times" in backwards["defects"]


def test_words_past_the_end_of_the_clip_are_refused():
    report = eq.clip_report(_sidecar(
        [_shot(0.0, 10.0)], duration=10.0,
        words=[{"start": 0.0, "end": 0.5}, {"start": 40.0, "end": 41.0}]))
    assert "malformed_word_times" in report["defects"]
    assert report["tail_s"] == eq.UNAVAILABLE
