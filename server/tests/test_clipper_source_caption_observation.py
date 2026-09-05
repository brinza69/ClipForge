"""Subtitle-line boxes with the time they were seen — batch A's first half.

WHAT THESE TESTS ARE PROTECTING. Every question this record will be asked is of
the form "was there text on screen during this stretch, and where" — and the
three ways to get that wrong are all absences read as answers: a moment nobody
sampled read as a moment with no subtitle, a frame the detector failed on read
as a frame with no boxes, and a text box read as a subtitle without anybody
confirming it is dialogue. One test each, and they are the ones to keep.
"""

from __future__ import annotations

from services.clipper import source_caption_observation as sco


class _Reader:
    """CRAFT's shape: `detect()[0][0]` is a list of `[x0, x1, y0, y1]`."""

    def __init__(self, boxes, raises=False):
        self.boxes, self.raises = boxes, raises

    def detect(self, _frame, **_kw):
        if self.raises:
            raise RuntimeError("no")
        return [[self.boxes]]


def _annotated(**kw):
    got = {"video": "s.mp4", "image_w": 480, "image_h": 270,
           "provenance": sco.HUMAN,
           "samples": [{"t": 1.0, "boxes": [{"x0": 0.25, "x1": 0.72,
                                             "y0": 0.91, "y1": 0.97}]}]}
    got.update(kw)
    return sco.from_annotations(got.pop("video"), got.pop("samples"), **got)


# --- an absence is never an answer ------------------------------------------


def test_a_moment_nobody_sampled_is_unevidenced_and_not_text_free():
    """The distinction the whole record exists for. A shot with no sample in it
    has to come back as "nothing was looked at", never as "no text was seen"."""
    obs = _annotated()
    got = sco.coverage(obs, 10.0, 12.0)
    assert got["samples"] == 0 and got["unevidenced"] is True
    assert got["with_text"] == got["without_text"] == 0


def test_an_interval_whose_only_samples_refused_is_unevidenced_too():
    """Looked at and not seen is, for every decision downstream, the same as not
    looked at — so a refusal may not quietly become "no text"."""
    obs = _annotated(samples=[{"t": 1.0, "boxes": None}])
    got = sco.coverage(obs, 0.0, 2.0)
    assert got["samples"] == 1 and got["refused"] == 1
    assert got["without_text"] == 0 and got["unevidenced"] is True


def test_a_sample_that_saw_no_text_is_evidence():
    obs = _annotated(samples=[{"t": 1.0, "boxes": []}])
    got = sco.coverage(obs, 0.0, 2.0)
    assert got["without_text"] == 1 and got["unevidenced"] is False


def test_the_boxes_inside_the_interval_come_back_with_it():
    obs = _annotated(samples=[
        {"t": 1.0, "boxes": [{"x0": 0.25, "x1": 0.72, "y0": 0.91, "y1": 0.97}]},
        {"t": 9.0, "boxes": [{"x0": 0.1, "x1": 0.2, "y0": 0.1, "y1": 0.2}]}])
    got = sco.coverage(obs, 0.0, 2.0)
    assert got["with_text"] == 1 and len(got["boxes"]) == 1
    assert got["boxes"][0]["x1"] == 0.72, "and not the other interval's"


def test_an_interval_that_is_not_two_ordered_seconds_is_refused():
    obs = _annotated()
    for lo, hi in ((2.0, 1.0), (1.0, 1.0), (None, 2.0), ("a", 2.0)):
        got = sco.coverage(obs, lo, hi)
        assert got["unevidenced"] is True, (lo, hi)
        assert got["why"], (lo, hi)


def test_no_observation_at_all_is_unevidenced_and_not_empty():
    for bad in (None, {}, {"samples": None}, 7, "samples"):
        got = sco.coverage(bad, 0.0, 2.0)
        assert got["unevidenced"] is True, repr(bad)


# --- a text box is not a subtitle -------------------------------------------


def test_the_detector_never_confirms_the_boxes_are_dialogue():
    """`pilot6b38`'s watermark is in 13 of 14 frames — MORE persistent than go
    ghost's real caption track — and only its width separates them, on
    thresholds chosen with the answer visible. So the detector's `dialogue`
    stays None and a consumer has to go and get a confirmation."""
    got = sco.observe("x.mp4", [1.0], reader=_Reader([[10, 100, 200, 220]]))
    assert got["dialogue"] is None


def test_only_a_real_boolean_confirms_dialogue():
    """A form posting the string "true" would otherwise turn a watermark into a
    subtitle track and suppress somebody's captions over it."""
    for bad in ("true", "false", 1, 0, "", [], {}, None):
        assert _annotated(dialogue=bad)["dialogue"] is None, repr(bad)
    assert _annotated(dialogue=True)["dialogue"] is True
    assert _annotated(dialogue=False)["dialogue"] is False


def test_an_annotation_may_not_claim_to_be_the_detector():
    """The detector route is `observe`, and it is the only one that can be
    re-run. An annotation wearing that name is an unverifiable record under the
    name of the one thing that is verifiable."""
    got = _annotated(provenance=sco.DETECTOR)
    assert got["samples"] == [] and got["why"]
    assert _annotated(provenance=sco.AGENT)["provenance"] == sco.AGENT


# --- what the detector produces ---------------------------------------------


def test_every_line_survives_and_none_is_aggregated_into_a_band(monkeypatch):
    """`source_captions._bands` keeps ONE width per band per frame — the widest
    box — which is exactly why it cannot answer a per-interval question. Two
    lines of the same caption fall in the same tenth of the frame and would
    become one number there; here they stay two boxes with two extents."""
    got = _run(monkeypatch,
               _Reader([[120, 340, 232, 250], [100, 380, 252, 268]]), [1.0])
    boxes = got["samples"][0]["boxes"]
    assert len(boxes) == 2
    assert boxes[0]["x0"] == 0.25 and boxes[1]["x0"] == round(100 / 480, 4)
    assert boxes[0]["y0"] != boxes[1]["y0"], "and they keep their own rows"


def test_a_detector_that_raises_is_a_refusal_and_not_an_empty_frame(monkeypatch):
    got = _run(monkeypatch, _Reader([], raises=True), [1.0])
    assert got["read"] == 0 and got["refused"] == 1
    assert got["samples"][0]["boxes"] is None
    assert got["samples"][0]["refused"] == sco.DETECTOR_FAILED


def test_a_frame_with_no_text_is_an_empty_list_and_not_a_refusal(monkeypatch):
    got = _run(monkeypatch, _Reader([]), [1.0])
    assert got["read"] == 1 and got["refused"] == 0
    assert got["samples"][0]["boxes"] == []


def test_the_boxes_are_fractions_of_the_image_that_was_analysed(monkeypatch):
    """The detector runs on the PROXY. A box in its pixels means nothing without
    the frame it was measured in, and fractions travel to the source where
    pixels do not."""
    got = _run(monkeypatch, _Reader([[120, 360, 243, 270]]), [1.0])
    assert got["image_w"] == 480 and got["image_h"] == 270
    assert got["samples"][0]["boxes"] == [
        {"x0": 0.25, "x1": 0.75, "y0": 0.9, "y1": 1.0}]


def test_a_box_outside_the_frame_fails_the_whole_frame(monkeypatch):
    """A box running from 0 to a million is a perfectly finite number and would
    put a subtitle across the entire width. The frame is the unit: a partial
    list measures the model's failure and reads as a measurement of the frame."""
    got = _run(monkeypatch, _Reader([[0, 480, 0, 270], [0, 999999, 10, 20]]),
               [1.0])
    assert got["samples"][0]["boxes"] is None
    assert got["samples"][0]["refused"] == sco.DETECTOR_FAILED


def test_the_decoded_time_is_recorded_beside_the_requested_one(monkeypatch):
    """Seeking is approximate. Reporting the boxes at the time that was ASKED
    for states a precision the decoder did not deliver, and an interval decision
    made on a box placed a second from where it was seen is undetectable
    downstream."""
    got = _run(monkeypatch, _Reader([]), [51.15], decoded_ms=51400.0)
    assert got["samples"][0]["t_requested"] == 51.15
    assert got["samples"][0]["t_decoded"] == 51.4


def test_no_detector_is_not_an_observation_that_saw_nothing():
    got = sco.observe("x.mp4", [1.0], reader=None)
    assert got["samples"] == [] and got["read"] == 0
    assert got["why"] in (sco.NO_DETECTOR, sco.NO_VIDEO)


def test_times_that_are_not_finite_seconds_are_refused():
    for at in (None, [], "1.0", [1.0, "x"], [-1.0], [float("nan")], 7):
        got = sco.observe("x.mp4", at, reader=_Reader([]))
        assert got["samples"] == [], repr(at)
        assert got["why"] == sco.BAD_TIMES, repr(at)


# --- a fake capture, so the rule is testable without a file -----------------


def _run(monkeypatch, reader, times, decoded_ms: float | None = None):
    import sys
    import types

    class _Cap:
        CAP_PROP_POS_MSEC = 0

        def __init__(self, _path):
            self._want = 0.0

        def isOpened(self):
            return True

        def set(self, _prop, value):
            self._want = value

        def get(self, _prop):
            return self._want if decoded_ms is None else decoded_ms

        def read(self):
            class _F:
                shape = (270, 480, 3)

            return True, _F()

        def release(self):
            pass

    fake = types.ModuleType("cv2")
    fake.CAP_PROP_POS_MSEC = 0
    fake.VideoCapture = _Cap
    monkeypatch.setitem(sys.modules, "cv2", fake)
    return sco.observe("x.mp4", times, reader=reader)
