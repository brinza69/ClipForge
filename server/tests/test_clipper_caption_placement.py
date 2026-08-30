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

TWO THINGS THE FIRST VERSION OF THIS FILE GOT WRONG, both found by review and
both the same shape — a test that passes on a contract production does not have:

- It handed ONE list of boxes to every shot, which looks per-shot and is not.
  The evidence is per shot now, in output pixels, mapped by the caller.
- It read a `frame` key off `fit` shots to find the letterbox. No shot has ever
  carried one — 161 `fit` shots, all inside the 58 sidecars of the pilot corpus,
  27 of which contain at least one, and zero with `frame` or `fit_rect` — so the
  branch was dead against real data and green against a fixture that invented
  the key. It comes from `dynamic_geometry.canvas_size`.
"""

from __future__ import annotations

from services.clipper import caption_placement as cp

OUT_H = 1920


def _rect(y: int, h: int) -> dict:
    return {"x": 0, "y": y, "w": 1080, "h": h}


def _shot(index: int, composition: str = "crop", **kw) -> dict:
    return {"index": index, "composition": composition, **kw}


def _seen(faces=(), panels=(), text=()) -> dict:
    """One shot's evidence, in OUTPUT pixels, as the caller would map it."""
    return {"faces": list(faces), "panels": list(panels), "text": list(text)}


#: A 16:9 source, so a `fit` shot really has a letterbox. `canvas_size` puts the
#: frame between 0.342 and 0.658 of the output height.
SRC = {"src_w": 1920, "src_h": 1080}


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
                             evidence=[_seen(faces=[_rect(900, 200)])], **SRC)
    assert cp.ON_FACE in view["lands_on"]
    assert view["worst"]["index"] == 0
    assert view["worst"]["share"] > 0


def test_the_three_signals_are_reported_apart():
    view = cp.placement_view(
        y_pct=0.5, shots=[_shot(0)], out_h=OUT_H, **SRC,
        evidence=[_seen(faces=[_rect(900, 200)], panels=[_rect(0, 100)],
                        text=[_rect(940, 40)])])
    ev = view["worst"]["evidence"]
    assert ev[cp.ON_FACE] > 0 and ev[cp.ON_SOURCE_TEXT] > 0
    assert ev[cp.ON_UI] == 0.0, "the panel is at the top and misses the band"
    assert cp.ON_UI not in view["lands_on"]


def test_a_caption_clear_of_everything_has_no_conflicts():
    view = cp.placement_view(y_pct=0.5, shots=[_shot(0)], out_h=OUT_H,
                             evidence=[_seen(faces=[_rect(0, 200)])], **SRC)
    assert view["lands_on"] == []
    assert view["worst"]["share"] == 0.0


# --- the error this file exists to prevent -----------------------------------


def test_the_shots_are_never_averaged():
    """A face crop makes the face fill the output; a game shot puts the same
    face in a small inset. One number across both is a number about neither —
    which is exactly how "66% on game UI" was once reported for captions that
    sat on a hoodie."""
    view = cp.placement_view(
        y_pct=0.5, out_h=OUT_H, shots=[_shot(0), _shot(1)], **SRC,
        # DIFFERENT evidence per shot, because that is what the mapping
        # produces: a face filling a crop and the same face as a small inset.
        evidence=[_seen(faces=[_rect(0, 1920)]), _seen(faces=[_rect(0, 90)])])
    assert len(view["shots"]) == 2
    assert "average" not in view and "mean" not in view
    assert view["worst"]["index"] in (0, 1)


def test_every_shot_carries_its_own_composition():
    """Because the answer means something different in each, and a reader who
    cannot see which is which is back to the averaged number."""
    view = cp.placement_view(y_pct=0.5, shots=[_shot(0, "crop"),
                                               _shot(1, "fit")],
                             out_h=OUT_H, evidence=[_seen(), _seen()], **SRC)
    assert [s["composition"] for s in view["shots"]] == ["crop", "fit"]


# --- the letterbox -----------------------------------------------------------


def test_a_caption_below_a_fit_frame_sits_on_the_blurred_band():
    """The one place a caption covers nothing of the source. Reported rather
    than approved: whether the text stays readable against a blurred copy of
    the frame is a contrast question nothing here measures."""
    view = cp.placement_view(y_pct=0.9, out_h=OUT_H,
                             shots=[_shot(0, "fit")],
                             evidence=[_seen()], **SRC)
    assert cp.ON_LETTERBOX in view["lands_on"]
    assert view["source_band"] == [0.3417, 0.6583], "from `canvas_size`"


def test_a_caption_inside_a_fit_frame_is_not_on_the_band():
    view = cp.placement_view(y_pct=0.5, out_h=OUT_H,
                             shots=[_shot(0, "fit")],
                             evidence=[_seen()], **SRC)
    assert cp.ON_LETTERBOX not in view["lands_on"]


def test_a_crop_shot_has_no_letterbox_to_sit_on():
    view = cp.placement_view(y_pct=0.95, shots=[_shot(0, "crop")],
                             out_h=OUT_H, evidence=[_seen()], **SRC)
    assert cp.ON_LETTERBOX not in view["lands_on"]


def test_a_source_already_upright_has_no_letterbox_at_all():
    """`canvas_size` returns no offset for a 9:16 source, so there is no band —
    and a caption anywhere on it covers the source."""
    assert cp.source_band(1080, 1920) == (None, True), "measured, and none"
    view = cp.placement_view(y_pct=0.9, out_h=OUT_H, shots=[_shot(0, "fit")],
                             evidence=[_seen()], src_w=1080, src_h=1920)
    assert view["source_band"] is None
    assert cp.ON_LETTERBOX not in view["lands_on"]


# --- what cannot be known ----------------------------------------------------


def test_a_missing_signal_is_named_and_never_becomes_clear():
    """An absent face track is not a clip with no faces in it. Reporting it as
    clear would be the whole plan's oldest mistake, in a new place."""
    view = cp.placement_view(
        y_pct=0.5, shots=[_shot(0)], out_h=OUT_H, **SRC,
        evidence=[{"faces": None, "panels": [], "text": []}])
    assert cp.NO_FACES in view["unavailable"]
    assert cp.ON_FACE not in view["worst"]["evidence"], "not measured, not 0.0"
    assert cp.ON_FACE not in view["lands_on"]


def test_an_empty_list_is_a_measurement_and_none_is_not():
    """`[]` is "we looked and found none"; None is "nobody looked"."""
    looked = cp.placement_view(y_pct=0.5, shots=[_shot(0)], out_h=OUT_H,
                               evidence=[_seen()], **SRC)
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


def test_an_unreadable_rectangle_makes_the_signal_unavailable():
    """It used to flow into `overlaps`, which answers 0.0 for a rectangle it
    cannot read — so a malformed box read as "covers nothing" and a shot full of
    them read as clear. An absence of measurement presented as a measurement of
    absence, in a new place."""
    for bad in ({"y": 900}, {"y": 900, "h": 0}, {"y": 900, "h": -5},
                {"y": float("nan"), "h": 10}, {"y": -50, "h": 10}, "not a rect"):
        view = cp.placement_view(
            y_pct=0.5, shots=[_shot(0)], out_h=OUT_H, **SRC,
            evidence=[{"faces": [_rect(900, 200), bad], "panels": [],
                       "text": []}])
        assert cp.NO_FACES in view["unavailable"], repr(bad)
        assert cp.ON_FACE not in view["shots"][0]["evidence"], repr(bad)
        assert cp.ON_FACE not in view["lands_on"], repr(bad)


def test_the_caption_height_is_the_one_the_geometry_burns():
    """0.12 here was a second definition of a number the geometry already had
    at 0.10. A report about a different box is a report about a caption nobody
    burns."""
    from services.clipper.captions import CAPTION_BOX_H_PCT

    assert cp.CAPTION_BAND_PCT is CAPTION_BOX_H_PCT


def test_a_caption_height_that_is_not_a_fraction_is_refused():
    """It used to reach `band_for`, which clamps — so a NaN, a string or a 7.0
    came back as a band somewhere plausible and every overlap below it was
    measured against a caption nobody could place."""
    for bad in (float("nan"), float("inf"), -0.5, 7.0, "0.5", True):
        view = cp.placement_view(y_pct=bad, shots=[_shot(0)], out_h=OUT_H,
                                 evidence=[_seen()], **SRC)
        assert view["worst"] is None, repr(bad)
        # REFUSED, and NOT also absent. Reporting `no_caption_plan` for a record
        # that has one, badly, is a different and wrong sentence.
        assert view["refused"] == [cp.BAD_CAPTION_Y], repr(bad)
        assert cp.NO_CAPTION not in view["unavailable"], repr(bad)

    absent = cp.placement_view(y_pct=None, shots=[_shot(0)], out_h=OUT_H,
                               evidence=[_seen()], **SRC)
    assert absent["refused"] == [] and cp.NO_CAPTION in absent["unavailable"]


def test_an_evidence_entry_that_is_not_a_record_says_so():
    """`(entry or {}).get(key)` turned a string or a list into "every signal
    missing" without saying that the entry itself was wrong."""
    for bad in ("faces", ["faces"], 3):
        view = cp.placement_view(y_pct=0.5, shots=[_shot(0)], out_h=OUT_H,
                                 evidence=[bad], **SRC)
        assert view["refused"] == [cp.BAD_EVIDENCE], repr(bad)
        assert view["shots"][0]["refused"] == [cp.BAD_EVIDENCE], repr(bad)
        # A corrupt record is not a record with no measurements in it: it says
        # nothing about faces, and it is not the worst case at 0.0 either.
        assert view["shots"][0]["evidence"] == {}, repr(bad)
        assert view["shots"][0]["unavailable"] == [], repr(bad)
        assert view["shots"][0]["share"] is None, repr(bad)
        assert view["worst"] is None, repr(bad)


def test_a_shot_that_is_not_a_shot_is_refused_out_loud():
    """It used to vanish from the list with no refusal — an entry removed from
    the corpus without saying so."""
    view = cp.placement_view(y_pct=0.5, out_h=OUT_H, **SRC,
                             shots=[7, _shot(1)],
                             evidence=[_seen(), _seen(faces=[_rect(900, 200)])])
    assert view["refused"] == [cp.BAD_SHOT]
    assert [s["refused"] for s in view["shots"]] == [[cp.BAD_SHOT], []]
    assert view["worst"]["index"] == 1, "the refused shot competes for nothing"


def test_the_coverage_is_the_union_and_not_the_biggest_box():
    """The counter-example, executed: shot 0 has a face over 60.4% of the
    caption; shot 1 has a face over 39.6% and text over a DIFFERENT 39.6%, so
    79.2% of the caption is unreadable. Taking the largest single box and then
    the largest single signal called shot 0 the worse one."""
    # The band is 0.45..0.55 of 1920 — rows 864..1056, 192px tall.
    view = cp.placement_view(
        y_pct=0.5, out_h=OUT_H, **SRC, shots=[_shot(0), _shot(1)],
        evidence=[_seen(faces=[_rect(864, 116)]),
                  _seen(faces=[_rect(864, 76)], text=[_rect(980, 76)])])
    assert view["shots"][0]["share"] == 0.604
    assert view["shots"][1]["share"] == 0.792
    assert view["worst"]["index"] == 1
    # Each signal on its own is still reported, and is still the smaller number.
    assert view["shots"][1]["evidence"][cp.ON_FACE] == 0.396
    assert view["shots"][1]["evidence"][cp.ON_SOURCE_TEXT] == 0.396


def test_two_boxes_of_one_signal_also_union_rather_than_max():
    """The same mistake one level down: two faces over different halves of the
    caption is a caption with a face over both halves."""
    view = cp.placement_view(
        y_pct=0.5, out_h=OUT_H, **SRC, shots=[_shot(0)],
        evidence=[_seen(faces=[_rect(864, 76), _rect(980, 76)])])
    assert view["shots"][0]["evidence"][cp.ON_FACE] == 0.792


def test_overlapping_boxes_are_not_counted_twice():
    """A union that added spans would report 120% of a caption covered."""
    view = cp.placement_view(
        y_pct=0.5, out_h=OUT_H, **SRC, shots=[_shot(0)],
        evidence=[_seen(faces=[_rect(864, 192)], text=[_rect(864, 192)])])
    assert view["shots"][0]["share"] == 1.0


def test_the_letterbox_is_not_an_occlusion():
    """A caption on the black bars is perfectly readable. `share` answers "how
    much of the caption can nobody read", and the old `max(evidence.values())`
    let "sits low on the padding" outrank it."""
    view = cp.placement_view(y_pct=0.9, out_h=OUT_H, **SRC,
                             shots=[_shot(0, "fit")], evidence=[_seen()])
    assert cp.ON_LETTERBOX in view["lands_on"]
    assert view["shots"][0]["evidence"][cp.ON_LETTERBOX] > 0
    assert view["shots"][0]["share"] == 0.0


def test_missing_geometry_is_not_an_upright_source():
    """`None` had two meanings: "measured, and there is no letterbox" and
    "nobody supplied the dimensions". Collapsed, a missing width read as an
    upright source and a `fit` shot came back with zero conflicts and zero
    unavailable."""
    assert cp.source_band(0, 0) == (None, False)
    view = cp.placement_view(y_pct=0.9, out_h=OUT_H, shots=[_shot(0, "fit")],
                             evidence=[_seen()], src_w=0, src_h=0)
    assert view["source_band_known"] is False
    assert cp.NO_GEOMETRY in view["unavailable"]
    assert cp.ON_LETTERBOX not in view["lands_on"]

    upright = cp.placement_view(y_pct=0.9, out_h=OUT_H,
                                shots=[_shot(0, "fit")], evidence=[_seen()],
                                src_w=1080, src_h=1920)
    assert upright["source_band_known"] is True
    assert cp.NO_GEOMETRY not in upright["unavailable"]


def test_the_evidence_stays_with_its_own_shot(monkeypatch):
    """Filtering the non-dicts out and then enumerating the survivors shifted
    every later shot onto somebody else's evidence — one bad entry and the whole
    report is about the wrong frames, silently and precisely."""
    view = cp.placement_view(
        y_pct=0.5, out_h=OUT_H, **SRC,
        shots=["not a shot", _shot(1), _shot(2)],
        # Aligned with the ORIGINAL list: index 0 is the junk entry, so the
        # face belongs to shot 1 and shot 2 sees nothing.
        evidence=[_seen(), _seen(faces=[_rect(900, 200)]), _seen()])
    assert [s["index"] for s in view["shots"]] == [0, 1, 2]
    assert view["shots"][0]["refused"] == [cp.BAD_SHOT], "the junk entry"
    assert view["shots"][1]["evidence"][cp.ON_FACE] > 0, "shot 1 has the face"
    assert view["shots"][2]["evidence"][cp.ON_FACE] == 0.0, "shot 2 does not"


def test_no_evidence_at_all_is_its_own_absence():
    """Distinct from a shot whose entry is missing one signal: this is nobody
    having mapped anything into the output frame."""
    view = cp.placement_view(y_pct=0.5, shots=[_shot(0)], out_h=OUT_H, **SRC)
    assert cp.NO_EVIDENCE in view["unavailable"]
    assert view["shots"][0]["evidence"] == {}
    assert view["lands_on"] == []


def test_a_shot_the_evidence_list_does_not_reach_is_unavailable():
    """A shorter list is not padded with empties. The shots it does not cover
    were not measured, and an unmeasured shot is not a clear one."""
    view = cp.placement_view(y_pct=0.5, shots=[_shot(0), _shot(1)],
                             out_h=OUT_H, evidence=[_seen()], **SRC)
    assert view["shots"][0]["unavailable"] == []
    assert set(view["shots"][1]["unavailable"]) == {cp.NO_FACES, cp.NO_PANELS,
                                                    cp.NO_TEXT}


# --- the report --------------------------------------------------------------


def test_the_report_states_the_rule_it_used():
    view = cp.placement_view(y_pct=0.5, shots=[_shot(0)], out_h=OUT_H,
                             evidence=[_seen()], **SRC)
    assert view["schema"] == "caption_placement_v1"
    assert view["scope"] == "what_the_burned_caption_covers_per_shot"
    assert view["band_pct"] == cp.CAPTION_BAND_PCT
    assert view["applied"] is False


def test_every_conflict_and_absence_is_in_a_closed_list():
    views = [
        cp.placement_view(
            y_pct=0.5, shots=[_shot(0)], out_h=OUT_H, **SRC,
            evidence=[_seen(faces=[_rect(900, 200)], panels=[_rect(900, 200)],
                            text=[_rect(900, 200)])]),
        cp.placement_view(y_pct=0.9, out_h=OUT_H, shots=[_shot(0, "fit")],
                          evidence=[_seen()], **SRC),
        cp.placement_view(y_pct=None, shots=None),
    ]
    for view in views:
        assert set(view["lands_on"]) <= set(cp.LANDS_ON)
        assert set(view["unavailable"]) <= set(cp.UNAVAILABLE)
