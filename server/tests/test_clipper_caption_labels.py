"""Which observed text is dialogue, and the three ways to get that wrong.

The detector finds TEXT. `pilot6b38`'s watermark is in 13 of 14 frames — more
persistent than go ghost's real caption track — and `source_captions` separates
them only by width, on thresholds chosen with the answer visible on four
sources. So the confirmation is a person's, and these tests are the shape it has
to take: per group, defaulting to `uncertain`, and never losing the boxes it
excluded.
"""

from __future__ import annotations

from services.clipper import caption_labels as cl

#: One frame with a subtitle (index 0) and a watermark (index 1).
OBS = {"samples": [
    {"t_requested": 1.0, "t_decoded": 1.0, "refused": None, "boxes": [
        {"x0": 0.30, "x1": 0.70, "y0": 0.91, "y1": 0.96},
        {"x0": 0.02, "x1": 0.10, "y0": 0.02, "y1": 0.06}]},
    {"t_requested": 2.0, "t_decoded": 2.0, "refused": None, "boxes": [
        {"x0": 0.35, "x1": 0.65, "y0": 0.91, "y1": 0.96}]},
    {"t_requested": 3.0, "t_decoded": 3.0, "refused": None, "boxes": []},
]}


def _label(at, boxes, what=cl.DIALOGUE, by=cl.HUMAN):
    return {"at": at, "boxes": boxes, "label": what, "by": by}


# --- per group, which is the whole design ------------------------------------


def test_one_frame_can_hold_a_subtitle_and_a_watermark_at_once():
    """A single `dialogue: true` for the frame validates the wrong box along
    with the right one, and every region built afterwards is sized to hold a
    logo."""
    got = cl.apply(OBS, [_label(1.0, [0]),
                         _label(1.0, [1], cl.NON_DIALOGUE)])
    marks = got["samples"][0]["marks"]
    assert marks[0]["label"] == cl.DIALOGUE
    assert marks[1]["label"] == cl.NON_DIALOGUE


def test_only_the_confirmed_boxes_can_build_a_region():
    got = cl.apply(OBS, [_label(1.0, [0]), _label(1.0, [1], cl.NON_DIALOGUE)])
    picked = cl.dialogue_boxes(OBS, got, 0.5, 1.5)
    assert picked["dialogue"] == 1 and picked["excluded"] == 1
    assert picked["boxes"] == [OBS["samples"][0]["boxes"][0]]


def test_the_excluded_and_unlabelled_counts_travel_with_the_boxes():
    """A region built from three boxes out of eleven is a different object from
    one built from eleven, and the caller has to see which it was handed."""
    got = cl.apply(OBS, [_label(1.0, [0])])
    picked = cl.dialogue_boxes(OBS, got, 0.5, 2.5)
    assert picked["dialogue"] == 1
    assert picked["unlabelled"] == 2, "the watermark and the second frame's line"


# --- the default is not either of the other two ------------------------------


def test_an_unlabelled_box_is_uncertain_and_never_dialogue():
    """Unconfirmed text may not size a framing."""
    got = cl.apply(OBS, [])
    assert got["counts"][cl.UNCERTAIN] == 3
    assert cl.dialogue_boxes(OBS, got, 0.0, 5.0)["boxes"] == []


def test_an_unlabelled_box_is_uncertain_and_never_non_dialogue():
    """The quieter error of the two: defaulting to `non_dialogue` would let
    unconfirmed text be CROPPED AWAY, and nothing would report it."""
    got = cl.apply(OBS, [])
    picked = cl.dialogue_boxes(OBS, got, 0.0, 5.0)
    assert picked["excluded"] == 0 and picked["unlabelled"] == 3


def test_a_label_that_is_not_a_verdict_marks_nothing():
    for bad in ({"at": 1.0, "label": "true", "by": cl.HUMAN},
                {"at": 1.0, "label": cl.DIALOGUE, "by": "detector"},
                {"at": 1.0, "label": cl.DIALOGUE},
                {"at": None, "label": cl.DIALOGUE, "by": cl.HUMAN},
                {"at": 1.0, "label": cl.DIALOGUE, "by": cl.HUMAN, "boxes": []},
                {"at": 1.0, "label": cl.DIALOGUE, "by": cl.HUMAN,
                 "boxes": [True]},
                "dialogue", 7, None):
        got = cl.apply(OBS, [bad])
        assert got["counts"][cl.DIALOGUE] == 0, repr(bad)
        assert got["unmatched"], repr(bad)


def test_the_detector_may_not_be_the_labeller():
    """`by` is `agent` or `human`. The detector's opinion is the thing being
    confirmed, and letting it sign its own confirmation closes the loop."""
    got = cl.apply(OBS, [_label(1.0, [0], by="detector")])
    assert got["counts"][cl.DIALOGUE] == 0 and got["unmatched"]


# --- a label that misses is not a label that was never written ---------------


def test_a_label_pointing_at_no_sample_is_reported_not_dropped():
    """A labelling pass whose targets silently missed is indistinguishable from
    one nobody ran."""
    got = cl.apply(OBS, [_label(9.0, [0])])
    assert got["counts"][cl.DIALOGUE] == 0
    assert got["unmatched"] and got["unmatched"][0]["why"] == "no_sample_at_this_time"


def test_a_label_pointing_past_the_last_box_is_reported():
    got = cl.apply(OBS, [_label(2.0, [0, 5])])
    assert got["counts"][cl.DIALOGUE] == 1
    assert any(u.get("why") == "box_index_out_of_range"
               for u in got["unmatched"])


def test_labels_match_the_decoded_time_and_not_the_requested_one():
    """`cv2` seeks approximately. A label written against the time somebody
    asked for would attach to a different frame."""
    obs = {"samples": [{"t_requested": 51.15, "t_decoded": 51.4,
                        "refused": None,
                        "boxes": [{"x0": 0.3, "x1": 0.7,
                                   "y0": 0.91, "y1": 0.96}]}]}
    assert cl.apply(obs, [_label(51.15, cl.ALL)])["counts"][cl.DIALOGUE] == 0
    assert cl.apply(obs, [_label(51.4, cl.ALL)])["counts"][cl.DIALOGUE] == 1


# --- the inventory keeps four rows apart -------------------------------------


def _shots():
    return [{"index": 0, "t0": 0.5, "t1": 1.5},     # the labelled frame
            {"index": 1, "t0": 1.5, "t1": 2.5},     # text, unlabelled
            {"index": 2, "t0": 2.5, "t1": 3.5},     # a frame with no text
            {"index": 3, "t0": 8.0, "t1": 9.0}]     # nobody looked


def test_four_rows_and_none_of_them_folded_together():
    got = cl.apply(OBS, [_label(1.0, [0]), _label(1.0, [1], cl.NON_DIALOGUE)])
    inv = cl.inventory(_shots(), OBS, got)
    assert inv["shots"] == 4
    assert inv["counts"] == {cl.CONFIRMED: 1, cl.OTHER_TEXT: 1,
                             cl.NO_TEXT: 1, cl.UNEVIDENCED: 1}


def test_text_nobody_labelled_is_not_a_shot_with_no_text():
    """The difference decides whether a framing may be sized to leave it out."""
    got = cl.apply(OBS, [])
    inv = cl.inventory(_shots(), OBS, got)
    rows = {r["shot"]: r["row"] for r in inv["rows"]}
    assert rows[0] == cl.OTHER_TEXT, "seen, none of it confirmed"
    assert rows[2] == cl.NO_TEXT and rows[3] == cl.UNEVIDENCED


def test_a_shot_nobody_sampled_is_unevidenced_and_not_empty():
    got = cl.apply(OBS, [_label(1.0, cl.ALL)])
    inv = cl.inventory(_shots(), OBS, got)
    rows = {r["shot"]: r for r in inv["rows"]}
    assert rows[3]["row"] == cl.UNEVIDENCED and rows[3]["samples"] == 0


def test_no_shots_is_a_refusal_and_not_an_empty_inventory():
    for bad in (None, [], "abc", 7, [1, 2]):
        inv = cl.inventory(bad, OBS, cl.apply(OBS, []))
        assert inv["shots"] == 0 and inv["why"], repr(bad)
