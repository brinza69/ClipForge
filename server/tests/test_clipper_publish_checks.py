"""Batch R7: the seven checks, read off one clip's artefacts.

Each of these guards the same distinction from a different side — that a check
which could not be made is not a check that passed. The sources differ and the
mistake is always the same shape.
"""

from __future__ import annotations

from services.clipper import publish_checks as pc
from services.clipper import publish_preflight as pf


def _sidecar(**over) -> dict:
    body = {
        "clip_id": "a", "duration": 30.0,
        "dynamic_plan": {"shots": [
            {"index": 0, "composition": "crop", "t0": 0.0, "t1": 15.0},
            {"index": 1, "composition": "crop", "t0": 15.0, "t1": 30.0}],
            "src_w": 1920, "src_h": 1080},
    }
    body.update(over)
    return body


# --- geometry ----------------------------------------------------------------


def test_a_present_and_impossible_duration_is_rejectable():
    """An ABSENT duration is age and a PRESENT unusable one is corruption —
    `edit_quality` already draws that line and this only carries the answer."""
    got = pc.geometry(_sidecar(duration=0))
    assert got["state"] == pf.FAIL and got["severity"] == pf.REJECTABLE
    assert "invalid_duration" in got["why"]


def test_an_absent_duration_is_unavailable_not_a_failure():
    body = _sidecar()
    body.pop("duration")
    got = pc.geometry(body)
    assert got["state"] == pf.UNAVAILABLE and got["why"] == "no_duration"


def test_a_sidecar_nobody_can_read_is_unavailable():
    for bad in (None, 7, "sidecar", []):
        assert pc.geometry(bad)["state"] == pf.UNAVAILABLE, repr(bad)
        assert pc.equivalence(bad)["state"] == pf.UNAVAILABLE, repr(bad)
        assert pc.provenance(bad)["state"] == pf.UNAVAILABLE, repr(bad)


# --- equivalence -------------------------------------------------------------


def test_a_cut_the_viewer_cannot_see_is_a_finding():
    """R1 found 116 of them across the pilots."""
    same = {"index": 0, "composition": "fit", "t0": 0.0, "t1": 10.0}
    body = _sidecar(dynamic_plan={
        "shots": [same, {**same, "index": 1, "t0": 10.0, "t1": 20.0}],
        "src_w": 1920, "src_h": 1080})
    got = pc.equivalence(body)
    assert got["state"] == pf.FAIL and got["severity"] == pf.REVISABLE


def test_an_unreadable_shot_list_is_not_zero_invisible_cuts():
    got = pc.equivalence(_sidecar(dynamic_plan={"shots": 7}))
    assert got["state"] == pf.UNAVAILABLE


# --- the ones that have no source at all -------------------------------------


def test_the_subject_check_says_what_is_missing_and_it_is_not_the_signal():
    """`dynamic_regimes` already reports `creator_unknown`. What does not exist
    is the list of which profiles REQUIRE a subject, and answering without it
    would invent the requirement in the same breath as checking it."""
    got = pc.subject(_sidecar())
    assert got["state"] == pf.UNAVAILABLE
    assert "require_a_subject" in got["why"]


def test_chrome_not_detected_is_not_a_pass():
    """`source_chrome` is a warning, never a verdict: its recall is low and
    undemonstrated, so an absence of hits is not a clean bill."""
    from services.clipper import source_chrome as sc

    got = pc.frame({"state": sc.NOT_DETECTED})
    assert got["state"] == pf.UNAVAILABLE
    assert "not_clean" in got["why"]


def test_chrome_detected_is_a_revisable_finding():
    from services.clipper import source_chrome as sc

    got = pc.frame({"state": sc.DETECTED, "frames_with_a_control": 9})
    assert got["state"] == pf.FAIL and got["severity"] == pf.REVISABLE


def test_no_chrome_detection_at_all_is_unavailable():
    for bad in (None, {}, {"state": "maybe"}, 7):
        assert pc.frame(bad)["state"] == pf.UNAVAILABLE, repr(bad)


# --- captions: three sources, one answer -------------------------------------


def _contrast(ok=True):
    from services.clipper import caption_contrast as cc

    return cc.verdict({"text_color": "#FFFFFF", "outline_color": "#000000",
                       "outline_width": 5,
                       "highlight_color": "#FFFFFF" if ok else "#FF3366"})


def test_a_source_that_already_has_captions_is_rejectable():
    """15 of 15 go ghost exports shipped with two caption systems. Nothing a
    bounded correction can move."""
    from services.clipper import source_captions as scap

    got = pc.captions(None, _contrast(), {"state": scap.PRESENT})
    assert got["state"] == pf.FAIL and got["severity"] == pf.REJECTABLE


def test_a_palette_that_cannot_be_read_is_a_revisable_finding():
    got = pc.captions({"refused": [], "worst_share_complete": True,
                       "lands_on": []}, _contrast(ok=False), None)
    assert got["state"] == pf.FAIL and got["severity"] == pf.REVISABLE
    assert "highlight_floor" in got["why"]


def test_a_placement_that_is_not_established_is_unavailable_not_clean():
    """A floor is not a coverage: naming the worst shot is a claim about all of
    them, and it is not established while any share is partial."""
    got = pc.captions({"refused": [], "worst_share_complete": False,
                       "lands_on": []}, _contrast(), None)
    assert got["state"] == pf.UNAVAILABLE
    assert "not_established" in got["why"]


def test_a_caption_on_a_face_is_a_revisable_finding():
    from services.clipper import caption_placement as cp

    got = pc.captions({"refused": [], "worst_share_complete": True,
                       "lands_on": [cp.ON_FACE], "worst": {"share": 1.0}},
                      _contrast(), None)
    assert got["state"] == pf.FAIL and got["severity"] == pf.REVISABLE


def test_every_caption_source_missing_is_unavailable():
    got = pc.captions(None, None, None)
    assert got["state"] == pf.UNAVAILABLE
    assert "contrast_unavailable" in got["why"]
    assert "placement_unavailable" in got["why"]


# --- boundary: the three-valued one ------------------------------------------


def test_an_undecidable_boundary_is_unavailable_and_that_is_why_it_is_three_valued():
    """`eligible` is True, False, and None for "the transcript could not say".
    None being unavailable rather than a pass is the whole reason for the third
    value: a board must not lose a moment for the way its transcript was made."""
    assert pc.boundary({"eligible": None, "why": "no_punctuation"})["state"] == pf.UNAVAILABLE
    assert pc.boundary({"eligible": False, "why": "end_mid_sentence"})["state"] == pf.FAIL
    assert pc.boundary({"eligible": True})["state"] == pf.PASS
    assert pc.boundary(None)["state"] == pf.UNAVAILABLE
    assert pc.boundary({"defects": []})["state"] == pf.UNAVAILABLE


# --- provenance ---------------------------------------------------------------


def test_a_stale_fingerprint_is_rejectable_and_a_missing_one_is_not():
    """Recomputed rather than copied: copying would let a plan edited after the
    render carry a stale digest and pass."""
    from services.clipper.render_input import input_fingerprint

    body = _sidecar()
    body["input_fingerprint"] = input_fingerprint(body)
    assert pc.provenance(body)["state"] == pf.PASS

    body["input_fingerprint"] = "0" * 16
    stale = pc.provenance(body)
    assert stale["state"] == pf.FAIL and stale["severity"] == pf.REJECTABLE

    body.pop("input_fingerprint")
    assert pc.provenance(body)["state"] == pf.UNAVAILABLE


# --- all seven together -------------------------------------------------------


def test_checks_for_answers_all_seven_and_nothing_else():
    got = pc.checks_for(_sidecar())
    assert set(got) == set(pf.CHECKS)
    assert pf.preflight(got)["refused"] == []


def test_a_clip_with_no_extra_artefacts_is_undecided_not_approved():
    """The measurement this batch exists for: with only a sidecar, five of the
    seven have no input and the verdict says so."""
    got = pf.preflight(pc.checks_for(_sidecar()))
    assert got["verdict"] == pf.UNDECIDED
    assert got["established"] == "2/7"
    assert set(got["unavailable"]) == {pf.SUBJECT, pf.FRAME, pf.CAPTIONS,
                                       pf.BOUNDARY, pf.PROVENANCE}
