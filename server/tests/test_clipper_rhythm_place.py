"""Batch R4: WHERE an earned cut lands, and when it cannot land anywhere.

Split from `test_clipper_rhythm.py` at the repo's 500-line limit, on the seam
the batch's own rule draws — a cut needs a REASON and a PLACE. That file pins
what earns a cut; this one pins the placement, which is where every review
finding in this batch has been.

The list, because each was found rather than reasoned about, and each looked
fine until someone asked the next question:

- Two required changes may not collapse onto one pause. The treatment that
  exists only between them would never be shown, and no cut, hold or violation
  would say so.
- A snap may not cross a required change in EITHER direction. Forwards, the shot
  before the cut shows the framing from before the change. Backwards, the cut
  that introduces this change happens before the previous one has begun. Cut
  ORDER catches neither.
- A snap may not reach into the tail zone, or the walk-back drops the cut and
  reports a requirement as impossible that its own moment satisfies easily.
- `duration - min_shot_s` is the LAST LEGAL place, not the first illegal one.
- The bounds must be tested at the resolution the cut is placed at, or a pause
  at 5.0499996 clears a ceiling of 5.05 and then lands exactly on it.
- Two reasons share a cut only when they were ASKED at the same moment. Where
  the cut ended up after snapping is not what anything asked for.
"""

from __future__ import annotations

from services.clipper import dynamic_rhythm as rhythm

from tests.test_clipper_rhythm import _pauses, _propose, _segments


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


def test_two_required_changes_fifty_milliseconds_apart_each_get_a_cut():
    """The rule at the resolution where it is hardest, and the case that caught
    the snap ceiling. 5.0 and 5.05, with the only pause at 5.3: the first may
    not move past the second, so it stays at its own moment and the second takes
    the pause. Both treatments reach the screen, and the 300ms shot between them
    is recorded as the flicker it is rather than dissolved into one clean cut."""
    view = _propose(_segments(("speaker", 0.0, 5.0), ("visual_evidence", 5.0, 5.05),
                              ("speaker", 5.05, 10.0)),
                    duration=10.0, boundaries=_pauses(5.3), scenes=[], beats=[])
    assert [c["t"] for c in view["cuts"]] == [5.0, 5.3]
    assert [c["placement"] for c in view["cuts"]] == ["unsnapped", "snapped"]
    assert view["required_conflicts"] == [], "both are materialisable"
    assert [v["shot_s"] for v in view["min_shot_violations"]] == [0.3]


def test_a_cut_may_not_snap_past_the_next_required_change():
    """Moving a cut forward over a later requirement keeps the list in order and
    still loses the treatment: the shot before it shows the framing from BEFORE
    this change, so the stretch between the two is never on screen at all. The
    pause at 5.35 is inside the first change's reach and beyond the second's
    moment, so it is not available to the first."""
    view = _propose(_segments(("speaker", 0.0, 5.0), ("visual_evidence", 5.0, 5.1),
                              ("speaker", 5.1, 10.0)),
                    duration=10.0, boundaries=_pauses(5.35), scenes=[], beats=[])
    assert [c["t"] for c in view["cuts"]] == [5.0, 5.35]
    assert view["cuts"][0]["placement"] == "unsnapped"
    assert view["required_conflicts"] == []


def test_a_cut_may_not_snap_back_before_the_previous_required_change():
    """The mirror of the rule above, and cut ORDER does not catch it: a cut
    placed before the previous CHANGE but after the previous CUT is still in
    order. Here the change at 5.0 snaps back to 4.8 and the one at 5.2 could
    follow it to 4.9 — in order, and delivering the second treatment before the
    first has begun. The floor keeps 4.9 out of the second's reach."""
    view = _propose(_segments(("speaker", 0.0, 5.0), ("visual_evidence", 5.0, 5.2),
                              ("action", 5.2, 10.0)),
                    duration=10.0, boundaries=[(4.8, 5.0), (4.9, 4.0)],
                    scenes=[], beats=[])
    times = [c["t"] for c in view["cuts"]]
    assert times == [4.8, 5.2]
    assert view["cuts"][1]["placement"] == "unsnapped"
    assert view["required_conflicts"] == []


def test_a_boundary_is_tested_at_the_resolution_it_is_placed_at():
    """The bounds filtered the RAW boundary time while the cut was placed at the
    rounded one. A pause at 5.0499996 passed a ceiling of 5.05 and then landed
    exactly on it — on the very change it was forbidden to cross, where that
    change merged into it and the treatment between the two vanished again."""
    view = _propose(_segments(("speaker", 0.0, 5.0), ("visual_evidence", 5.0, 5.05),
                              ("action", 5.05, 10.0)),
                    duration=10.0, boundaries=_pauses(5.0499996),
                    scenes=[], beats=[])
    assert [c["t"] for c in view["cuts"]] == [5.0, 5.05]
    assert view["cuts"][0]["placement"] == "unsnapped", "the pause was out of reach"
    assert view["required_conflicts"] == []


def test_a_cut_never_answers_two_requests_asked_at_different_moments():
    """THE INVARIANT behind the merge. Two reasons share a cut only when they
    were ASKED at the same moment — `existing["t"]` is where the cut ended up
    after snapping, which may be nowhere near what anything asked for, and
    testing against it folded a treatment away."""
    fixtures = [
        (_segments(("speaker", 0.0, 5.0), ("visual_evidence", 5.0, 5.05),
                   ("action", 5.05, 10.0)), _pauses(5.2), [5.05], []),
        (_segments(("speaker", 0.0, 5.0), ("visual_evidence", 5.0, 5.4),
                   ("action", 5.4, 10.0)), _pauses(5.2, 5.4), [5.0], [5.2]),
        (_segments(("safe", 0.0, 10.0)), _pauses(3.0, 3.2), [3.0, 3.2], [3.1]),
    ]
    for segments, boundaries, scenes, beats in fixtures:
        view = _propose(segments, profile="action", duration=10.0,
                        boundaries=boundaries, scenes=scenes, beats=beats)
        for cut in view["cuts"]:
            moments = {m for _r, m in cut["requests"]}
            assert len(moments) == 1, f"{cut['requests']} folded into one cut"


def test_a_snap_may_not_manufacture_a_tail_conflict():
    """A required change at 9.2 of a 10s clip is materialisable: cutting at its
    own moment leaves an 0.8s shot. Letting it snap forward to the pause at 9.5
    made the tail walk-back drop it and report a requirement as impossible — a
    conflict invented by the placement rather than found in the timeline."""
    view = _propose(_segments(("speaker", 0.0, 9.2), ("visual_evidence", 9.2, 10.0)),
                    duration=10.0, boundaries=_pauses(9.5), scenes=[], beats=[])
    assert [c["t"] for c in view["cuts"]] == [9.2]
    assert view["cuts"][0]["placement"] == "unsnapped"
    assert view["required_conflicts"] == []


def test_the_last_legal_place_for_a_cut_is_still_a_place():
    """`duration - min_shot_s` is legal: the shot after it is exactly the
    minimum, which passes. Excluding it threw away the only boundary the rule
    allows and sent the change to its own moment for no reason."""
    view = _propose(_segments(("speaker", 0.0, 9.2), ("visual_evidence", 9.2, 10.0)),
                    duration=10.0, boundaries=_pauses(9.4), scenes=[], beats=[])
    assert [c["t"] for c in view["cuts"]] == [9.4]
    assert view["cuts"][0]["placement"] == "snapped"
    assert view["required_conflicts"] == []


def test_the_conflicts_come_out_in_time_order():
    """The tail walk-back appends from the end, so two conflicts came out
    reversed. A timeline that reads backwards is not one an auditor follows."""
    view = _propose(_segments(("speaker", 0.0, 9.5), ("visual_evidence", 9.5, 9.8),
                              ("action", 9.8, 10.0)),
                    duration=10.0, boundaries=[], scenes=[], beats=[])
    # Both are dropped, and dropping the later one leaves the earlier just as
    # close to the end — which is why the walk-back is a loop.
    times = [c["t"] for c in view["required_conflicts"]]
    assert times == [9.5, 9.8]
    assert view["cuts"] == [] and view["min_shot_violations"] == []


def test_a_dropped_tail_cut_keeps_every_reason_it_answered():
    """One cut can answer a treatment change and a source cut at once. Routing
    only the first reason threw the other provenance away, and a requirement
    that appears nowhere in the report is worse than one reported as impossible."""
    view = _propose(_segments(("speaker", 0.0, 9.9), ("visual_evidence", 9.9, 10.0)),
                    profile="exploration", duration=10.0,
                    boundaries=[(9.9, 2.0)], scenes=[9.9], beats=[])
    assert view["cuts"] == []
    assert [c["reason"] for c in view["required_conflicts"]] == [rhythm.REASON_TREATMENT]
    assert view["held_reasons"] == {rhythm.HOLD_TAIL_MIN_SHOT: 1}
    assert view["held"][0]["reason"] == rhythm.REASON_SCENE


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
