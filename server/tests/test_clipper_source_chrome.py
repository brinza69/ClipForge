"""Batch R6: is a video player's chrome burned into the export.

The thing these guard is that `not_detected` never becomes "clean". The
recogniser reads this content badly — on the one source known to carry burned-in
captions it produced eight tokens and none above confidence 0.5 — so recall is
low and undemonstrated, and only `detected` is a claim.

The threshold is checked against the measurement it came from: 14 positives with
5 to 32 frames each, 27 negatives with one hit between them.
"""

from __future__ import annotations

from services.clipper import source_chrome as sc


class _Reader:
    """An easyocr stand-in. `frames` is a list of `[(box, text, conf)]`."""

    def __init__(self, frames, throw_on=()):
        self.frames = frames
        self.throw_on = set(throw_on)
        self.calls = 0

    def readtext(self, frame, **kw):
        i = self.calls
        self.calls += 1
        if i in self.throw_on:
            raise RuntimeError("model failed")
        return self.frames[i]


def _box(y=0.05, height=1920):
    top = y * height
    return [[0, top], [100, top], [100, top + 20], [0, top + 20]]


# --- the threshold, against the measurement it came from ---------------------


def test_two_frames_is_the_threshold_and_the_margin_is_real():
    """5-against-1: the weakest positive had five frames with a hit and the
    strongest negative had one. Both directions are checked, because a rule that
    only ever fires is not a rule."""
    assert sc.classify([1, 1, 0, 0, 0, 0], analysed=6)[0] == sc.DETECTED
    assert sc.classify([1, 0, 0, 0, 0, 0], analysed=6)[0] == sc.NOT_DETECTED
    assert sc.classify([0, 0, 0, 0, 0, 0], analysed=6)[0] == sc.NOT_DETECTED
    # The weakest real positive.
    assert sc.classify([1] * 5 + [0] * 40, analysed=45)[0] == sc.DETECTED
    # The strongest real negative.
    assert sc.classify([1] + [0] * 48, analysed=49)[0] == sc.NOT_DETECTED


def test_the_denominator_is_what_was_analysed_not_what_was_sampled():
    """A model that throws on every frame leaves no hits, and no hits from no
    looks is indistinguishable from no hits from many — the mistake
    `source_captions` had to be taught, one module along."""
    state, why = sc.classify([], analysed=0)
    assert state == sc.UNAVAILABLE and why == sc.DETECTOR_FAILED

    state, why = sc.classify([0, 0], analysed=2)
    assert state == sc.UNAVAILABLE and why == sc.TOO_FEW


def test_only_detected_is_a_claim():
    assert sc.is_a_warning(sc.DETECTED) is True
    assert sc.is_a_warning(sc.NOT_DETECTED) is False
    assert sc.is_a_warning(sc.UNAVAILABLE) is False


# --- the vocabulary ----------------------------------------------------------


def test_the_five_labels_that_actually_fired_are_matched():
    for label in ("Watch later", "SHARE", "SAVE", "Search", "Subscribe"):
        assert sc.CONTROLS.match(label), label


def test_a_word_that_merely_contains_a_control_is_not_one():
    """Anchored at both ends: a subtitle saying "I will share this" is not a
    share button."""
    for text in ("I will share this", "shared", "research", "saved the day",
                 "", "watch later than usual"):
        assert not sc.CONTROLS.match(text), text


# --- what it reads off a frame -----------------------------------------------


def test_a_low_confidence_read_is_not_a_read():
    """The garbage that killed the URL hypothesis came back at 0.00 to 0.01."""
    reader = _Reader([[(_box(), "SHARE", 0.59)], [(_box(), "SHARE", 0.61)]])
    assert sc._frame_hits(object_frame(), reader) == []
    got = sc._frame_hits(object_frame(), reader)
    assert len(got) == 1 and got[0]["text"] == "SHARE"


def test_a_frame_the_model_throws_on_contributes_nothing_at_all():
    """ATOMIC, like `source_captions._frame_bands`: a partial frame is a
    measurement of the model's failure, not of the picture."""
    reader = _Reader([[]], throw_on={0})
    assert sc._frame_hits(object_frame(), reader) is None


def test_a_frame_with_no_size_is_refused():
    class _Tiny:
        shape = (0, 0)

    assert sc._frame_hits(_Tiny(), _Reader([[]])) is None


def object_frame():
    class _Frame:
        shape = (1920, 1080)

    return _Frame()


# --- end to end, with the video reader stubbed out ---------------------------


def _run(monkeypatch, frames, throw_on=()):
    samples = [(float(i), object_frame()) for i in range(len(frames))]
    monkeypatch.setattr(sc, "_sample", lambda video, every_s: samples)
    return sc.detect("x.mp4", reader=_Reader(frames, throw_on))


def test_the_positive_shape_is_detected(monkeypatch):
    """What a Moist export looks like: a control label in most frames."""
    frames = [[(_box(0.056), "Watch later", 0.91)] for _ in range(10)]
    got = _run(monkeypatch, frames)
    assert got["state"] == sc.DETECTED
    assert got["frames_with_a_control"] == 10
    assert got["frames_analysed"] == 10
    assert len(got["hits"]) == 10


def test_the_negative_shape_is_not_detected_and_that_is_not_clean(monkeypatch):
    """One isolated `Search` in one frame out of 49 — the strongest negative in
    the corpus. `not_detected`, and nothing about it says the export is fit to
    publish."""
    frames = [[] for _ in range(48)]
    frames.insert(27, [(_box(0.551), "Search", 0.73)])
    got = _run(monkeypatch, frames)
    assert got["state"] == sc.NOT_DETECTED
    assert got["frames_with_a_control"] == 1
    assert sc.is_a_warning(got["state"]) is False


def test_every_frame_failing_is_unavailable_and_not_not_detected(monkeypatch):
    frames = [[] for _ in range(10)]
    got = _run(monkeypatch, frames, throw_on=set(range(10)))
    assert got["state"] == sc.UNAVAILABLE
    assert got["why_unavailable"] == sc.DETECTOR_FAILED
    assert got["frames_analysed"] == 0
    assert got["frames_sampled"] == 10, "sampled is not analysed"


def test_a_missing_recogniser_costs_the_run_nothing(monkeypatch):
    monkeypatch.setattr(sc, "_reader", lambda: None)
    got = sc.detect("x.mp4")
    assert got["state"] == sc.UNAVAILABLE
    assert got["why_unavailable"] == sc.NO_DETECTOR


def test_an_unreadable_video_is_not_a_clean_one(monkeypatch):
    monkeypatch.setattr(sc, "_sample", lambda video, every_s: [])
    got = sc.detect("x.mp4", reader=_Reader([]))
    assert got["state"] == sc.UNAVAILABLE
    assert got["why_unavailable"] == sc.NO_VIDEO


def test_it_never_claims_to_be_calibrated_or_applied(monkeypatch):
    got = _run(monkeypatch, [[] for _ in range(10)])
    assert got["calibrated"] is False
    assert got["applied"] is False


# --- the denominator cannot be handed in -------------------------------------


def test_the_counts_and_the_denominator_have_to_agree():
    """`classify([1, 1], analysed=6)` answered `detected`, honouring a
    `SAMPLES_MIN` of 6 over a list of two: the denominator was whatever the
    caller said it was."""
    assert sc.classify([1, 1], analysed=6) == (sc.UNAVAILABLE,
                                               sc.COUNTS_DISAGREE)
    assert sc.classify([1, 1, 0, 0, 0, 0], analysed=6)[0] == sc.DETECTED
    for bad in ("11", None, 7):
        assert sc.classify(bad, analysed=6)[0] == sc.UNAVAILABLE, repr(bad)
    for bad in ([1, -1, 0, 0, 0, 0], [1, True, 0, 0, 0, 0],
                [1, 1.0, 0, 0, 0, 0], [1, None, 0, 0, 0, 0]):
        assert sc.classify(bad, analysed=6) == (sc.UNAVAILABLE,
                                                sc.COUNTS_DISAGREE), repr(bad)
    assert sc.classify([0] * 6, analysed=True)[0] == sc.UNAVAILABLE


# --- an impossible read fails the whole frame --------------------------------


def test_a_confidence_that_is_not_a_confidence_fails_the_frame():
    """A NaN passed `value < CONF_MIN` — every comparison against a NaN is
    false — and manufactured a `detected` out of a model that had failed."""
    for bad in (float("nan"), float("inf"), -0.1, 1.5):
        reader = _Reader([[(_box(), "SHARE", bad)]])
        assert sc._frame_hits(object_frame(), reader) is None, repr(bad)


def test_a_coordinate_that_is_not_a_coordinate_fails_the_frame():
    """A NaN reached the report, and from there the JSON."""
    nan = float("nan")
    for box in ([[0, nan], [100, nan], [100, 20], [0, 20]],
                [[nan, 0], [100, 0], [100, 20], [nan, 20]]):
        reader = _Reader([[(box, "SHARE", 0.9)]])
        assert sc._frame_hits(object_frame(), reader) is None


def test_a_box_outside_the_frame_fails_the_frame():
    """It was kept as if it had been read. Two pixels of slack for a box that
    touches the edge, the same `source_captions` allows."""
    for box in ([[0, 3000], [100, 3000], [100, 3020], [0, 3020]],
                [[0, -5], [100, -5], [100, 20], [0, 20]],
                [[2000, 0], [2100, 0], [2100, 20], [2000, 20]]):
        reader = _Reader([[(box, "SHARE", 0.9)]])
        assert sc._frame_hits(object_frame(), reader) is None

    edge = [[0, 1900], [100, 1900], [100, 1922], [0, 1922]]
    got = sc._frame_hits(object_frame(), _Reader([[(edge, "SHARE", 0.9)]]))
    assert got is not None and len(got) == 1, "two pixels past is ordinary"


# --- the cadence the threshold was measured at -------------------------------


def test_a_different_cadence_is_refused_rather_than_rescaled(monkeypatch):
    """`FRAMES_MIN` counts frames, so two frames is 0.1 seconds of video at one
    cadence and 60 at another. The separation was measured at exactly one frame
    per second."""
    monkeypatch.setattr(sc, "_sample",
                        lambda video, every_s: [(0.0, object_frame())] * 10)
    for bad in (2.0, 0.5, 0, None, "1.0"):
        got = sc.detect("x.mp4", every_s=bad, reader=_Reader([[]] * 10))
        assert got["state"] == sc.UNAVAILABLE, repr(bad)
        assert got["why_unavailable"] == sc.WRONG_CADENCE, repr(bad)

    ok = sc.detect("x.mp4", reader=_Reader([[]] * 10))
    assert ok["state"] == sc.NOT_DETECTED
    assert ok["every_s"] == sc.EVERY_S == ok["measured_at_every_s"]
