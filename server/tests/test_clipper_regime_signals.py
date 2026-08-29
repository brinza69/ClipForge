"""Batch R3b: the signals a regime is decided from, and what they cannot say.

Split from `test_clipper_regimes.py` at 500 lines. The other file is about the
rules — which regime a moment is, and which boundaries earn a cut. This one is
about the measurements underneath: the clock they are on, the three kinds of
silence, and the difference between a number and the absence of one.

Almost every test here exists because a first version turned "nobody measured
this" into "measured, and zero" somewhere new.
"""

from __future__ import annotations

import pytest

from services.clipper import dynamic_regimes as regimes

HOP = 0.25
STYLE = {"action_pct": 0.60, "speech_ratio_on": 0.30}


def _flags(pattern: str) -> list[bool]:
    return [c == "1" for c in pattern]


def _levels(pattern: str) -> list[float]:
    return [1.0 if c == "1" else 0.0 for c in pattern]


def _view(creator: str, others: str | None, action: str, speech: str, **kw) -> dict:
    kw.setdefault("duration", len(creator) * HOP)
    return regimes.regime_view(
        creator=_flags(creator),
        others=None if others is None else _flags(others),
        action=_levels(action), speech=[1.0 if c == "1" else 0.0 for c in speech],
        hop=HOP, style=STYLE, **kw)


# --- the clock ---------------------------------------------------------------


def test_a_sample_at_the_end_of_the_clip_does_not_invent_a_quarter_second():
    """The dense track carries a sample at EOF. Treating it as a full interval
    made a 4.0s clip come back 4.25s long — a report describing video that does
    not exist."""
    assert regimes.interval_count(4.0, HOP) == 16
    assert regimes.interval_count(4.1, HOP) == 17

    view = regimes.regime_view(
        creator=[True] * 17, others=[False] * 17, action=[0.1] * 17,
        speech=[1.0] * 17, hop=HOP, duration=4.0, style=STYLE)
    assert view["intervals"] == 16
    assert view["samples"] == 16
    assert view["segments"][-1]["t1"] == 4.0


def test_a_clip_that_does_not_divide_evenly_keeps_its_last_fragment():
    view = regimes.regime_view(
        creator=[True] * 17, others=[False] * 17, action=[0.1] * 17,
        speech=[1.0] * 17, hop=HOP, duration=4.1, style=STYLE)
    assert view["intervals"] == 17
    assert view["segments"][-1]["t1"] == 4.1, "the tail is clamped, not dropped"


# --- three kinds of silence --------------------------------------------------


def test_the_speech_threshold_actually_decides_something():
    """It was read, persisted, passed — and never used, because a boolean per
    sample cleared any threshold at all."""
    quiet = regimes.regime_view(
        creator=[True] * 4, others=[False] * 4, action=[0.0] * 4,
        speech=[0.1] * 4, hop=HOP, duration=1.0,
        style={"action_pct": 0.6, "speech_ratio_on": 0.30})
    loud = regimes.regime_view(
        creator=[True] * 4, others=[False] * 4, action=[0.0] * 4,
        speech=[0.9] * 4, hop=HOP, duration=1.0,
        style={"action_pct": 0.6, "speech_ratio_on": 0.30})
    assert [s["regime"] for s in quiet["segments"]] == ["safe"]
    assert [s["regime"] for s in loud["segments"]] == ["speaker"]


def test_no_word_times_is_not_proof_of_silence():
    """`_clip_words` returns [] when the clip has no caption plan. Reading that
    as "nobody spoke" turned real speech into `visual_evidence`."""
    view = regimes.regime_view(
        creator=[True] * 4, others=[False] * 4, action=[0.0] * 4,
        speech=None, hop=HOP, duration=1.0, style=STYLE, speech_known=False)
    assert view["speech_known"] is False
    assert view["speech_source"] is None
    assert [s["regime"] for s in view["segments"]] == ["safe"]
    assert view["segments"][0]["reason"] == "speech_unknown"


def test_a_missing_motion_tail_is_not_a_quiet_tail():
    """Eight samples of timeline against four of motion: the second half had no
    data, and it did not demonstrate the absence of action."""
    view = regimes.regime_view(
        creator=[False] * 8, others=[False] * 8, action=[0.9, 0.9, 0.9, 0.9],
        speech=[0.0] * 8, hop=HOP, duration=2.0, style=STYLE)
    assert view["action_coverage"] == regimes.COVERAGE_PARTIAL
    regimes_seen = [s["regime"] for s in view["segments"]]
    assert regimes_seen[0] == "action"
    assert regimes_seen[-1] == "safe", "an uncovered tail is not evidence of quiet"
    assert view["segments"][-1]["reason"] == "action_partial"


def test_an_absent_motion_series_never_reads_as_calm():
    view = regimes.regime_view(
        creator=[False] * 4, others=[False] * 4, action=None, speech=[0.0] * 4,
        hop=HOP, duration=1.0, style=STYLE,
        coverage=regimes.COVERAGE_UNAVAILABLE,
        variability=regimes.VARIABILITY_UNAVAILABLE)
    assert [s["regime"] for s in view["segments"]] == ["safe"]
    assert view["coverage"]["action"] == 0


def test_the_report_carries_enough_to_audit_the_answer():
    view = _view(creator="1111", others="0000", action="0000", speech="1111")
    assert view["duration_s"] == 1.0
    assert view["intervals"] == 4
    assert view["coverage"] == {"target": 4, "others": 4, "action": 4, "speech": 4}
    assert view["action_coverage"] in regimes.COVERAGES
    assert view["action_variability"] in regimes.VARIABILITIES
    assert view["speech_known"] is True
    assert view["speech_source"] == "caption_plan"


# --- the clock the motion is actually on -------------------------------------


def test_a_ten_fps_proxy_is_resampled_onto_the_canonical_bins():
    """Frames are whole, so a 0.25s request on a 10 FPS proxy lands on a 0.2s
    grid. Indexing that as if it were 0.25s reads every value from a moment it
    does not describe, and the drift grows across the clip."""
    out = regimes.resample([0.0, 1.0, 2.0, 3.0, 4.0],
                           from_hop=0.2, to_hop=0.25, intervals=4)
    assert out[0] == pytest.approx(1.2)


def test_a_bin_with_no_measurement_is_none_not_calm():
    out = regimes.resample([0.0, 1.0], from_hop=0.2, to_hop=0.25, intervals=4)
    assert out[1:] == [None, None, None]
    view = regimes.regime_view(
        creator=[False] * 4, others=[False] * 4, action=out, speech=[0.0] * 4,
        hop=HOP, duration=1.0, style=STYLE)
    assert [s["regime"] for s in view["segments"]][-1] == "safe"


def test_resampling_a_missing_series_measures_nothing_anywhere():
    assert regimes.resample([], from_hop=0.2, to_hop=0.25, intervals=4) == [None] * 4


# --- a track shorter than its clip -------------------------------------------


def test_a_face_track_shorter_than_the_clip_leaves_the_rest_unknown():
    """The report names its scope as the timeline. With one sample against a
    30-second clip it used to cover 0.25s and still make that claim, and the
    missing 29.75s were padded with "nobody was there"."""
    view = regimes.regime_view(
        creator=[True], others=[False], action=[0.1], speech=[1.0],
        hop=HOP, duration=30.0, style=STYLE)
    assert view["intervals"] == 120 and view["samples"] == 120
    assert view["target_covered"] is False
    assert view["segments"][-1]["t1"] == 30.0
    assert view["segments"][-1]["reason"] == "target_unknown"
    assert view["segments"][-1]["regime"] == "safe"


def test_a_complete_track_says_so():
    view = _view(creator="1111", others="0000", action="0000", speech="1111")
    assert view["target_covered"] is True


# --- what the key is allowed to claim ----------------------------------------


def test_no_key_ever_claims_an_identity():
    """`stable_track` finds a geometrically stable cluster. R3a's own
    documentation says nobody labelled those boxes, so `crop_creator` would
    assert the very thing this work exists to stop asserting — and without an
    anchor at all it is merely whichever face was found."""
    assert regimes.visual_key_for("speaker", anchor_known=True) == "crop_anchor"
    assert regimes.visual_key_for("speaker", anchor_known=False) == "crop_subject"

    view = regimes.regime_view(
        creator=[True] * 4, others=None, action=[0.1] * 4, speech=[1.0] * 4,
        hop=HOP, duration=1.0, style=STYLE)
    assert view["target_anchor_known"] is False
    assert view["target_basis"] == "unanchored_face"
    assert view["treatment_segments"][0]["visual_key"] == "crop_subject"


def test_an_anchored_subject_keeps_the_stronger_key():
    view = _view(creator="1111", others="0000", action="0000", speech="1111")
    assert view["target_anchor_known"] is True
    assert view["target_basis"] == "stable_anchor"
    assert view["treatment_segments"][0]["visual_key"] == "crop_anchor"


def test_the_first_motion_sample_is_a_sentinel_not_a_measurement():
    """`region_motion` has no previous frame to difference against, so it emits
    0.0. Using it as data let a perfectly constant series look like it had
    dispersion — and dispersion is what the normaliser scales against."""
    assert regimes.resample([0, 3, 3, 3, 3], from_hop=0.2, to_hop=0.25,
                            intervals=4) == [3.0, 3.0, 3.0, None]


def test_a_motion_value_describes_the_interval_before_it():
    """The sample at `t` is the difference between the frames at `t - step` and
    `t`. Treating it as the interval that FOLLOWS shifted the whole series
    forward by one step."""
    out = regimes.resample([0, 1, 2, 3, 4], from_hop=0.2, to_hop=0.25, intervals=4)
    assert out[:3] == [pytest.approx(1.2), pytest.approx(2.4), pytest.approx(3.6)]
    assert out[3] is None, "the last bin is not fully covered by measurements"


def test_a_series_of_only_the_sentinel_measures_nothing():
    assert regimes.resample([0.0], from_hop=0.2, to_hop=0.25, intervals=2) == [None, None]


def test_coverage_and_variability_are_separate_questions():
    """A series can be complete and flat, or varied and full of holes. One word
    for both hid whichever was worse — and a complete flat series is real: a
    static screen measured end to end."""
    flat = regimes.regime_view(
        creator=[False] * 4, others=[False] * 4, action=[0.5] * 4,
        speech=[0.0] * 4, hop=HOP, duration=1.0, style=STYLE,
        coverage=regimes.COVERAGE_COMPLETE,
        variability=regimes.VARIABILITY_FLAT)
    assert flat["action_coverage"] == regimes.COVERAGE_COMPLETE
    assert flat["action_variability"] == regimes.VARIABILITY_FLAT
    # And a flat series is not evidence of calm, so the stretch is `safe`.
    assert [s["regime"] for s in flat["segments"]] == ["safe"]
    assert flat["segments"][0]["reason"] == "action_flat"


def test_a_hole_inside_a_measured_series_says_partial_not_measured():
    """The reason names what was wrong with THIS bin, not the series average."""
    view = regimes.regime_view(
        creator=[False] * 4, others=[False] * 4, action=[0.9, None, 0.9, 0.9],
        speech=[0.0] * 4, hop=HOP, duration=1.0, style=STYLE,
        coverage=regimes.COVERAGE_PARTIAL)
    reasons = [s["reason"] for s in view["segments"]]
    assert "action_partial" in reasons
    assert "action_measured" not in reasons


def test_the_evidence_never_reports_a_mean_of_nothing():
    """`reason = creator_unknown` and `evidence.creator = 0.0` in the same
    breath: the second reads as a measurement of absence."""
    view = regimes.regime_view(
        creator=[True], others=[False], action=[0.1], speech=[1.0],
        hop=HOP, duration=1.0, style=STYLE)
    tail = view["segments"][-1]
    assert tail["reason"] == "target_unknown"
    assert tail["evidence"]["target"] is None
    assert tail["evidence_coverage"]["target"] == 0

    head = view["segments"][0]
    assert head["evidence"]["target"] == 1.0
    assert head["evidence_coverage"]["target"] == 1


def test_a_constant_real_motion_is_safe_in_both_halves():
    """End to end on the exact series that exposed the state model: `[0,3,3,3,3]`
    is a sentinel plus four identical measurements. The measured part is FLAT —
    no spread to scale against, so no evidence of action — and the tail is not
    measured at all. Both must be `safe`; neither is `visual_evidence`, which
    would claim there was nothing happening."""
    binned = regimes.resample([0, 3, 3, 3, 3], from_hop=0.2, to_hop=HOP, intervals=4)
    assert binned == [3.0, 3.0, 3.0, None]

    measured = [v for v in binned if v is not None]
    scaled, spread = regimes.normalise_series(measured)
    assert spread is False, "four identical values have no dispersion"
    values = iter(scaled)
    action = [next(values) if v is not None else None for v in binned]

    view = regimes.regime_view(
        creator=[False] * 4, others=[False] * 4, action=action,
        speech=[0.0] * 4, hop=HOP, duration=1.0, style=STYLE,
        coverage=regimes.COVERAGE_PARTIAL,
        variability=regimes.VARIABILITY_FLAT)
    # Both halves are `safe`, and they are two segments because the REASON
    # differs: flat is not the same fact as unmeasured.
    assert {s["regime"] for s in view["segments"]} == {"safe"}
    assert [s["reason"] for s in view["segments"]] == ["action_flat", "action_partial"]
    assert view["regime_boundaries"] == 0, "one regime throughout"
    assert view["action_coverage"] == regimes.COVERAGE_PARTIAL
    assert view["action_variability"] == regimes.VARIABILITY_FLAT
    assert view["coverage"]["action"] == 3, "three measured bins, not four"


def test_a_changed_reason_is_a_changed_segment():
    """One regime, two different facts. Segmenting on the regime alone threw the
    second away before it reached the sidecar: a stretch that is `safe` because
    the series is flat and then `safe` because it stopped being measured is not
    one uniform stretch."""
    view = regimes.regime_view(
        creator=[False] * 4, others=[False] * 4, action=[0.5, 0.5, 0.5, None],
        speech=[0.0] * 4, hop=HOP, duration=1.0, style=STYLE,
        coverage=regimes.COVERAGE_PARTIAL, variability=regimes.VARIABILITY_FLAT)
    assert [s["reason"] for s in view["segments"]] == ["action_flat", "action_partial"]
    # But it is still ONE regime, so it earns no regime boundary — and one
    # treatment, so it earns no cut either.
    assert view["regime_boundaries"] == 0
    assert view["treatment_boundaries"] == 0


def test_the_denominator_survives_the_treatment_merge():
    """The view that will actually be materialised is the merged one. Copying
    the mean without its coverage left it unable to tell a mean over two samples
    from one over twenty."""
    view = _view(creator="1111" + "0000", others="0000" + "1111",
                 action="0000" + "0000", speech="1111" + "0000")
    for group in view["treatment_segments"]:
        for member in group["members"]:
            assert "evidence" in member and "evidence_coverage" in member
            assert set(member["evidence"]) == set(member["evidence_coverage"])


def test_the_persisted_schema_never_says_creator():
    """`stable_track` finds a geometric cluster, not a person. Every field the
    sidecar carries says `target`, and `target_basis` says which kind it was."""
    view = _view(creator="1111", others="1111", action="0000", speech="1111")
    assert "creator" not in str(view)
    assert view["segments"][0]["reason"] == "target_and_external_face_together"
    assert "target" in view["segments"][0]["evidence"]
    assert view["target_basis"] == "stable_anchor"
