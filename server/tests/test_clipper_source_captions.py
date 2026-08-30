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

THE ASYMMETRY THIS FILE IS MOSTLY ABOUT, and an earlier version of it had the
direction backwards. §R6: `present` means the source already carries captions,
so ClipForge's layer is DISABLED; `absent` keeps it; `unknown` changes nothing.
A wrong `present` therefore ships a clip with NO captions, which is why it is
the state that needs both bars cleared — and why `absent` needs EVERY band to be
clearly negative while `present` needs only one to be clearly positive.
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


def test_a_watermark_cannot_hide_a_real_caption_track():
    """THE band-order bug. Judging only the band with the most frames let a
    label win every time: a watermark at 14 of 14 and 0.08 wide beat the real
    caption track at 9 of 14 and 0.45, and the source came back `absent` with
    the caption band never examined."""
    both = _bands((8, 14, 0.08), (9, 9, 0.45))
    state, _why, band = sc.classify(both, 14)
    assert state == sc.PRESENT
    assert band["band"] == 9, "the band that decided has to be the caption one"


def test_absent_needs_every_band_to_be_negative():
    """`present` is reachable on one band because it is the state that
    suppresses ClipForge's captions; `absent` is not, because it is the state
    that would leave a duplicate in place."""
    assert sc.classify(_bands((8, 14, 0.08), (9, 9, 0.45)), 14)[0] == sc.PRESENT
    assert sc.classify(_bands((8, 14, 0.08), (2, 1, 0.05)), 14)[0] == sc.ABSENT


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


def test_too_few_frames_is_unknown_not_a_verdict():
    """A handful of frames is not a distribution."""
    state, why, band = sc.classify(GO_GHOST, 3)
    assert state == sc.UNKNOWN and why == sc.TOO_FEW and band is None


def test_no_detector_is_unknown_and_costs_nothing():
    """The dependency is optional on purpose. A missing model must not be read
    as a clean source, and must not cost the analysis run either."""
    out = sc.detect("does-not-exist.mp4", reader=None)
    assert out["state"] == sc.UNKNOWN
    assert out["why_unknown"] in (sc.NO_DETECTOR, sc.NO_VIDEO)


def test_an_unreadable_video_is_unknown(tmp_path):
    """With a detector present and a file that is not a video. A verdict here
    would be a verdict about a path, not about a source."""
    broken = tmp_path / "not-a-video.mp4"
    broken.write_bytes(b"nope")

    class _Reader:
        def detect(self, *_a, **_k):
            raise AssertionError("must never be reached")

    out = sc.detect(str(broken), reader=_Reader())
    assert out["state"] == sc.UNKNOWN and out["why_unknown"] == sc.NO_VIDEO


def test_a_detector_that_throws_on_every_frame_is_not_a_clean_source():
    """The bug my own test missed: it asserted the bands were empty and never
    checked the VERDICT. Empty bands are indistinguishable from a source with no
    text in them, so the denominator has to be what the model got through — not
    what was read off disk."""
    class _Angry:
        def detect(self, *_a, **_k):
            raise RuntimeError("bad frame")

    frames = [_Frame() for _ in range(14)]
    bands, analysed = sc._bands(frames, _Angry())
    assert all(b["frames"] == 0 for b in bands)
    assert analysed == 0, "nothing was analysed, whatever was sampled"
    state, why, _band = sc.classify(bands, analysed)
    assert state == sc.UNKNOWN and why == sc.TOO_FEW


def test_a_share_is_taken_over_what_was_analysed_not_what_was_sampled():
    """Half the frames thrown on would otherwise halve every share and turn a
    caption track into an absence."""
    class _Half:
        def __init__(self):
            self.n = 0

        def detect(self, *_a, **_k):
            self.n += 1
            if self.n % 2:
                raise RuntimeError("bad frame")
            return ([[(100, 400, 200, 230)]],)

    bands, analysed = sc._bands([_Frame() for _ in range(20)], _Half())
    assert analysed == 10
    assert sc.classify(bands, analysed)[0] == sc.PRESENT


class _Frame:
    shape = (270, 480, 3)


# --- the report --------------------------------------------------------------


def test_only_present_may_disable_the_clipforge_layer():
    """THE DIRECTION, executable. An earlier version of this batch had it
    backwards in the prose while the logic was right — and prose cannot fail a
    test run. Following that paragraph would have disabled captions on every
    source WITHOUT them and left the duplicates exactly where the defect is.

    Whatever consumes this signal reads the constant, not the docstring."""
    assert sc.DISABLES_OWN_CAPTIONS == sc.PRESENT
    assert sc.disables_own_captions(sc.PRESENT) is True
    assert sc.disables_own_captions(sc.ABSENT) is False
    assert sc.disables_own_captions(sc.UNKNOWN) is False
    # And every state the classifier can produce answers the question.
    for state in sc.STATES:
        assert isinstance(sc.disables_own_captions(state), bool)


def test_the_state_that_disables_needs_the_strongest_evidence():
    """The asymmetry follows from the direction rather than from taste: the
    expensive answer is the one that suppresses our captions, so it is the one
    that has to clear both bars — while the cheap one needs every band to
    agree."""
    one_strong_band = _bands((8, 14, 0.08), (9, 9, 0.45))
    assert sc.classify(one_strong_band, 14)[0] == sc.DISABLES_OWN_CAPTIONS
    assert not sc.disables_own_captions(sc.classify(HUD, 14)[0])
    assert not sc.disables_own_captions(sc.classify(SCATTERED, 14)[0])
    assert not sc.disables_own_captions(sc.classify(EMPTY, 14)[0])
    assert not sc.disables_own_captions(sc.classify(GO_GHOST, 3)[0])


def test_a_malformed_box_does_not_escape_the_module(monkeypatch):
    """The contract is "optional signal, never raises", and only the `detect()`
    CALL was inside the `try` — the parsing was outside it, so a box CRAFT
    returned with three coordinates instead of four raised `IndexError` straight
    out of the module. Through `detect()`, because that is the surface the
    contract is about."""
    class _Malformed:
        def detect(self, *_a, **_k):
            return ([[(100, 400, 200)]],)          # three coordinates, not four

    monkeypatch.setattr(sc, "_sample", lambda *_a, **_k: [_Frame()] * 14)
    out = sc.detect("anything.mp4", reader=_Malformed())
    assert out["state"] == sc.UNKNOWN
    assert out["analysed"] == 0 and out["failed"] == 14


def test_a_frame_contributes_everything_or_nothing(monkeypatch):
    """A partial frame measures the model's failure, not the source. One bad box
    among good ones must not leave the good ones counted."""
    class _OneBad:
        def detect(self, *_a, **_k):
            return ([[(10, 300, 200, 230), (5, 9)]],)

    monkeypatch.setattr(sc, "_sample", lambda *_a, **_k: [_Frame()] * 14)
    out = sc.detect("anything.mp4", reader=_OneBad())
    assert out["bands"] == [], "a partial frame left partial evidence behind"
    assert out["failed"] == 14


def test_a_non_finite_coordinate_fails_the_frame(monkeypatch):
    """NaN survives every arithmetic check and would place a band at an
    arbitrary index."""
    class _Nan:
        def detect(self, *_a, **_k):
            return ([[(10, float("nan"), 200, 230)]],)

    monkeypatch.setattr(sc, "_sample", lambda *_a, **_k: [_Frame()] * 14)
    assert sc.detect("anything.mp4", reader=_Nan())["analysed"] == 0


def test_a_box_that_cannot_fit_the_frame_fails_it(monkeypatch):
    """FINITE IS NOT ENOUGH. A box the detector reports as running from 0 to a
    million is a perfectly finite number and a width of two thousand frames,
    which clears the width bar on its own and manufactures a `present` — the
    one state that switches ClipForge's captions off."""
    monkeypatch.setattr(sc, "_sample", lambda *_a, **_k: [_Frame()] * 14)

    class _Huge:
        def detect(self, *_a, **_k):
            return ([[(0, 1_000_000, 200, 230)]],)

    out = sc.detect("anything.mp4", reader=_Huge())
    assert out["analysed"] == 0
    assert not sc.disables_own_captions(out["state"])

    class _Inverted:
        def detect(self, *_a, **_k):
            return ([[(400, 100, 200, 230)]],)     # x1 before x0

    assert sc.detect("anything.mp4", reader=_Inverted())["analysed"] == 0

    class _Negative:
        def detect(self, *_a, **_k):
            return ([[(-50, 300, 200, 230)]],)

    assert sc.detect("anything.mp4", reader=_Negative())["analysed"] == 0


def test_a_box_touching_the_frame_edge_is_ordinary(monkeypatch):
    """The guard has to reject the impossible without rejecting a caption that
    reaches the edge, which is what a full-width subtitle does."""
    monkeypatch.setattr(sc, "_sample", lambda *_a, **_k: [_Frame()] * 14)

    class _FullWidth:
        def detect(self, *_a, **_k):
            return ([[(0, 480, 200, 230)]],)       # exactly the frame width

    out = sc.detect("anything.mp4", reader=_FullWidth())
    assert out["analysed"] == 14
    assert out["state"] == sc.PRESENT


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
