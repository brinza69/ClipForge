"""Batch R6, second half: what the burned caption actually lands on.

Recorded, applied to nothing. What is pinned here is the arithmetic and the
refusals — the delivered caption stays where `_caption_y` put it.

THE MISTAKE THIS FILE EXISTS TO PREVENT is already in the repo, in
`panels_to_keep_out`: mapping a game-UI panel through a face crop is arithmetic
with no referent, the scale factor is 5-8x, and it shipped a report claiming
"66% of the caption sits on detected game UI" for a clip whose captions sit on
the streamer's hoodie. A vision model disagreed and the frames settled it.

The same error, told the other way round, would be one number for "the caption
is on the face" across a multi-shot edit: a face crop makes the face fill the
output while a game shot puts the same face in a small inset. So the report is
per shot, it names the worst one, and it does not offer an average.
"""

from __future__ import annotations

from services.clipper import caption_placement as cp

OUT_H = 1920


def _rect(y: int, h: int) -> dict:
    return {"x": 0, "y": y, "w": 1080, "h": h}


def _shot(index: int, composition: str = "crop", **kw) -> dict:
    return {"index": index, "composition": composition, **kw}


# --- the arithmetic ----------------------------------------------------------


def test_the_band_is_a_strip_around_the_caption_centre():
    top, bottom = cp.band_for(0.5, 0.10)
    assert (round(top, 3), round(bottom, 3)) == (0.45, 0.55)


def test_the_band_is_clamped_to_the_frame():
    assert cp.band_for(0.0)[0] == 0.0
    assert cp.band_for(1.0)[1] == 1.0


def test_overlap_is_a_share_of_the_caption_not_of_the_rectangle():
    """"The caption is 60% covered" and "the panel covers 3% of its own area"
    are different sentences, and only the first is about whether anybody can
    read it."""
    band = (0.40, 0.60)
    # A rectangle spanning most of the frame covers the caption completely.
    assert cp.overlaps(band, (0.0, 1.0)) == 1.0
    # And one covering half the band is a half, whatever its own size.
    assert cp.overlaps(band, (0.50, 0.90)) == 0.5


def test_a_rectangle_that_misses_the_band_covers_nothing():
    assert cp.overlaps((0.40, 0.60), (0.70, 0.90)) == 0.0
    assert cp.overlaps((0.40, 0.60), None) == 0.0


# --- what the caption lands on ----------------------------------------------


def test_a_caption_over_a_face_is_reported():
    view = cp.placement_view(y_pct=0.5, shots=[_shot(0)], out_h=OUT_H,
                             faces=[_rect(900, 200)], panels=[], text=[])
    assert cp.ON_FACE in view["conflicts"]
    assert view["worst"]["index"] == 0
    assert view["worst"]["share"] > 0


def test_the_three_signals_are_reported_apart():
    view = cp.placement_view(y_pct=0.5, shots=[_shot(0)], out_h=OUT_H,
                             faces=[_rect(900, 200)], panels=[_rect(0, 100)],
                             text=[_rect(940, 40)])
    ev = view["worst"]["evidence"]
    assert ev[cp.ON_FACE] > 0 and ev[cp.ON_SOURCE_TEXT] > 0
    assert ev[cp.ON_UI] == 0.0, "the panel is at the top and misses the band"
    assert cp.ON_UI not in view["conflicts"]


def test_a_caption_clear_of_everything_has_no_conflicts():
    view = cp.placement_view(y_pct=0.5, shots=[_shot(0)], out_h=OUT_H,
                             faces=[_rect(0, 200)], panels=[], text=[])
    assert view["conflicts"] == []
    assert view["worst"]["share"] == 0.0


# --- the error this file exists to prevent -----------------------------------


def test_the_shots_are_never_averaged():
    """A face crop makes the face fill the output; a game shot puts the same
    face in a small inset. One number across both is a number about neither —
    which is exactly how "66% on game UI" was once reported for captions that
    sat on a hoodie."""
    view = cp.placement_view(
        y_pct=0.5, out_h=OUT_H,
        shots=[_shot(0), _shot(1)],
        faces=[_rect(0, 1920)], panels=[], text=[])
    assert len(view["shots"]) == 2
    assert "average" not in view and "mean" not in view
    assert view["worst"]["index"] in (0, 1)


def test_every_shot_carries_its_own_composition():
    """Because the answer means something different in each, and a reader who
    cannot see which is which is back to the averaged number."""
    view = cp.placement_view(y_pct=0.5, shots=[_shot(0, "crop"),
                                               _shot(1, "fit")],
                             out_h=OUT_H, faces=[], panels=[], text=[])
    assert [s["composition"] for s in view["shots"]] == ["crop", "fit"]


# --- the letterbox -----------------------------------------------------------


def test_a_caption_below_a_fit_frame_sits_on_the_blurred_band():
    """The one place a caption covers nothing of the source. Reported rather
    than approved: whether the text stays readable against a blurred copy of
    the frame is a contrast question nothing here measures."""
    view = cp.placement_view(
        y_pct=0.9, out_h=OUT_H,
        shots=[_shot(0, "fit", frame=_rect(400, 1080))],
        faces=[], panels=[], text=[])
    assert cp.ON_LETTERBOX in view["conflicts"]


def test_a_caption_inside_a_fit_frame_is_not_on_the_band():
    view = cp.placement_view(
        y_pct=0.5, out_h=OUT_H,
        shots=[_shot(0, "fit", frame=_rect(400, 1080))],
        faces=[], panels=[], text=[])
    assert cp.ON_LETTERBOX not in view["conflicts"]


def test_a_crop_shot_has_no_letterbox_to_sit_on():
    view = cp.placement_view(y_pct=0.95, shots=[_shot(0, "crop")],
                             out_h=OUT_H, faces=[], panels=[], text=[])
    assert cp.ON_LETTERBOX not in view["conflicts"]


# --- what cannot be known ----------------------------------------------------


def test_a_missing_signal_is_named_and_never_becomes_clear():
    """An absent face track is not a clip with no faces in it. Reporting it as
    clear would be the whole plan's oldest mistake, in a new place."""
    view = cp.placement_view(y_pct=0.5, shots=[_shot(0)], out_h=OUT_H,
                             faces=None, panels=[], text=[])
    assert cp.NO_FACES in view["unavailable"]
    assert cp.ON_FACE not in view["worst"]["evidence"], "not measured, not 0.0"
    assert cp.ON_FACE not in view["conflicts"]


def test_an_empty_list_is_a_measurement_and_none_is_not():
    """`[]` is "we looked and found none"; None is "nobody looked"."""
    looked = cp.placement_view(y_pct=0.5, shots=[_shot(0)], out_h=OUT_H,
                               faces=[], panels=[], text=[])
    assert looked["unavailable"] == []
    assert looked["worst"]["evidence"][cp.ON_FACE] == 0.0


def test_no_caption_plan_is_unavailable_not_conflict_free():
    view = cp.placement_view(y_pct=None, shots=[_shot(0)], out_h=OUT_H)
    assert cp.NO_CAPTION in view["unavailable"]
    assert view["shots"] == [] and view["worst"] is None


def test_no_shot_list_is_unavailable_too():
    view = cp.placement_view(y_pct=0.5, shots=[], out_h=OUT_H)
    assert cp.NO_SHOTS in view["unavailable"]
    assert view["worst"] is None


# --- the report --------------------------------------------------------------


def test_the_report_states_the_rule_it_used():
    view = cp.placement_view(y_pct=0.5, shots=[_shot(0)], out_h=OUT_H,
                             faces=[], panels=[], text=[])
    assert view["schema"] == "caption_placement_v1"
    assert view["scope"] == "what_the_burned_caption_covers_per_shot"
    assert view["band_pct"] == cp.CAPTION_BAND_PCT
    assert view["applied"] is False


def test_every_conflict_and_absence_is_in_a_closed_list():
    views = [
        cp.placement_view(y_pct=0.5, shots=[_shot(0)], out_h=OUT_H,
                          faces=[_rect(900, 200)], panels=[_rect(900, 200)],
                          text=[_rect(900, 200)]),
        cp.placement_view(y_pct=0.9, out_h=OUT_H,
                          shots=[_shot(0, "fit", frame=_rect(400, 1080))]),
        cp.placement_view(y_pct=None, shots=None),
    ]
    for view in views:
        assert set(view["conflicts"]) <= set(cp.CONFLICTS)
        assert set(view["unavailable"]) <= set(cp.UNAVAILABLE)
