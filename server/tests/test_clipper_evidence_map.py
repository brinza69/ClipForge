"""Batch R6: where a detected box lands in the output frame.

The chain is four transforms and every one of them is a place to be silently
wrong, so the tests are written against the RENDERER's arithmetic rather than
against the mapper's own. `dynamic_render` pads before it crops; `dynamic_edit`
scales the proxy on X and Y separately; `dynamic_geometry` refuses a shot whose
crop moves. Each of those is checked here as a property, not as a number.
"""

from __future__ import annotations

from services.clipper import evidence_map as em
from services.clipper.dynamic_geometry import canvas_size

SRC_W, SRC_H = 2560, 1440
PROXY_W, PROXY_H = 480, 270
OUT_W, OUT_H = 1080, 1920
SCALE = SRC_W / PROXY_W  # 5.333..., the real factor on the pilots


def _crop_shot(**kw) -> dict:
    shot = {"index": 0, "composition": "crop", "t0": 0.0, "t1": 3.0,
            "rect": {"x": 890, "y": 0, "w": 810, "h": 1440}}
    shot.update(kw)
    return shot


def _fit_shot(**kw) -> dict:
    shot = {"index": 0, "composition": "fit", "t0": 0.0, "t1": 3.0}
    shot.update(kw)
    return shot


def _map(box, shot=None, style=None, **kw):
    shot = shot or _crop_shot()
    crop = em.crop_window(shot, src_w=SRC_W, src_h=SRC_H, style=style)
    assert not isinstance(crop, str), crop
    return em.map_box(box, proxy_w=PROXY_W, proxy_h=PROXY_H, src_w=SRC_W,
                      src_h=SRC_H, crop=crop, out_w=OUT_W, out_h=OUT_H, **kw)


# --- the chain ---------------------------------------------------------------


def test_the_pad_comes_before_the_crop():
    """`dynamic_render` says so and the comment cost a review round: `scale`
    fixes its output size when the filter is configured, so letterboxing up
    front is what makes every crop 9:16. A mapper that crops before padding is
    describing a renderer this repo abandoned."""
    _cw, _ch, off_y = canvas_size(SRC_W, SRC_H)
    assert off_y > 0, "a 16:9 source really is padded"

    crop = em.crop_window(_crop_shot(), src_w=SRC_W, src_h=SRC_H)
    # The crop's canvas-space origin is the shot rect's source y PLUS the
    # offset, exactly as `build_dynamic_filtergraph` computes it.
    assert crop[1] == 0 + off_y


def test_x_and_y_are_scaled_separately():
    """Assuming one factor because the proxy "should" preserve the aspect is an
    assumption about an encoder."""
    square = [0, 0, 100, 100]
    wide = em.map_box(square, proxy_w=480, proxy_h=540, src_w=SRC_W,
                      src_h=SRC_H, crop=(0.0, 0.0, float(SRC_W),
                                         float(canvas_size(SRC_W, SRC_H)[1])),
                      out_w=OUT_W, out_h=OUT_H)
    # 480->2560 is 5.33x, 540->1440 is 2.67x, so a square box is not square.
    assert round(wide["w"] / wide["h"], 2) == 2.0


def test_the_crop_comes_from_the_anchor_and_not_from_the_rectangle():
    """A single-point size timeline proves CONSTANT SIZE, not identity with
    `shot["rect"]`. The delivered window is built by `build_sendcmd` from the
    timeline and the ANCHOR, and across the corpus 837 of 1,965 `crop` shots
    have a delivered window that differs from the planner's rectangle — 42.6%.

    So the crop's own top-left is the corner of the frame, and the rectangle's
    is not."""
    shot = _crop_shot(rect={"x": 890, "y": 0, "w": 810, "h": 1440},
                      anchor=[1295, 720])
    crop = em.crop_window(shot, src_w=SRC_W, src_h=SRC_H)
    assert not isinstance(crop, str)

    at_the_crop = [crop[0] / (SRC_W / PROXY_W), 0, 10, 10]
    _cw, _ch, off_y = canvas_size(SRC_W, SRC_H)
    at_the_crop[1] = (crop[1] - off_y) / (SRC_H / PROXY_H)
    got = _map(at_the_crop, shot)
    assert round(got["x"], 3) == 0.0 and round(got["y"], 3) == 0.0

    # ...and on the corpus's own shape they differ, in both axes and for two
    # different reasons. Taken verbatim from a stored shot on a 1920x1080
    # source: `_size` rounds a height of 792 to a width of 446 while the planner
    # stored 444, and `_anchor` CLAMPS the centre to 682 because 684 would put
    # the window's bottom past the frame.
    #     shot["rect"]   (144, 288, 444, 792)
    #     delivered      (143, 286, 446, 792)
    real = _crop_shot(rect={"x": 144, "y": 288, "w": 444, "h": 792},
                      anchor=[366, 684], shake=0.0)
    _cw2, _ch2, off2 = canvas_size(1920, 1080)
    delivered = em.crop_window(real, src_w=1920, src_h=1080)
    assert (round(delivered[2]), round(delivered[3])) == (446, 792)
    assert round(delivered[0]) == 143, "not the rectangle's 144"
    assert round(delivered[1] - off2) == 286, "not the rectangle's 288"


def test_a_full_crop_box_fills_the_output():
    shot = _crop_shot(rect={"x": 0, "y": 0, "w": SRC_W, "h": SRC_H})
    box = [0, 0, PROXY_W, PROXY_H]
    got = _map(box, shot)
    assert got["x"] == 0.0 and got["y"] == 0.0
    assert round(got["w"]) == OUT_W and round(got["h"]) == OUT_H


def test_a_fit_shot_maps_through_the_whole_canvas():
    """A `fit` shot crops the entire canvas, so the source sits in the middle
    with the letterbox above and below it."""
    _cw, canvas_h, off_y = canvas_size(SRC_W, SRC_H)
    got = _map([0, 0, PROXY_W, PROXY_H], _fit_shot())
    assert got["x"] == 0.0
    # The top of the source is `off_y` down the canvas, and the canvas maps to
    # the full output height.
    assert round(got["y"]) == round(off_y * OUT_H / canvas_h)


# --- what it refuses ---------------------------------------------------------


def test_a_moving_crop_is_refused_rather_than_approximated():
    """`shot["rect"]` is the delivered window only while the size timeline is a
    single point — and `move: push` is a LABEL, true only when the style's
    `push_amount` is above zero."""
    shot = _crop_shot(move="push")
    still = em.crop_window(shot, src_w=SRC_W, src_h=SRC_H,
                           style={"push_amount": 0.0})
    assert not isinstance(still, str), "the label alone does not move it"

    moving = em.crop_window(shot, src_w=SRC_W, src_h=SRC_H,
                            style={"push_amount": 0.1, "push_hz": 10.0})
    assert moving == em.MOVING_CROP


def test_missing_proxy_dimensions_are_refused_not_assumed_to_be_the_source():
    """`dynamic_edit` falls back to `or src_w`, which assumes a scale of 1 where
    the real one is 5.3. A face reported at a fifth of its size lands somewhere
    else entirely and reports a clean caption."""
    crop = em.crop_window(_crop_shot(), src_w=SRC_W, src_h=SRC_H)
    for bad in (0, None, -1, float("nan"), "480"):
        got = em.map_box([0, 0, 10, 10], proxy_w=bad, proxy_h=PROXY_H,
                         src_w=SRC_W, src_h=SRC_H, crop=crop)
        assert got == em.NO_PROXY_SIZE, repr(bad)


def test_a_box_that_is_not_four_finite_numbers_is_refused():
    for bad in ([0, 0, 10], [0, 0, 0, 10], [0, 0, 10, 0], "box", None,
                [0, 0, float("nan"), 10], {"x": 0, "y": 0}, [0, 0, 10, True]):
        assert _map(bad) == em.BAD_BOX, repr(bad)


def test_a_shot_with_no_rectangle_is_refused_for_that_reason():
    """The first version ran the size timeline first and caught every exception
    as `MOVING_CROP`, so a shot with no `t0` was reported as one whose crop
    moves — a specific claim about geometry, made about a record nobody could
    read."""
    assert em.crop_window({"composition": "crop", "t0": 0.0, "t1": 1.0},
                          src_w=SRC_W, src_h=SRC_H) == em.NO_CROP
    assert em.crop_window({"composition": "crop"}, src_w=SRC_W,
                          src_h=SRC_H) == em.NO_CROP, "the rect is missing too"


def test_a_shot_that_cannot_be_read_is_not_a_shot_whose_crop_moves():
    no_times = {"composition": "crop", "rect": {"x": 0, "y": 0,
                                                "w": 810, "h": 1440}}
    assert em.crop_window(no_times, src_w=SRC_W, src_h=SRC_H) == em.BAD_SHOT
    for bad in ("0.0", None, float("nan"), True):
        shot = {**no_times, "t0": bad, "t1": 3.0}
        assert em.crop_window(shot, src_w=SRC_W,
                              src_h=SRC_H) == em.BAD_SHOT, repr(bad)
    # A `fit` shot needs no rect but still needs times.
    assert em.crop_window({"composition": "fit"}, src_w=SRC_W,
                          src_h=SRC_H) == em.BAD_SHOT


# --- off the frame is not a zero ---------------------------------------------


def test_a_box_outside_the_crop_is_off_frame_and_not_an_overlap_of_zero():
    """It is not in the delivered picture at all, which is a different answer
    from "it is in the picture covering nothing" — the first is absence, the
    second a measurement, and `caption_placement` has an axis for each."""
    shot = _crop_shot(rect={"x": 0, "y": 0, "w": 400, "h": 1440})
    far_right = [460, 10, 10, 10]  # source x ~2453, well past the crop
    assert _map(far_right, shot) == em.OFF_FRAME


def test_a_box_straddling_the_crop_edge_is_clipped_not_dropped():
    shot = _crop_shot(rect={"x": 0, "y": 0, "w": 810, "h": 1440})
    straddling = [(810 / SCALE) - 5, 10, 20, 20]
    got = _map(straddling, shot)
    assert isinstance(got, dict)
    assert got["x"] + got["w"] <= OUT_W + 1e-6, "clipped to the frame"
    assert got["w"] > 0


# --- the shape the caption report expects ------------------------------------


def test_a_signal_nobody_offered_stays_none():
    """`None` is `unavailable` and an empty list is "we looked and found none".
    The two must not collapse."""
    got = em.shot_evidence(_crop_shot(), boxes_by_signal={"faces": None,
                                                          "panels": []},
                           proxy_w=PROXY_W, proxy_h=PROXY_H,
                           src_w=SRC_W, src_h=SRC_H)
    assert got["evidence"]["faces"] is None
    assert got["evidence"]["panels"] == []


def test_a_partly_unreadable_signal_becomes_unavailable_not_shorter():
    """Reporting the boxes that mapped would answer "the caption covers this
    much" out of a list that was partly unplaceable."""
    got = em.shot_evidence(
        _crop_shot(),
        boxes_by_signal={"faces": [[170, 10, 20, 20], "not a box"]},
        proxy_w=PROXY_W, proxy_h=PROXY_H, src_w=SRC_W, src_h=SRC_H)
    assert got["evidence"]["faces"] is None
    assert got["refused"] == [em.BAD_BOX]


def test_an_off_frame_box_does_not_make_the_signal_unavailable():
    """It was placed successfully; it simply is not in the picture."""
    shot = _crop_shot(rect={"x": 0, "y": 0, "w": 400, "h": 1440})
    got = em.shot_evidence(shot, boxes_by_signal={"faces": [[460, 10, 10, 10]]},
                           proxy_w=PROXY_W, proxy_h=PROXY_H,
                           src_w=SRC_W, src_h=SRC_H)
    assert got["evidence"]["faces"] == []
    assert got["off_frame"]["faces"] == 1
    assert got["refused"] == []


def test_a_refused_crop_makes_every_signal_unavailable():
    got = em.shot_evidence(
        _crop_shot(move="push"),
        boxes_by_signal={"faces": [[0, 0, 10, 10]], "panels": [[0, 0, 10, 10]]},
        proxy_w=PROXY_W, proxy_h=PROXY_H, src_w=SRC_W, src_h=SRC_H,
        style={"push_amount": 0.1, "push_hz": 10.0})
    assert got["evidence"] == {"faces": None, "panels": None}
    assert got["refused"] == [em.MOVING_CROP]


# --- it feeds the report it was built for ------------------------------------


def test_the_output_is_what_caption_placement_reads():
    """The two halves have to fit: `placement_view` takes `{"faces": [...]}` in
    output pixels, and a face across the caption band has to come back as a
    conflict."""
    from services.clipper import caption_placement as cp

    shot = _crop_shot(rect={"x": 890, "y": 0, "w": 810, "h": 1440})
    # A proxy box over the lower middle of the crop, where a bottom caption sits.
    _cw, _ch, off_y = canvas_size(SRC_W, SRC_H)
    source_y = 0.72 * 1440
    box = [890 / SCALE, source_y / (SRC_H / PROXY_H), 100, 40]
    got = em.shot_evidence(shot, boxes_by_signal={"faces": [box],
                                                  "panels": [], "text": []},
                           proxy_w=PROXY_W, proxy_h=PROXY_H,
                           src_w=SRC_W, src_h=SRC_H)
    view = cp.placement_view(y_pct=0.75, shots=[shot], out_h=OUT_H,
                             evidence=[got["evidence"]],
                             src_w=SRC_W, src_h=SRC_H)
    assert view["refused"] == []
    assert cp.ON_FACE in view["lands_on"]
    assert view["shots"][0]["share_complete"] is True
