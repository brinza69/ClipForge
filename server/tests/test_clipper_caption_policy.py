"""Who decides whether an export burns a caption layer of its own.

THE DEFECT THIS EXISTS TO STOP RECREATING. 37 of 101 stored clips are rejected
for carrying two caption tracks, and on 31 August a human confirmed it on 4 of 4
watched: under ClipForge's layer, the source's own burned subtitles are still
visible. Re-rendering those projects with the layer on would produce the same 37
defects again, out of a corpus that now knows better.

AND THE DETECTOR STILL DOES NOT DECIDE IT. `source_captions` answered correctly —
its `present` on `pilotf81b` is what the human saw — but its thresholds were
chosen with the answer visible on four sources and it says so: `calibrated:
false`. A detector that has never been calibrated must not silently remove
somebody's captions, because that failure is invisible: the clip ships with no
text at all and nothing reports it.
"""

from __future__ import annotations

from services.clipper import caption_policy as cp
from services.clipper import publish_corpus as pcorp
from services.clipper import source_captions as scap


def _detector(state=scap.PRESENT, calibrated=False) -> dict:
    return {"state": state, "calibrated": calibrated}


# --- a person's answer is applied --------------------------------------------


def test_a_person_saying_the_source_has_captions_suppresses_the_layer():
    got = cp.decide(True)
    assert got["action"] == cp.SUPPRESS and got["decided_by"] == cp.HUMAN


def test_a_person_saying_it_does_not_keeps_the_layer():
    got = cp.decide(False)
    assert got["action"] == cp.BURN and got["decided_by"] == cp.HUMAN


# --- the detector's is not ----------------------------------------------------


def test_the_detector_alone_never_suppresses_anything():
    """It is right about `pilotf81b` and it is still `calibrated: false`. The
    cost of being wrong in this direction is a clip with no text at all."""
    got = cp.decide(None, _detector())
    assert got["action"] == cp.BURN
    assert got["decided_by"] == cp.DEFAULT
    assert got["detector"] == scap.PRESENT, "recorded, and applied to nothing"
    assert got["detector_calibrated"] is False


def test_a_person_overrides_the_detector_in_both_directions():
    assert cp.decide(True, _detector(scap.ABSENT))["action"] == cp.SUPPRESS
    assert cp.decide(False, _detector(scap.PRESENT))["action"] == cp.BURN


def test_nobody_having_said_is_not_somebody_saying_no():
    """`None` is the absence of an answer and `False` is an answer, which is why
    the setting is three-valued rather than a checkbox."""
    silent = cp.decide(None)
    said_no = cp.decide(False)
    assert silent["action"] == said_no["action"] == cp.BURN
    assert silent["decided_by"] == cp.DEFAULT
    assert said_no["decided_by"] == cp.HUMAN
    assert silent["why"] != said_no["why"]


def test_a_setting_that_is_not_a_verdict_is_not_a_verdict():
    """A form posting the string "false" would otherwise be truthy and suppress
    the captions of a project whose source has none."""
    for bad in ("true", "false", 1, 0, "", [], {}):
        got = cp.decide(bad)
        assert got["decided_by"] == cp.DEFAULT, repr(bad)
        assert got["action"] == cp.BURN, repr(bad)


def test_an_unreadable_detector_verdict_changes_nothing():
    for bad in (None, {}, {"state": "maybe"}, 7, "present"):
        got = cp.decide(None, bad)
        assert got["action"] == cp.BURN and got["detector"] is None, repr(bad)


# --- and it is what finally lets `own_layer` say False -----------------------


def test_a_suppressed_render_is_the_declaration_a_missing_ass_was_not():
    """A missing `.ass` cannot distinguish "we added none" from "the file was
    cleaned up", which is why `_own_caption_layer` stopped answering False at
    all. A recorded decision is a fact about the encode."""
    assert pcorp._own_caption_layer(None, {}) is None
    assert pcorp._own_caption_layer(None, {"caption_policy": cp.decide(True)}) is False


def test_a_recorded_burn_is_not_a_yes_either():
    """It says a layer was ASKED for. Whether libass drew one is the `.ass`
    question — and the R6 defect was an event drawn into a transparent bar."""
    burned = {"caption_policy": cp.decide(False)}
    assert pcorp._own_caption_layer(None, burned) is None
    assert pcorp._own_caption_layer({"caption_y_source": "ass"}, burned) is True


def test_the_duplicate_check_clears_when_our_layer_was_suppressed():
    """The DUPLICATE half clears — a source that carries captions and an export
    that added none is ONE layer, not two, and the reject goes away.

    The CHECK does not pass, and that is deliberate. Suppressing our layer
    leaves the source's own subtitle as the only text on screen; whether it
    survives the crop is a different question with no measurement behind it.
    A pass here would trade a duplicate for an unverified absence."""
    from services.clipper import publish_captions as pcap
    from services.clipper import publish_preflight as pf

    placed = {"placement_refused": None, "worst_share_complete": True,
              "on_face": False}
    got = pcap.captions(placed, _readable(), {"state": scap.PRESENT},
                        own_layer=False)
    assert got["state"] == pf.UNAVAILABLE
    assert pcap.TWO_LAYERS not in got["why"], "the duplicate finding is gone"


def _readable():
    from services.clipper import caption_contrast as cc

    return cc.verdict({"text_color": "#FFFFFF", "outline_color": "#000000",
                       "outline_width": 5, "highlight_color": "#FFFFFF"})
