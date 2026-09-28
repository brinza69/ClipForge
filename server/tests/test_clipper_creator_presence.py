"""Batch R3a: is the CREATOR there, not is anyone there.

On a reaction stream the detector reports plenty of faces and they are real —
they are the faces in the video being watched. Measured on the Moist pilot: of
the 1.285 samples that carry a face, 464 are ANCHOR-COMPATIBLE. The rest are not,
and they were holding the crop.

The distinction is not pedantry. Nobody has labelled those boxes; geometry is
all this knows. "Not on the fixed anchor" is what is measured, "not the creator"
is what is inferred from it, and writing the second where the first is true is
how a measurement turns into a claim it cannot support.

How much of the PRESENCE timeline that moves is not quoted anywhere: the stored
`faces.json` has a 6.7s median gap, which rounds `ENTER_S` to a single sample
and so measures a rule production does not run. Anchor compatibility is a
property of the boxes and survives the resolution difference; the presence
effect does not.

Recorded, never applied. Everything here computes what the composition WOULD be;
the delivered plan is untouched, because `legacy_dynamic` is the rollback and
the shadow export has to stay what R2 froze.
"""

from __future__ import annotations

from services.clipper import dynamic_subject as subject

# A fixed overlay in the top-left, the shape `stable_track` actually found on
# the Moist pilot: anchor at (34, 141), 25px wide.
ANCHOR = {"cx": 34.0, "cy": 141.0, "w": 25.0, "spread": 4.1, "runner_up": 12.21}


def _sample(t: float, *boxes) -> dict:
    return {"t": t, "boxes": [list(b) for b in boxes]}


def _creator_box() -> list[float]:
    return [22.0, 129.0, 25.0, 25.0]        # centred on the anchor


def _other_box() -> list[float]:
    return [600.0, 300.0, 90.0, 90.0]       # a face in the video being watched


def test_a_face_on_the_anchor_survives_and_one_elsewhere_does_not():
    track = [_sample(0.0, _creator_box(), _other_box())]
    kept = subject.anchored_track(track, ANCHOR)
    assert kept[0]["boxes"] == [_creator_box()]


def test_samples_are_never_dropped_only_their_boxes():
    """The timeline is indexed by position. Compressing it would shift every
    time after the first removal — a subtler failure than the one being fixed."""
    track = [_sample(0.0, _creator_box()), _sample(0.25, _other_box()),
             _sample(0.5), _sample(0.75, _other_box(), _creator_box())]
    kept = subject.anchored_track(track, ANCHOR)
    assert len(kept) == len(track)
    assert [s["t"] for s in kept] == [0.0, 0.25, 0.5, 0.75]
    assert [len(s["boxes"]) for s in kept] == [1, 0, 0, 1]


def test_no_anchor_means_no_filtering_at_all():
    """`stable_track` returns None for three of the four pilots. On those the
    raw track IS the identity track — a source with one subject is the case
    that already works, and R3a must not touch it."""
    track = [_sample(0.0, _other_box()), _sample(0.25, _creator_box())]
    assert subject.anchored_track(track, None) == track
    assert subject.anchored_track(track, {}) == track
    assert subject.anchored_track(track, {"cx": None}) == track


def test_the_creators_absence_is_marked_from_where_it_began():
    """Waiting ENTER_S before believing the subject is gone stops a blink from
    splitting a shot — and leaves the first three seconds of every real absence
    framed on someone who is not there. Exactly the wrong three seconds."""
    hop = 0.25
    track = ([_sample(i * hop, _creator_box()) for i in range(4)]
             + [_sample((4 + i) * hop) for i in range(20)])

    # ENTER_S is 3.0s, so at a 0.25s hop the old rule keeps saying "someone is
    # there" for twelve samples after the face stops: True through index 14.
    lagging = subject.presence_timeline(track, hop=hop)
    assert lagging[:15] == [True] * 15, "the old behaviour, kept as the contrast"
    assert not lagging[15]

    creator = subject.creator_presence(track, ANCHOR, hop=hop)
    assert creator[:4] == [True] * 4
    assert not any(creator[4:]), "the absence must start where the face stopped"


def test_only_the_disappearance_is_back_dated():
    """The return already has `span_has_subject`'s unanimous-raw-evidence rule.
    Back-dating it too would eat into an absence that genuinely happened."""
    hop = 0.25
    track = ([_sample(i * hop) for i in range(20)]
             + [_sample((20 + i) * hop, _creator_box()) for i in range(8)])
    creator = subject.creator_presence(track, ANCHOR, hop=hop)
    # The run of real absence is not shortened by the return that follows it.
    assert not creator[19]


def test_a_reacted_face_no_longer_holds_the_crop():
    """The whole batch, in one case: the anchor goes empty, the video being
    watched still shows a face, and the composition has to stop pretending."""
    hop = 0.25
    track = ([_sample(i * hop, _creator_box()) for i in range(8)]
             + [_sample((8 + i) * hop, _other_box()) for i in range(20)])

    anyone = subject.presence_timeline(track, hop=hop)
    creator = subject.creator_presence(track, ANCHOR, hop=hop)
    assert all(anyone), "someone is on screen throughout — that is the trap"
    assert not any(creator[8:]), "but the creator is not"


def test_the_proposal_is_computed_over_the_shots_that_exist():
    """No regimes, no active speaker, no new boundaries. Those are R3b, and
    mixing them in would make it impossible to tell which change moved which
    frame."""
    hop = 0.25
    track = ([_sample(i * hop, _creator_box()) for i in range(8)]
             + [_sample((8 + i) * hop, _other_box()) for i in range(24)])
    shots = [{"index": 0, "t0": 0.0, "t1": 2.0, "composition": "crop"},
             {"index": 1, "t0": 2.0, "t1": 8.0, "composition": "crop"}]

    view = subject.proposed_compositions(shots, track, ANCHOR, hop=hop)
    assert [s["index"] for s in view["shots"]] == [0, 1]
    assert view["shots"][0]["composition"] == "crop"
    assert view["shots"][1]["composition"] == "fit"
    assert view["changed"] == 1
    assert view["reason"] == "anchored"
    assert view["face_samples"] == 32 and view["compatible_samples"] == 8


def test_the_proposal_does_not_touch_the_plan_it_describes():
    """Recorded, never applied. If the shadow export changes, R2's contract is
    broken and the two batches can no longer be told apart."""
    shots = [{"index": 0, "t0": 0.0, "t1": 2.0, "composition": "crop"}]
    before = [dict(s) for s in shots]
    subject.proposed_compositions(shots, [_sample(0.0, _other_box())], ANCHOR)
    assert shots == before


def test_without_an_anchor_compatibility_is_unknown_not_perfect():
    """`anchored_track` returns the raw track, so every sample "passes" — but
    nothing was compared against anything. Reporting that as full compatibility
    is the same lie as the one this batch fixes, told in the other direction."""
    view = subject.proposed_compositions(
        [{"index": 0, "t0": 0.0, "t1": 1.0, "composition": "crop"}],
        [_sample(0.0, _other_box())], None)
    assert view["reason"] == "no_anchor"
    assert view["anchor"] is None
    assert view["compatible_samples"] is None
    assert view["anchor_tolerance_px"] is None
    assert view["face_samples"] == 1


def test_without_an_anchor_the_proposal_can_still_differ():
    """Two changes ship in R3a, not one. Filtering is an identity without an
    anchor; the RETROSPECTIVE hysteresis is not, and it applies either way. A
    test that asserted "no anchor means nothing changes" would be pinning a
    coincidence of its own fixture."""
    hop = 0.25
    track = ([_sample(i * hop, _other_box()) for i in range(4)]
             + [_sample((4 + i) * hop) for i in range(24)])
    # The shot sits wholly inside the absence, where the old timeline was still
    # claiming a subject because the flip had not been confirmed yet.
    shots = [{"index": 0, "t0": 2.0, "t1": 5.0, "composition": "crop"}]

    assert subject.anchored_track(track, None) == track, "the track is untouched"
    view = subject.proposed_compositions(shots, track, None, hop=hop)
    assert view["reason"] == "no_anchor"
    # The delivered plan called this shot `crop`, because the old timeline was
    # still lagging three seconds behind the disappearance.
    assert view["shots"][0]["delivered"] == "crop"
    assert view["shots"][0]["composition"] == "fit"
    assert view["changed"] == 1


def test_the_proposal_states_the_rule_it_used():
    """A number whose rule nobody can reconstruct is not evidence. The tolerance
    in particular: the anchor here is 25px wide and the floor is 40, so the
    effective tolerance is 40 — calling it "one face width" would be wrong on
    the only source that exercises it."""
    view = subject.proposed_compositions(
        [{"index": 0, "t0": 0.0, "t1": 1.0, "composition": "crop"}],
        [_sample(0.0, _creator_box())], ANCHOR, hop=0.25)
    assert view["schema"] == "creator_view_v1"
    assert view["scope"] == "composition_only_existing_shots"
    assert view["sample_hop_s"] == 0.25
    assert view["enter_s"] == subject.ENTER_S and view["leave_s"] == subject.LEAVE_S
    assert view["retrospective"] is True
    assert view["anchor_coordinate_space"] == "proxy"
    assert view["anchor_tolerance_px"] == 40.0
    assert view["total_samples"] == 1
