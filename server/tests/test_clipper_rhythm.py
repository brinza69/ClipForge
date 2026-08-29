"""Batch R4: a cut needs a reason AND a place.

The delivered planner has only ever had the second half. `_cut_times` walks the
window taking the best-sounding boundary every `target_shot_s`, and where a
stretch offers none it cuts anyway; `_pick_camera` then guarantees the next shot
looks different. The pace is a property of the renderer, and that is how 29,3
cuts a minute reach a tutorial and a Just Chatting stream alike.

So the case this file exists for is the first one below: a quiet talking head
full of natural pauses and nothing at all changing on screen. The old rule cuts
six times. The new one cuts zero, and says which band it therefore misses.

The asymmetry between the two kinds of reason is the other thing pinned here. A
treatment change is REQUIRED — the frame has become wrong, and holding it to
protect a minimum shot length trades a defect the viewer sees for a metric
nobody does. Everything else is an opportunity, and an opportunity that cannot
be taken cleanly is not taken.

Recorded, applied to nothing. The delivered plan is what R2 froze.
"""

from __future__ import annotations

from services.clipper import dynamic_regimes, dynamic_rhythm as rhythm
from services.clipper.dynamic_cameras import DEFAULT_STYLE
# Through `dynamic_edit`, which re-exports it: the two modules import each
# other, so reaching into `dynamic_cuts` first decides whether the planner
# loads at all.
from services.clipper.dynamic_edit import _cut_times

STYLE = {"min_shot_s": 0.60}


def _segments(*runs: tuple[str, float, float]) -> list[dict]:
    """R3b's per-sample verdicts, already collapsed into runs."""
    return [{"t0": t0, "t1": t1, "regime": name, "reason": "fixture",
             "samples": int(round((t1 - t0) / 0.25))}
            for name, t0, t1 in runs]


def _view(segments: list[dict], *, anchor_known: bool = True,
          coverage: str = "complete", variability: str = "variable") -> dict:
    """The R3b report R4 reads, built through R3b's own merge."""
    return {
        "segments": segments,
        "treatment_segments": dynamic_regimes.merge_by_visual_key(
            segments, anchor_known=anchor_known),
        "target_anchor_known": anchor_known,
        "action_coverage": coverage,
        "action_variability": variability,
    }


def _pauses(*times: float) -> list[tuple[float, float]]:
    """Places a cut would not land inside a word. Nothing more than places."""
    return [(t, 2.0) for t in times]


def _propose(segments, *, profile="talking_head", duration=20.0,
             boundaries=(), scenes=(), beats=(), **kw) -> dict:
    return rhythm.rhythm_view(
        duration=duration, profile=profile, mode="content_aware_shadow",
        regime_view=_view(segments, **{k: kw.pop(k) for k in
                                       ("anchor_known", "coverage", "variability")
                                       if k in kw}),
        boundaries=list(boundaries), scenes=scenes, beats=beats,
        style=STYLE, **kw)


# --- the batch, in one case --------------------------------------------------


def test_a_pause_with_nothing_behind_it_is_not_a_cut():
    """A quiet talking head: one subject, nothing changing, natural pauses all
    the way through. There is nowhere the picture needs to change, so nothing
    does."""
    quiet = _segments(("speaker", 0.0, 20.0))
    view = _propose(quiet, boundaries=_pauses(2.5, 5.0, 7.5, 10.0, 12.5, 15.0),
                    scenes=[], beats=[])
    assert view["cut_count"] == 0
    assert view["cuts"] == []
    assert view["boundaries_available"] == 6, "the places existed; the reasons did not"


def test_the_delivered_planner_cuts_the_same_clip_six_times():
    """The contrast, run through the delivered function rather than described.
    Neither number is a bug — they answer different questions, and R4 is the
    claim that the second one is the question worth asking."""
    boundaries = _pauses(2.5, 5.0, 7.5, 10.0, 12.5, 15.0)
    legacy = _cut_times(boundaries, 20.0, DEFAULT_STYLE)
    assert len(legacy) >= 6
    assert _propose(_segments(("speaker", 0.0, 20.0)),
                    boundaries=boundaries, scenes=[], beats=[])["cut_count"] == 0


# --- a reason, and where it lands --------------------------------------------


def test_a_treatment_change_moves_to_the_nearest_pause():
    """The subject leaves at 5.0 and the nearest pause is 300ms later. Cutting
    at the pause says the same thing without landing inside a word."""
    view = _propose(_segments(("speaker", 0.0, 5.0), ("visual_evidence", 5.0, 10.0)),
                    duration=10.0, boundaries=_pauses(5.3), scenes=[], beats=[])
    assert [c["t"] for c in view["cuts"]] == [5.3]
    assert view["cuts"][0]["placement"] == "snapped"
    assert view["cuts"][0]["reasons"] == [rhythm.REASON_TREATMENT]
    # Every request the cut answers, with the moment it was asked FROM. A single
    # `asked_at` could not say that two changes had been folded into one cut,
    # which is exactly what has to stay visible.
    assert view["cuts"][0]["requests"] == [[rhythm.REASON_TREATMENT, 5.0]]


def test_a_treatment_change_with_no_pause_near_it_still_cuts_and_says_so():
    """A reason with no place is NOT dropped when the frame has become wrong.
    Waiting a second for a pause is a second of crop held on an anchor nobody is
    standing on — which is the defect R3a exists to stop."""
    view = _propose(_segments(("speaker", 0.0, 5.0), ("visual_evidence", 5.0, 10.0)),
                    duration=10.0, boundaries=_pauses(6.5), scenes=[], beats=[])
    assert [c["t"] for c in view["cuts"]] == [5.0]
    assert view["cuts"][0]["placement"] == "unsnapped"
    assert view["cuts"][0]["boundary_weight"] is None


def test_an_optional_reason_with_no_place_is_dropped():
    """The other half of the asymmetry. Nothing is wrong with the frame, so a
    cut here would be rhythm for its own sake."""
    view = _propose(_segments(("safe", 0.0, 10.0)), profile="exploration",
                    duration=10.0, boundaries=_pauses(9.0), scenes=[4.0], beats=[])
    assert view["cut_count"] == 0
    assert view["held_reasons"] == {rhythm.HOLD_NO_BOUNDARY: 1}


def test_one_moment_can_carry_two_reasons():
    """A treatment change that also falls on a source cut is better evidence
    than either, and dropping the second would make the first unfalsifiable."""
    view = _propose(_segments(("speaker", 0.0, 5.0), ("visual_evidence", 5.0, 10.0)),
                    profile="exploration", duration=10.0,
                    boundaries=_pauses(5.0), scenes=[5.0], beats=[])
    assert len(view["cuts"]) == 1
    assert sorted(view["cuts"][0]["reasons"]) == [rhythm.REASON_SCENE,
                                                  rhythm.REASON_TREATMENT]


# --- what is not a reason ----------------------------------------------------


def test_a_regime_change_that_delivers_the_same_image_is_held_not_cut():
    """R1's rule, one level up. `conversation` and `visual_evidence` are two
    different answers about what is happening and one answer about what is on
    screen; forcing a cut between them puts back the 116 invisible cuts."""
    view = _propose(_segments(("conversation", 0.0, 5.0),
                              ("visual_evidence", 5.0, 10.0)),
                    duration=10.0, boundaries=_pauses(5.0), scenes=[], beats=[])
    assert view["cut_count"] == 0
    assert view["held_reasons"] == {rhythm.HOLD_SAME_TREATMENT: 1}
    # Held, not invisible: "we saw this change and chose not to cut on it" is a
    # different statement from "we never looked".
    assert view["held"][0]["t"] == 5.0
    # And the event is named for what was OBSERVED. A regime changed; a treatment
    # change is precisely what this is not, and labelling it as one would put a
    # reason in the report that never existed.
    assert view["held"][0]["reason"] == rhythm.EVENT_REGIME_CHANGE
    assert rhythm.EVENT_REGIME_CHANGE not in rhythm.REASONS


def test_a_beat_outside_measured_action_is_not_an_event():
    """The gate is the measurement. R3b only answers `action` where the motion
    series was covered AND had spread, so a beat anywhere else cannot claim to
    be inside action."""
    view = _propose(_segments(("speaker", 0.0, 10.0)), profile="action",
                   duration=10.0, boundaries=_pauses(4.0), scenes=[], beats=[4.0])
    assert view["cut_count"] == 0
    assert view["held_reasons"] == {rhythm.HOLD_NOT_MEASURED_ACTION: 1}


def test_a_beat_inside_measured_action_earns_a_cut_for_the_action_profile():
    view = _propose(_segments(("action", 0.0, 10.0)), profile="action",
                    duration=10.0, boundaries=_pauses(4.0), scenes=[], beats=[4.0])
    assert [c["t"] for c in view["cuts"]] == [4.0]
    assert view["cut_reasons"] == {rhythm.REASON_BEAT: 1}


def test_the_same_beat_is_refused_to_a_profile_that_does_not_accelerate():
    """§4 gives the pace to events for `action` and to nothing else. A quiet
    profile does not get to borrow the busy one's grammar because the signal
    happened to be there."""
    view = _propose(_segments(("action", 0.0, 10.0)), profile="conversation",
                    duration=10.0, boundaries=_pauses(4.0), scenes=[], beats=[4.0])
    assert view["cut_count"] == 0
    assert view["held_reasons"] == {rhythm.HOLD_PROFILE: 1}
    assert rhythm.REASON_BEAT not in rhythm.ADMITS["conversation"]


def test_a_slide_that_has_to_be_read_is_held_for_its_whole_length():
    """Eight seconds of one diagram. The old grammar cuts it into three shots
    because three pauses happened; nothing about the diagram changed."""
    view = _propose(_segments(("visual_evidence", 0.0, 8.0)),
                    profile="instructional", duration=8.0,
                    boundaries=_pauses(2.0, 4.0, 6.0), scenes=[], beats=[])
    assert view["cut_count"] == 0


def test_a_source_scene_cut_is_followed():
    """Following a cut the source already made invents nothing, which is why
    every profile admits it."""
    view = _propose(_segments(("safe", 0.0, 10.0)), profile="exploration",
                    duration=10.0, boundaries=[(4.0, 1.6)], scenes=[4.0], beats=[])
    assert [c["t"] for c in view["cuts"]] == [4.0]
    assert view["cut_reasons"] == {rhythm.REASON_SCENE: 1}


# --- the minimum shot, reported rather than obeyed ---------------------------


def test_a_required_cut_too_soon_is_emitted_and_recorded():
    """Two treatment changes 200ms apart. Suppressing the second would hold a
    frame the report has already called wrong, so it cuts and the violation is
    written down — which is how a flickering presence timeline becomes something
    a human can be shown."""
    view = _propose(_segments(("speaker", 0.0, 5.0), ("visual_evidence", 5.0, 5.2),
                              ("speaker", 5.2, 10.0)),
                    duration=10.0, boundaries=[], scenes=[], beats=[])
    assert [c["t"] for c in view["cuts"]] == [5.0, 5.2]
    assert [v["shot_s"] for v in view["min_shot_violations"]] == [0.2]
    assert view["required_conflicts"] == []


def test_an_optional_cut_too_soon_is_dropped():
    view = _propose(_segments(("speaker", 0.0, 5.0), ("visual_evidence", 5.0, 10.0)),
                    profile="exploration", duration=10.0,
                    boundaries=_pauses(5.0, 5.3), scenes=[5.3], beats=[])
    assert [c["t"] for c in view["cuts"]] == [5.0]
    assert view["held_reasons"] == {rhythm.HOLD_MIN_SHOT: 1}


def test_a_runt_tail_is_refused_not_merely_reported():
    """A cut 100ms before the end is a 100ms flash. Reporting the violation and
    keeping the cut would present a requirement the timeline cannot materialise
    as a valid edit, so the cut is removed and the requirement is recorded as a
    conflict — R5 decides whether to extend the window or move the boundary."""
    view = _propose(_segments(("speaker", 0.0, 9.9), ("visual_evidence", 9.9, 10.0)),
                    duration=10.0, boundaries=[], scenes=[], beats=[])
    assert view["cuts"] == []
    assert view["min_shot_violations"] == [], "not a violation — a refusal"
    conflict = view["required_conflicts"][0]
    assert conflict["conflict"] == rhythm.CONFLICT_TAIL
    assert conflict["t"] == 9.9 and conflict["shot_s"] == 0.1
    assert view["required_conflict_kinds"] == {rhythm.CONFLICT_TAIL: 1}


def test_an_optional_cut_in_the_tail_is_only_held():
    """Same flash, no requirement behind it. It never reaches the conflict list,
    because nothing about the frame was wrong."""
    view = _propose(_segments(("safe", 0.0, 10.0)), profile="exploration",
                    duration=10.0, boundaries=[(9.9, 1.6)], scenes=[9.9], beats=[])
    assert view["cuts"] == [] and view["required_conflicts"] == []
    assert view["held_reasons"] == {rhythm.HOLD_TAIL_MIN_SHOT: 1}


def test_two_required_changes_never_collapse_into_one_clean_cut():
    """The failure this rule exists for. A change at 5.0 and another at 5.2 both
    snap to the pause at 5.1; absorbed as a duplicate, the treatment that exists
    only between them is never shown, and there is no cut, no hold and no
    violation to say so. The flicker the report exists to expose becomes
    invisible."""
    view = _propose(_segments(("speaker", 0.0, 5.0), ("visual_evidence", 5.0, 5.2),
                              ("speaker", 5.2, 10.0)),
                    duration=10.0, boundaries=_pauses(5.1), scenes=[], beats=[])
    # The first snaps to the pause; the second cannot be satisfied by it, so it
    # stays at its own moment and the 100ms shot is recorded.
    assert [c["t"] for c in view["cuts"]] == [5.1, 5.2]
    assert [c["placement"] for c in view["cuts"]] == ["snapped", "unsnapped"]
    assert [v["shot_s"] for v in view["min_shot_violations"]] == [0.1]


def test_a_required_change_that_cannot_be_separated_is_a_conflict():
    """When the second change's own moment is BEHIND the cut that absorbed it,
    there is nowhere left to put it. Recorded rather than dropped: a requirement
    nobody can materialise is not the same as one that was met."""
    view = _propose(_segments(("speaker", 0.0, 5.0), ("visual_evidence", 5.0, 5.05),
                              ("speaker", 5.05, 10.0)),
                    duration=10.0, boundaries=_pauses(5.3), scenes=[], beats=[])
    assert [c["t"] for c in view["cuts"]] == [5.3]
    conflict = view["required_conflicts"][0]
    assert conflict["conflict"] == rhythm.CONFLICT_COLLISION
    assert conflict["t"] == 5.05 and conflict["collides_with"] == 5.3


def test_the_cuts_come_out_in_order_whatever_the_snap_windows_overlap():
    """THE INVARIANT, not a scenario. Raised in review as a consequence of
    overlapping snap windows; worked through, it cannot happen under today's
    selection rule — any boundary inside a later reason's window and below the
    earlier placement was also inside the earlier reason's window, so it lost
    there and loses again. This sweeps the overlapping cases anyway, because the
    ORDER is what the tail walk-back and the partition attribution both assume,
    and the next change to the boundary choice would break it silently."""
    windows = [
        [(5.0, 1.0), (5.35, 5.0)],          # a far strong pause beats a near weak one
        [(5.35, 3.0), (5.2, 3.0)],          # equal weight, both in reach of both
        [(4.7, 4.0), (5.2, 4.0), (5.4, 1.0)],
        [],                                  # nothing to snap to at all
    ]
    for boundaries in windows:
        view = _propose(_segments(("speaker", 0.0, 5.0),
                                  ("visual_evidence", 5.0, 5.1),
                                  ("action", 5.1, 10.0)),
                        duration=10.0, boundaries=boundaries, scenes=[], beats=[])
        times = [c["t"] for c in view["cuts"]]
        assert times == sorted(times), f"out of order for {boundaries}"
        assert len(set(times)) == len(times), f"two cuts at one instant: {boundaries}"


# --- what cannot be known ----------------------------------------------------


def test_an_unscanned_source_is_not_a_source_that_cut_nowhere():
    """`[]` is a measurement. None is the absence of one, and reading the second
    as the first is how "no scene cuts" becomes evidence."""
    segments = _segments(("safe", 0.0, 10.0))
    measured = _propose(segments, duration=10.0, scenes=[], beats=[])
    unknown = _propose(segments, duration=10.0, scenes=None, beats=None)
    assert measured["scenes_known"] is True and measured["beats_known"] is True
    assert unknown["scenes_known"] is False and unknown["beats_known"] is False
    assert measured["cut_count"] == unknown["cut_count"] == 0


def test_no_regime_view_is_unavailable_not_a_clip_that_needs_no_cuts():
    view = rhythm.rhythm_view(duration=10.0, profile="talking_head",
                              mode="legacy_dynamic", regime_view=None,
                              boundaries=_pauses(5.0), scenes=[], beats=[],
                              style=STYLE)
    assert view["available"] is False
    assert view["unavailable_because"] == "no_regime_view"
    assert "cut_count" not in view, "a proposal that was never made has no count"


# --- the report --------------------------------------------------------------


def test_the_proposal_changes_nothing_it_describes():
    """Recorded, never applied. If the delivered plan moved, R2's contract is
    broken and the batches can no longer be told apart."""
    segments = _segments(("speaker", 0.0, 5.0), ("visual_evidence", 5.0, 10.0))
    before = [dict(s) for s in segments]
    shots = [{"index": 0, "t0": 0.0, "t1": 10.0}]
    frozen = [dict(s) for s in shots]
    view = _propose(segments, duration=10.0, boundaries=_pauses(5.0),
                    scenes=[], beats=[], legacy_shots=shots)
    assert segments == before and shots == frozen
    assert view["applied"] is False


def test_the_report_states_the_rule_it_used():
    view = _propose(_segments(("speaker", 0.0, 10.0)), duration=10.0,
                    boundaries=_pauses(5.0), scenes=[], beats=[])
    assert view["schema"] == "rhythm_view_v1"
    assert view["scope"] == "cut_proposal_from_reasons_and_boundaries"
    assert view["min_shot_s"] == 0.60 and view["snap_s"] == rhythm.SNAP_S
    assert view["admits"] == list(rhythm.ADMITS["talking_head"])


def test_what_the_profile_asks_for_and_nothing_measures_is_named():
    """§4 tells `talking_head` to reframe on a clear idea or emotion. The
    nearest thing that exists is a keyword regex, and calling a word list an
    emotion is the invented signal §3.4 forbids. So the gap is named instead of
    approximated — and that is why this profile reads below its band."""
    view = _propose(_segments(("speaker", 0.0, 60.0)), duration=60.0,
                    boundaries=_pauses(*[i * 2.0 for i in range(1, 30)]),
                    scenes=[], beats=[])
    assert view["unmeasured_rules"] == ["reframe_on_idea_or_emotion"]
    assert view["cut_count"] == 0
    assert view["pace"][0]["verdict"] == "below"


def test_a_profile_is_a_property_of_the_clip_not_of_a_moment_in_it():
    """"A profile change inside the clip does not by itself produce a cut."
    Today it cannot: `edit_profiles.resolve` answers once per clip from its
    content type, so there is no in-clip change to cut on — and no reason in the
    closed list is derived from the profile. The profile only ever decides which
    reasons are ADMITTED."""
    assert all("profile" not in reason for reason in rhythm.REASONS)
    segments = _segments(("action", 0.0, 10.0))
    args = dict(duration=10.0, boundaries=_pauses(4.0), scenes=[], beats=[4.0])
    bold = _propose(segments, profile="action", **args)
    careful = _propose(segments, profile="conservative", **args)
    # Same signals, same clip: the careful profile takes a subset, never a cut
    # in a different place.
    assert {c["t"] for c in careful["cuts"]} <= {c["t"] for c in bold["cuts"]}


def test_the_closed_lists_cover_every_profile():
    """Two tables keyed by profile stop covering the profiles the first time one
    of them is extended and the other is not."""
    from services.clipper.edit_profiles import PROFILES

    assert set(rhythm.ADMITS) == set(PROFILES)
    assert set(rhythm.UNMEASURED) == set(PROFILES)
    for admitted in rhythm.ADMITS.values():
        assert set(admitted) <= set(rhythm.REASONS)
