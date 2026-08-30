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
    assert cp.ON_FACE in view["conflicts"]
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
    assert cp.ON_UI not in view["conflicts"]


def test_a_caption_clear_of_everything_has_no_conflicts():
    view = cp.placement_view(y_pct=0.5, shots=[_shot(0)], out_h=OUT_H,
                             evidence=[_seen(faces=[_rect(0, 200)])], **SRC)
    assert view["conflicts"] == []
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
    assert cp.ON_LETTERBOX in view["conflicts"]
    assert view["source_band"] == [0.3417, 0.6583], "from `canvas_size`"


def test_a_caption_inside_a_fit_frame_is_not_on_the_band():
    view = cp.placement_view(y_pct=0.5, out_h=OUT_H,
                             shots=[_shot(0, "fit")],
                             evidence=[_seen()], **SRC)
    assert cp.ON_LETTERBOX not in view["conflicts"]


def test_a_crop_shot_has_no_letterbox_to_sit_on():
    view = cp.placement_view(y_pct=0.95, shots=[_shot(0, "crop")],
                             out_h=OUT_H, evidence=[_seen()], **SRC)
    assert cp.ON_LETTERBOX not in view["conflicts"]


def test_a_source_already_upright_has_no_letterbox_at_all():
    """`canvas_size` returns no offset for a 9:16 source, so there is no band —
    and a caption anywhere on it covers the source."""
    assert cp.source_band(1080, 1920) == (None, True), "measured, and none"
    view = cp.placement_view(y_pct=0.9, out_h=OUT_H, shots=[_shot(0, "fit")],
                             evidence=[_seen()], src_w=1080, src_h=1920)
    assert view["source_band"] is None
    assert cp.ON_LETTERBOX not in view["conflicts"]


# --- what cannot be known ----------------------------------------------------


def test_a_missing_signal_is_named_and_never_becomes_clear():
    """An absent face track is not a clip with no faces in it. Reporting it as
    clear would be the whole plan's oldest mistake, in a new place."""
    view = cp.placement_view(
        y_pct=0.5, shots=[_shot(0)], out_h=OUT_H, **SRC,
        evidence=[{"faces": None, "panels": [], "text": []}])
    assert cp.NO_FACES in view["unavailable"]
    assert cp.ON_FACE not in view["worst"]["evidence"], "not measured, not 0.0"
    assert cp.ON_FACE not in view["conflicts"]


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
        assert cp.ON_FACE not in view["conflicts"], repr(bad)


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
    assert cp.ON_LETTERBOX in view["conflicts"]
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
    assert cp.ON_LETTERBOX not in view["conflicts"]

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
    assert view["conflicts"] == []


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
        assert set(view["conflicts"]) <= set(cp.CONFLICTS)
        assert set(view["unavailable"]) <= set(cp.UNAVAILABLE)


# --- an incomplete union is not an exact one ---------------------------------


def test_a_share_computed_over_some_of_the_signals_says_so():
    """`share` is a union over the signals that were MEASURED. A shot with faces
    unavailable and text at 0.4 reported 0.4, which reads as "40% of the caption
    is covered" when the honest answer is "at least 40%, and nobody looked at
    the rest"."""
    partial = cp.placement_view(
        y_pct=0.5, out_h=OUT_H, **SRC, shots=[_shot(0)],
        # No face track at all; text measured.
        evidence=[{"faces": None, "panels": [], "text": [_rect(864, 76)]}])
    shot = partial["shots"][0]
    assert shot["share"] > 0, "what was measured is still reported"
    assert shot["share_complete"] is False
    assert cp.NO_FACES in shot["unavailable"]
    assert partial["worst_share_complete"] is False

    whole = cp.placement_view(
        y_pct=0.5, out_h=OUT_H, **SRC, shots=[_shot(0)],
        evidence=[_seen(text=[_rect(864, 76)])])
    assert whole["shots"][0]["share"] == partial["shots"][0]["share"]
    assert whole["shots"][0]["share_complete"] is True
    assert whole["worst_share_complete"] is True


def test_the_worst_case_says_whether_it_is_a_measurement_or_a_floor():
    """A reader who sees a number and no qualifier beside it will treat it as
    the answer."""
    view = cp.placement_view(y_pct=0.5, out_h=OUT_H, **SRC, shots=[_shot(0)],
                             evidence=None)
    # Nothing was measured, so there is no union to report — `share` is None
    # and not 0.0, and a shot that measured nothing is nobody's worst case.
    assert view["shots"][0]["share"] is None
    assert view["worst"] is None
    assert view["worst_share_complete"] is None


# --- the closed list of compositions is enforced -----------------------------


def test_a_composition_nobody_defined_is_not_a_crop():
    """The code asked `composition == "fit"` and let everything else fall
    through to the `crop` branch, so a shot labelled `wobble` came back reported
    as a full-bleed crop with no letterbox and no complaint."""
    # Compared EXACTLY: `"fit "` and `"CROP"` are not `fit` and `crop`. A
    # closed list with a private spelling rule is two rules.
    for bad in ("wobble", "CROP", "fit ", " crop", "pan_and_scan"):
        view = cp.placement_view(y_pct=0.9, out_h=OUT_H, **SRC,
                                 shots=[{"index": 0, "composition": bad}],
                                 evidence=[_seen()])
        shot = view["shots"][0]
        assert shot["refused"] == [cp.BAD_COMPOSITION], repr(bad)
        assert shot["share"] is None and shot["evidence"] == {}, repr(bad)
        assert view["worst"] is None, repr(bad)
        assert cp.BAD_COMPOSITION in view["refused"], repr(bad)


def test_every_declared_composition_is_accepted():
    """A closed list that refuses its own members is worse than none."""
    for good in cp.COMPOSITIONS:
        view = cp.placement_view(y_pct=0.5, out_h=OUT_H, **SRC,
                                 shots=[{"index": 0, "composition": good}],
                                 evidence=[_seen()])
        assert view["shots"][0]["refused"] == [], good


def test_a_shot_that_does_not_say_how_it_is_composed_is_not_refused():
    """Nobody said is not somebody saying a wrong one. The face and text signals
    do not depend on the composition and stay measured; only the letterbox
    question loses its answer."""
    view = cp.placement_view(y_pct=0.5, out_h=OUT_H, **SRC,
                             shots=[{"index": 0}],
                             evidence=[_seen(faces=[_rect(864, 116)])])
    shot = view["shots"][0]
    assert shot["refused"] == []
    assert cp.NO_COMPOSITION in shot["unavailable"]
    assert shot["evidence"][cp.ON_FACE] > 0, "the face was still measured"
    assert cp.ON_LETTERBOX not in shot["conflicts"]


# --- the public entry point takes whatever it is given -----------------------


def test_the_containers_do_not_raise():
    """Every one of these raised: `enumerate(7)` on a shot list that is an int,
    `len(evidence)` on one, `int(src_w)` on a string, a division by an `out_h`
    of `"1920"`. This is a shadow signal whose entire contract is that its
    absence changes nothing."""
    cases = [
        (dict(shots=7), cp.BAD_SHOTS),
        (dict(shots="two shots"), cp.BAD_SHOTS),
        (dict(evidence=7), cp.BAD_EVIDENCE_LIST),
        (dict(evidence="faces"), cp.BAD_EVIDENCE_LIST),
        (dict(out_h="1920"), cp.BAD_OUT_H),
        (dict(out_h=0), cp.BAD_OUT_H),
        (dict(out_h=float("nan")), cp.BAD_OUT_H),
        (dict(src_w="1920"), cp.BAD_SOURCE_SIZE),
        (dict(src_h=float("inf")), cp.BAD_SOURCE_SIZE),
    ]
    for override, expected in cases:
        kwargs = {"y_pct": 0.5, "shots": [_shot(0)], "out_h": OUT_H,
                  "evidence": [_seen()], **SRC}
        kwargs.update(override)
        view = cp.placement_view(**kwargs)  # must not raise
        assert expected in view["refused"], (override, view["refused"])
        assert view["schema"] == "caption_placement_v1"


def test_a_rectangle_list_that_is_not_a_list_is_refused_not_ignored():
    """`for r in rects` raised on anything that is not a sequence. A signal that
    arrived as the wrong shape is refused, not quietly filed as missing."""
    for bad in (7, "a rect", {"y": 900, "h": 200}):
        view = cp.placement_view(y_pct=0.5, out_h=OUT_H, **SRC,
                                 shots=[_shot(0)],
                                 evidence=[{"faces": bad, "panels": [],
                                            "text": []}])
        shot = view["shots"][0]
        assert cp.BAD_RECTS in shot["refused"], repr(bad)
        assert cp.ON_FACE not in shot["evidence"], repr(bad)
        assert cp.NO_FACES not in shot["unavailable"], "refused, not missing"
        assert shot["share_complete"] is False, repr(bad)


def test_a_string_source_dimension_does_not_reach_canvas_size():
    """`src_w <= 0` on a string raised `TypeError` out of a function that
    already had a word for this case: its own `(None, False)`."""
    assert cp.source_band("1920", 1080) == (None, False)
    assert cp.source_band(1920, None) == (None, False)
    assert cp.source_band(float("nan"), 1080) == (None, False)


# --- the letterbox is not an occlusion, and not a demonstrated read ----------


def test_the_letterbox_is_reported_but_never_called_readable():
    """The renderer fills the strip with a BLURRED COPY of the frame rather than
    with black, so "it is only bars, the text is fine" is a claim about a
    renderer this repo stopped shipping. §R6 wants a contrast measurement first,
    and nothing here measures contrast."""
    view = cp.placement_view(y_pct=0.9, out_h=OUT_H, **SRC,
                             shots=[_shot(0, "fit")], evidence=[_seen()])
    assert cp.ON_LETTERBOX in view["conflicts"], "reported"
    assert view["shots"][0]["share"] == 0.0, "and not an occlusion"
    # Nothing anywhere in the payload claims the caption is legible there.
    assert "readable" not in repr(view) and "legible" not in repr(view)
