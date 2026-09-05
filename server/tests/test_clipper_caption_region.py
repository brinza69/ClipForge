"""The third framing: a region holding the subject and the observed subtitle.

WHY IT EXISTS. A 9:16 crop of go ghost keeps the speaker at 803 output pixels
and cuts the subtitle; the full letterbox keeps the subtitle whole and drops the
speaker to 254. This computes the rectangle between them — and the number that
matters is that it is built from what was LOCALLY observed, not from the
cumulative band, because on the moment that started this argument the two differ
by 806 px against 1216.
"""

from __future__ import annotations

from services.clipper import caption_region as cr

SRC_W, SRC_H = 2560, 1440
#: `6053a598cf06` at 51.4 s, from `source_caption_observation` on the proxy.
SEEN = [{"x0": 0.3583, "x1": 0.4333, "y0": 0.9259, "y1": 0.9556},
        {"x0": 0.4458, "x1": 0.5708, "y0": 0.9259, "y1": 0.9481},
        {"x0": 0.5729, "x1": 0.6729, "y0": 0.9148, "y1": 0.9593}]
#: The same clip's stored subject.
SUBJECT = {"face": {"cx": 1264.0, "cy": 570.67, "w": 602.67, "n": 221.0}}


# --- the measurement the whole option rests on -------------------------------


def test_the_local_region_keeps_the_speaker_the_size_the_crop_delivers():
    """THE FINDING, and the numbers are the point of the test.

    On this interval the lines span 806 source px, so the region that holds them
    and the face scales by 1.341 against the shipping crop's 1080/810 = 1.333 —
    within half a percent. The face lands at 808 output px against the crop's
    803, and the caption band at 85.9 against 85.5, with the WHOLE subtitle
    inside instead of 14% of it.

    The cumulative band would have argued for the full letterbox and its 254 px
    face: three times worse than the evidence supports. Comparing against the
    crop's own delivered sizes here, rather than against remembered figures, is
    what makes that a comparison rather than a claim.
    """
    got = cr.region_for(SEEN, SUBJECT, SRC_W, SRC_H)
    assert got["rect"] is not None and got["why"] is None
    assert 800 <= got["rect"]["w"] <= 815, got["rect"]

    band_h = (0.9593 - 0.9148) * SRC_H          # what was on screen, not the band
    sizes = cr.delivered_sizes(got["rect"], subject_w=602.67, band_h=band_h)
    crop = cr.delivered_sizes({"x": 0, "y": 0, "w": 810, "h": 1440},
                              subject_w=602.67, band_h=band_h)
    assert abs(sizes["subject_w_out_px"] - crop["subject_w_out_px"]) < 10, (
        sizes, crop)
    assert abs(sizes["band_h_out_px"] - crop["band_h_out_px"]) < 2, (sizes, crop)
    assert sizes["letterbox_bars_px"] > 0, "wider than 9:16, so bars top/bottom"
    assert crop["letterbox_bars_px"] == 0.0, "and 9:16 fills the frame"


def test_the_region_contains_both_the_text_and_the_subject():
    got = cr.region_for(SEEN, SUBJECT, SRC_W, SRC_H)
    r, t, s = got["rect"], got["text_rect"], got["subject_rect"]
    for part in (t, s):
        assert r["x"] <= part["x"] and r["y"] <= part["y"]
        assert r["x"] + r["w"] >= part["x"] + part["w"]
        assert r["y"] + r["h"] >= part["y"] + part["h"]


def test_a_region_narrower_than_9_16_is_scaled_by_its_height():
    """One scale per region is what letterboxing means, and the obvious version
    uses the width scale for both cases — which pillarboxes nothing and reports
    a subject larger than the output can hold."""
    tall = {"x": 0, "y": 0, "w": 400, "h": 1400}
    got = cr.delivered_sizes(tall, subject_w=200, band_h=70)
    assert got["scale"] == round(1920 / 1400, 4)
    assert got["pillarbox_bars_px"] > 0 and got["letterbox_bars_px"] == 0.0


# --- what it refuses ---------------------------------------------------------


def test_no_observed_text_gets_no_region_at_all():
    """NOT the subject's own box, and not the previous interval's rectangle. A
    region built to hold text nobody saw is a framing decision resting on an
    absence."""
    for boxes in (None, [], "x", 7, [{"x0": 0.1}]):
        got = cr.region_for(boxes, SUBJECT, SRC_W, SRC_H)
        assert got["rect"] is None, repr(boxes)
        assert got["why"] == cr.NO_TEXT, repr(boxes)


def test_no_subject_reports_the_text_rectangle_and_still_refuses_the_region():
    """The text half is a measurement and travels; the region is not one."""
    got = cr.region_for(SEEN, None, SRC_W, SRC_H)
    assert got["why"] == cr.NO_SUBJECT and got["rect"] is None
    assert got["text_rect"] is not None, "the half that WAS measured survives"


def test_boxes_outside_the_frame_are_refused_and_not_clamped():
    """A clamp turns a measurement that is wrong somewhere else into a plausible
    rectangle, and nothing downstream can tell."""
    got = cr.region_for([{"x0": 0.1, "x1": 1.4, "y0": 0.9, "y1": 0.95}],
                        SUBJECT, SRC_W, SRC_H)
    assert got["rect"] is None and got["why"] == cr.NO_TEXT


def test_no_source_geometry_is_not_a_region():
    for w, h in ((0, 1440), (2560, 0), (None, 1440), ("x", None)):
        got = cr.region_for(SEEN, SUBJECT, w, h)
        assert got["why"] == cr.NO_GEOMETRY, (w, h)


def test_an_unmeasured_size_stays_none_and_never_becomes_zero():
    """A region reported with a subject width of 0 reads as "the speaker
    vanished", which is a far stronger claim than "nobody measured him"."""
    got = cr.delivered_sizes({"x": 0, "y": 0, "w": 806, "h": 1111},
                             subject_w=None, band_h=None)
    assert got["scale"] is not None
    assert got["subject_w_out_px"] is None and got["band_h_out_px"] is None


def test_a_rectangle_that_is_not_one_is_refused():
    for bad in (None, {}, {"w": 0, "h": 10}, {"w": 10}, {"w": "a", "h": 1}, 7):
        got = cr.delivered_sizes(bad, subject_w=1, band_h=1)
        assert got["scale"] is None and got["why"] == cr.NO_GEOMETRY, repr(bad)


# --- the only non-circular test ----------------------------------------------


def test_checking_a_region_against_its_own_boxes_is_not_a_test():
    """THE CORRECTION. `region_for` is the UNION of the boxes it is given, so it
    contains them by construction — "58 of 58 shots hold the observed text" is
    geometric feasibility for the observations used and nothing about unseen
    frames. `built_from` says how many boxes are construction data so a reader
    can see which claim is on offer."""
    got = cr.region_for(SEEN, SUBJECT, SRC_W, SRC_H)
    assert got["built_from"] == len(SEEN)
    same = cr.verify_frozen(got["rect"], SEEN, SRC_W, SRC_H)
    assert same["clipped"] == 0, "of course; they built it"
    assert same["circular"] is False, "the flag is about the CALLER's choice"


def test_a_frozen_region_is_tested_on_boxes_it_never_saw():
    got = cr.region_for(SEEN, SUBJECT, SRC_W, SRC_H)
    later = [{"x0": 0.30, "x1": 0.72, "y0": 0.9148, "y1": 0.9593},
             {"x0": 0.40, "x1": 0.55, "y0": 0.9259, "y1": 0.9481}]
    out = cr.verify_frozen(got["rect"], later, SRC_W, SRC_H)
    assert out["held_out"] == 2
    assert out["held"] + out["clipped"] == 2
    assert out["worst_overflow_px"] is not None


def test_the_overflow_is_reported_because_three_clipped_is_not_one_finding():
    """"3 of 40 clipped" and "3 of 40 clipped by two pixels" are different
    findings, and only the second one is actionable."""
    frozen = {"x": 1000.0, "y": 1300.0, "w": 500.0, "h": 100.0}
    out = cr.verify_frozen(frozen, [{"x0": 0.3, "x1": 0.62,
                                     "y0": 0.915, "y1": 0.955}],
                           SRC_W, SRC_H)
    assert out["clipped"] == 1 and out["worst_overflow_px"] > 200


def test_an_empty_held_out_set_is_not_a_pass():
    """The most tempting reading in the module: nothing was clipped, so the
    region held. Nothing was TESTED."""
    got = cr.region_for(SEEN, SUBJECT, SRC_W, SRC_H)
    for boxes in ([], None, "x", 7):
        out = cr.verify_frozen(got["rect"], boxes, SRC_W, SRC_H)
        assert out["clipped"] == 0 and out["held"] == 0, repr(boxes)
        assert out["why"] == cr.HELD_OUT_EMPTY, repr(boxes)
        assert out["circular"] is None, repr(boxes)


def test_an_unreadable_held_out_box_does_not_count_as_held():
    got = cr.region_for(SEEN, SUBJECT, SRC_W, SRC_H)
    out = cr.verify_frozen(got["rect"], [{"x0": 0.3, "x1": None,
                                          "y0": 0.9, "y1": 0.95}],
                           SRC_W, SRC_H)
    assert out["why"] == cr.HELD_OUT_EMPTY and out["held"] == 0


def test_the_subject_caveat_rides_with_every_region():
    """The face size is `dynamic_plan.subject.face` — a clip-wide average —
    projected through a scale. The text became local in this batch; the subject
    did not, and a caller may not promote the one into the other."""
    got = cr.region_for(SEEN, SUBJECT, SRC_W, SRC_H)
    assert got["subject_caveat"] == cr.SUBJECT_IS_A_CLIP_AVERAGE
