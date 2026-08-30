"""Batch R6: does the SOURCE already carry burned-in subtitles.

The defect is in the baseline — 15 of 15 go ghost exports shipped with two
caption systems because nothing could tell — and R0 has been reporting
`captions_duplicate_declared: unavailable` ever since, on the grounds that 0
there would turn "we never asked" into "we checked and it is clean".

What is pinned here is the JUDGEMENT, not the model: `classify` takes the
per-band evidence and returns a state, so the rule can be tested without a video
file, without weights and without a GPU. The model's own accuracy is measured
where it can be — against four sources somebody already labelled, by
`scripts/detect_source_captions.py --expect`.

THE ASYMMETRY THIS FILE IS MOSTLY ABOUT. `present` costs a caption layer
somebody can switch back on. `absent` is what turns the second layer off, so it
may only be returned when the detector actually ran and found nothing. Every
other outcome is `unknown`, which changes nothing — and most of the tests below
are one more way of arriving at that.
"""

from __future__ import annotations

from services.clipper import source_captions as sc


def _bands(*spec: tuple[int, int, float]) -> list[dict]:
    return [{"band": b, "frames": f, "widest": w} for b, f, w in spec]


#: The four labelled pilots, as the numbers the detector actually produced.
GO_GHOST = _bands((9, 9, 0.45))                    # burned captions
EMPTY: list[dict] = []                             # nothing on screen at all
HUD = _bands((8, 13, 0.079))                       # persistent, far too narrow
SCATTERED = _bands((7, 3, 0.346), (8, 3, 0.34))    # wide, but almost never


def test_the_source_with_burned_captions_is_found():
    """The case the batch exists for, at the numbers it really produced: one
    band, in 9 of 14 frames, with a line spanning 0.45 of the frame."""
    state, why, band = sc.classify(GO_GHOST, 14)
    assert state == sc.PRESENT and why is None
    assert band["band"] == 9 and band["share"] == 0.643


def test_a_persistent_hud_label_is_not_a_caption_track():
    """THE discriminator, and the one no other property supplies. This band
    carries text in 13 of 14 frames — more persistent than the real caption
    track — and it is 0.079 of the frame wide. A line of dialogue is not."""
    state, _why, band = sc.classify(HUD, 14)
    assert state == sc.ABSENT
    assert band["share"] > 0.9, "more persistent than the true positive"
    assert band["widest"] < sc.WIDTH_MAYBE


def test_wide_text_that_is_almost_never_there_is_not_a_caption_track():
    """The other negative fails on the other axis: 0.346 of the frame is wide
    enough, and three frames of fourteen is not a caption track. Both
    discriminators do work; neither is carrying the other."""
    state, _why, band = sc.classify(SCATTERED, 14)
    assert state == sc.ABSENT
    assert band["widest"] > sc.WIDTH_MIN, "wide enough on its own"
    assert band["share"] <= sc.BAND_SHARE_MAYBE


def test_a_source_with_no_text_at_all_is_absent():
    assert sc.classify(EMPTY, 14)[0] == sc.ABSENT


# --- what may not be answered ------------------------------------------------


def test_evidence_between_the_thresholds_is_refused():
    """An ambiguous source is exactly the one where switching the second caption
    layer off would be a guess."""
    state, why, _band = sc.classify(_bands((9, 5, 0.20)), 14)
    assert state == sc.UNKNOWN and why == sc.AMBIGUOUS


def test_too_few_frames_is_unknown_not_absent():
    """A handful of frames is not a distribution, and `absent` is the answer
    that turns a caption layer off."""
    state, why, band = sc.classify(GO_GHOST, 3)
    assert state == sc.UNKNOWN and why == sc.TOO_FEW and band is None


def test_no_detector_is_unknown_and_costs_nothing():
    """The dependency is optional on purpose. A missing model must not be read
    as a clean source, and must not cost the analysis run either."""
    out = sc.detect("does-not-exist.mp4", reader=None)
    assert out["state"] == sc.UNKNOWN
    assert out["why_unknown"] in (sc.NO_DETECTOR, sc.NO_VIDEO)


def test_an_unreadable_video_is_unknown(tmp_path):
    """With a detector present and a file that is not a video. `absent` here
    would disable a caption layer because a path was wrong."""
    broken = tmp_path / "not-a-video.mp4"
    broken.write_bytes(b"nope")

    class _Reader:
        def detect(self, *_a, **_k):
            raise AssertionError("must never be reached")

    out = sc.detect(str(broken), reader=_Reader())
    assert out["state"] == sc.UNKNOWN and out["why_unknown"] == sc.NO_VIDEO


def test_a_detector_that_throws_does_not_become_absent():
    """One frame the model cannot handle is not evidence about the source. It
    is skipped, and if every frame is skipped the bands are empty — which is
    `absent` only because the frames WERE sampled and looked at."""
    class _Angry:
        def detect(self, *_a, **_k):
            raise RuntimeError("bad frame")

    bands = sc._bands([_Frame(), _Frame()], _Angry())
    assert all(b["frames"] == 0 for b in bands)


class _Frame:
    shape = (270, 480, 3)


# --- the report --------------------------------------------------------------


def test_the_thresholds_ride_with_the_answer():
    """They were chosen on four sources with the labels visible. A report that
    hid that would be claiming a calibration nobody ran."""
    out = sc.detect("nowhere.mp4", reader=None)
    assert out["schema"] == "source_captions_v1"
    assert out["calibrated"] is False
    assert out["thresholds"]["band_share_min"] == sc.BAND_SHARE_MIN
    assert out["thresholds"]["width_min"] == sc.WIDTH_MIN


def test_every_state_and_reason_is_in_a_closed_list():
    for bands, sampled in ((GO_GHOST, 14), (HUD, 14), (EMPTY, 14),
                           (GO_GHOST, 2), (_bands((9, 5, 0.20)), 14)):
        state, why, _band = sc.classify(bands, sampled)
        assert state in sc.STATES
        assert why is None or why in sc.REASONS
