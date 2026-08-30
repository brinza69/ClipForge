"""Batch R6, second half: what the placement report REFUSES to say.

Split from `test_clipper_caption_placement.py` at the 500-line limit, on the
seam the batch is about. That file checks what the report says when it can say
something; this one checks the three ways it must not:

- a FLOOR presented as a coverage — `share` unions the signals that were
  measured, so a shot with no face track and text at 0.4 was reporting 0.4;
- a CLOSED LIST that nothing checks, which is a comment — `composition == "fit"`
  let everything else fall through to the `crop` branch;
- a SHADOW SIGNAL that raises, which breaks the only contract it has.

The fixtures are duplicated from the other file rather than shared through a
helper module. Four three-line builders is the cheaper trade, and a `conftest`
that two files import is a third place to look when a fixture surprises you.
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
