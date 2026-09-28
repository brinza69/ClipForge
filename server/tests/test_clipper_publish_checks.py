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


def test_a_duration_with_no_geometry_beside_it_does_not_pass():
    """The check is named for both halves, and `{"duration": 30}` used to pass
    it: no shot list means no defect can be raised against one, and the missing
    half read as a clean one. A duration is not a geometry."""
    got = pc.geometry({"duration": 30})
    assert got["state"] == pf.UNAVAILABLE
    assert got["why"] == "no_composition"
    assert got["evidence"]["duration_s"] == 30, "the half that was read survives"


def test_both_halves_present_is_the_only_pass():
    got = pc.geometry(_sidecar())
    assert got["state"] == pf.PASS
    assert got["evidence"]["composition"] == {"crop": 2}


def test_a_distribution_of_unknowns_is_not_a_known_composition():
    """`clip_report` returns the STRING `"unavailable"` only when the shot list
    itself is unreadable; when it can be read it returns a counter, and a
    counter of unknowns is `{"unavailable": 19}`. Comparing the whole field to
    the sentinel is false for every one of those — 31 of the 89 clips this
    check passed had every shot's composition unknown. A container is not the
    scalar it contains, which is the same mistake one level in."""
    body = _sidecar(dynamic_plan={"shots": [
        {"index": 0, "t0": 0.0, "t1": 15.0},
        {"index": 1, "t0": 15.0, "t1": 30.0}], "src_w": 1920, "src_h": 1080})
    got = pc.geometry(body)
    assert got["evidence"]["composition"] == {"unavailable": 2}
    assert got["state"] == pf.UNAVAILABLE
    assert "no_composition" in got["why"]


def test_a_partly_unknown_composition_is_not_established_either():
    body = _sidecar(dynamic_plan={"shots": [
        {"index": 0, "composition": "crop", "t0": 0.0, "t1": 15.0},
        {"index": 1, "t0": 15.0, "t1": 30.0}], "src_w": 1920, "src_h": 1080})
    assert pc.geometry(body)["state"] == pf.UNAVAILABLE


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


def test_zero_invisible_cuts_does_not_pass_a_check_that_also_names_rhythm():
    """Half of this check has no source — §4's own `UNMEASURED` list says
    `talking_head` wants a reframe "at a clear idea", and nothing measures one.
    Passing on the half that was read asserted the half that was not."""
    got = pc.equivalence(_sidecar())
    assert got["state"] == pf.UNAVAILABLE
    assert got["why"] == "profile_rhythm_is_not_evaluated"
    assert got["evidence"] == {"equivalent_cuts": 0}, "the count is still evidence"


# --- subject: the requirement comes from the delivered treatment -------------


def test_a_clip_that_keeps_the_whole_frame_needs_no_subject():
    """The requirement is not invented from a profile — it is read off what the
    render did. A `fit` shows the whole frame, so a diagram, a map or a wide
    shot is complete in it and no face is owed."""
    body = _sidecar(dynamic_plan={"shots": [
        {"index": 0, "composition": "fit", "t0": 0.0, "t1": 30.0}],
        "src_w": 1920, "src_h": 1080})
    got = pc.subject(body)
    assert got["state"] == pf.PASS and got["evidence"]["crop_shots"] == 0


def test_a_crop_needs_its_anchors_target_and_no_sidecar_carries_one():
    """A `crop` puts a 9:16 window somewhere because an anchor said to. If the
    anchor was not on the subject the clip is 1080 pixels of the wrong thing."""
    got = pc.subject(_sidecar())
    assert got["state"] == pf.UNAVAILABLE
    assert "target_basis" in got["why"] and got["evidence"]["crop_shots"] == 2


def _regime(target=1.0, covered=1, **over) -> dict:
    """A `regime_view` with per-segment target evidence over the whole clip."""
    return {"target_basis": "stable_anchor", "target_covered": True,
            "segments": [{"t0": 0.0, "t1": 30.0,
                          "evidence": {"target": target},
                          "evidence_coverage": {"target": covered}}],
            **over}


def test_a_crop_that_followed_an_unanchored_face_is_not_a_pass():
    """The Moist case: 14 of 14 exports follow *a* face, and it is the one in
    the browser — a human confirmed it on 31 August. Followed-a-face is not
    followed-the-subject."""
    body = _sidecar(regime_view={"target_basis": "unanchored_face"})
    assert pc.subject(body)["state"] == pf.UNAVAILABLE
    body["regime_view"] = _regime()
    assert pc.subject(body)["state"] == pf.PASS


def test_a_basis_is_a_method_and_presence_is_a_measurement():
    """`target_basis` says HOW the target was defined, never whether it was
    there — and reading it as the answer let a `stable_anchor` pass on sixteen
    samples with `evidence.target = 0.0`."""
    absent = _sidecar(regime_view=_regime(target=0.0, covered=16))
    got = pc.subject(absent)
    assert got["state"] != pf.PASS
    assert got["evidence"]["crop_shots_with_no_target"] == [0, 1]

    never = _sidecar(regime_view=_regime(target=0.0, covered=0))
    got = pc.subject(never)
    assert got["state"] == pf.UNAVAILABLE
    assert "nobody_sampled" in got["why"]


def test_a_lost_track_is_not_a_demonstrated_absence():
    """AND THE OTHER HALF OF THE SAME RULE, which this check got wrong in the
    opposite direction. It reported `fail` on 12 clips, and a frame refuted it:
    on `pilot2c8a/ec47597c60f2` at 0.70s the target is declared absent in 17 of
    17 crop shots while the creator fills the delivered frame.

    `evidence.target` starts from source detections and arrives here through the
    anchor filter and the hysteresis, so a zero says the TRACK lost him — a fact
    about the tracker. Twelve of those shots carry raw detections compatible
    with the anchor. There is no route to a subject failure today, and saying so
    is the honest state."""
    got = pc.subject(_sidecar(regime_view=_regime(target=0.0, covered=16)))
    assert got["state"] == pf.UNAVAILABLE
    assert "not_evidence_the_frame_is_empty" in got["why"]
    assert got["evidence"]["crop_shots_with_no_target"], "kept for the next reader"


def test_a_target_timeline_that_did_not_cover_the_clip_is_unavailable():
    body = _sidecar(regime_view=_regime(covered=4))
    body["regime_view"]["target_covered"] = False
    assert pc.subject(body)["state"] == pf.UNAVAILABLE


def test_evidence_from_a_segment_the_shot_does_not_touch_does_not_answer_for_it():
    """A segment ending exactly where the shot begins describes seconds the
    shot does not contain."""
    body = _sidecar(regime_view=_regime())
    body["regime_view"]["segments"] = [
        {"t0": 0.0, "t1": 15.0, "evidence": {"target": 1.0},
         "evidence_coverage": {"target": 8}}]
    got = pc.subject(body)
    assert got["state"] == pf.UNAVAILABLE
    assert got["evidence"]["crop_shots_never_sampled"] == [1]


def test_an_absent_composition_is_old_and_a_wrong_one_is_broken():
    """31 of the 101 stored clips have no `composition` on any shot: they were
    rendered before the key existed. Both answers are `unavailable`, and the
    reason is what somebody acts on — reporting the 31 as "outside the closed
    list" would have read as 31 corrupt records."""
    old = _sidecar(dynamic_plan={"shots": [{"index": 0, "t0": 0.0, "t1": 30.0}]})
    got = pc.subject(old)
    assert got["state"] == pf.UNAVAILABLE
    assert got["why"] == "shots_do_not_say_how_they_are_composed"

    broken = _sidecar(dynamic_plan={"shots": [{"index": 0, "t0": 0.0,
                                               "t1": 30.0,
                                               "composition": "wobble"}]})
    assert pc.subject(broken)["why"] == "composition_not_in_the_closed_list"


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


def _clean_source() -> dict:
    from services.clipper import source_captions as scap

    return {"state": scap.ABSENT, "calibrated": False}


def _placed(**over) -> dict:
    """A placement row in the shape `caption_corpus.measure` actually emits.

    The check used to read `lands_on`, `worst` and `refused`, which are
    `caption_placement.placement_view`'s names one layer down. `measure` sends
    `on_face`, `worst_share` and `placement_refused` — so `ON_FACE in
    placement["lands_on"]` was `in []` on all 101 clips and the face signal
    reached nothing.
    """
    return {"placement_refused": None, "worst_share_complete": True,
            "on_face": False, "worst_share": 0.0, **over}


def test_a_source_with_captions_and_an_export_with_its_own_is_rejectable():
    """15 of 15 go ghost exports shipped with two caption systems. Nothing a
    bounded correction can move — but it takes BOTH layers to be that."""
    from services.clipper import source_captions as scap

    got = pc.captions(_placed(), _contrast(), {"state": scap.PRESENT},
                      own_layer=True)
    assert got["state"] == pf.FAIL and got["severity"] == pf.REJECTABLE
    assert got["why"] == pc.TWO_LAYERS


def test_a_source_with_captions_alone_does_not_reject_the_export():
    """A verdict about the PROJECT's proxy is not a sentence about this export.
    Rejecting on it alone said "two caption tracks" out of one measurement."""
    from services.clipper import source_captions as scap

    got = pc.captions(_placed(), _contrast(), {"state": scap.PRESENT})
    assert got["state"] == pf.UNAVAILABLE
    assert "not_established" in got["why"]


def test_a_source_that_captions_a_clip_we_did_not_is_one_layer_not_two():
    """The DUPLICATE half clears — one layer is not two. The check as a whole
    does not, because suppressing ours leaves the SOURCE's subtitle as the only
    text on screen and nothing has measured whether it survives the crop."""
    from services.clipper import source_captions as scap

    got = pc.captions(_placed(), _contrast(), {"state": scap.PRESENT},
                      own_layer=False)
    assert got["state"] == pf.UNAVAILABLE
    assert pc.TWO_LAYERS not in got["why"], "the duplicate finding is gone"
    assert "source_subtitles_legibility" in got["why"]


def test_a_suppressed_layer_is_not_judged_by_the_ass_it_did_not_use():
    """Six failures on go ghost said `own_layer: false` and
    `the_caption_sits_on_a_face` in the same record. Both halves read the `.ass`
    beside the render, and after a `suppress` that file describes a layer nobody
    burned — the 15 pilotf81b exports still carry one from an earlier run, so
    the decision has to settle it rather than the file's absence."""
    got = pc.captions(_placed(on_face=True), _contrast(ok=False),
                      _clean_source(), own_layer=False)
    assert got["state"] == pf.UNAVAILABLE
    assert pc.ON_A_FACE not in got["why"]
    assert "highlight_floor" not in got["why"]
    assert got["evidence"]["suppressed_layer"] is True


def test_no_source_verdict_cannot_reach_a_pass_on_not_duplicated():
    """The inverse direction, and the worse one: with placement and contrast
    both clean and nobody having asked whether the source has captions, a check
    whose first word is "not duplicated" came back PASS."""
    for absent in (None, {}, {"state": "unknown"}):
        got = pc.captions(_placed(), _contrast(), absent)
        assert got["state"] == pf.UNAVAILABLE, repr(absent)
        assert "no_source_caption_verdict" in got["why"], repr(absent)


def test_a_palette_that_cannot_be_read_is_a_revisable_finding():
    got = pc.captions(_placed(), _contrast(ok=False), _clean_source())
    assert got["state"] == pf.FAIL and got["severity"] == pf.REVISABLE
    assert "highlight_floor" in got["why"]


def test_a_shortfall_a_human_accepted_is_not_found_again():
    """`Neon Pop`'s 2.33 was decided on 2026-08-31 and written into
    `KNOWN_SHORTFALLS`. The check re-derived it as a fresh defect because the
    acceptance was reachable only from the style, which it never sees."""
    from services.clipper import caption_contrast as cc

    neon = cc.verdict({"name": "Neon Pop", "text_color": "#FFFFFF",
                       "highlight_color": "#FF3366", "outline_color": "#1A0033",
                       "outline_width": 5})
    assert neon["accepted_shortfall"], "the verdict carries its own exception"
    got = pc.captions(_placed(), neon, _clean_source())
    assert got["state"] == pf.PASS
    assert got["evidence"]["accepted_shortfalls"]


def test_a_placement_that_is_not_established_is_unavailable_not_clean():
    """A floor is not a coverage: naming the worst shot is a claim about all of
    them, and it is not established while any share is partial."""
    got = pc.captions(_placed(worst_share_complete=False), _contrast(),
                      _clean_source())
    assert got["state"] == pf.UNAVAILABLE
    assert "not_established" in got["why"]


def test_a_caption_on_a_face_is_a_revisable_finding():
    got = pc.captions(_placed(on_face=True), _contrast(), _clean_source())
    assert got["state"] == pf.FAIL and got["severity"] == pf.REVISABLE
    assert got["why"] == pc.ON_A_FACE


def test_a_measured_defect_is_not_erased_by_a_signal_nobody_collected():
    """THE ORDER IS THE RULE. A face under the caption was measured; the UI and
    source-text signals have no per-shot detector and never will have one for
    this corpus. The strongest fact in the record was being silenced by the
    weakest, and the check came back `unavailable`."""
    got = pc.captions(_placed(on_face=True, worst_share_complete=False),
                      None, None)
    assert got["state"] == pf.FAIL
    assert got["why"] == pc.ON_A_FACE
    assert got["evidence"]["unestablished"], "and it still says what it lacked"


def test_a_contrast_defect_survives_an_incomplete_placement_too():
    got = pc.captions(_placed(worst_share_complete=False), _contrast(ok=False),
                      _clean_source())
    assert got["state"] == pf.FAIL and "highlight_floor" in got["why"]


def test_every_caption_source_missing_is_unavailable():
    got = pc.captions(None, None, None)
    assert got["state"] == pf.UNAVAILABLE
    assert "contrast_unavailable" in got["why"]
    assert "placement_unavailable" in got["why"]
    assert "no_source_caption_verdict" in got["why"]


# --- boundary: the three-valued one ------------------------------------------


def _view(defects=(), unknown=(), **over) -> dict:
    return {"defects": list(defects), "unknown": list(unknown), **over}


def test_the_boundary_check_reads_the_defects_not_the_eligibility():
    """`eligible` is a verdict about the MOMENT, and R5 lets it be True on the
    strength of a repair `boundary_view` only PROPOSED. Nothing applied that
    repair to the file on disk, so "this moment could be made complete" was
    being read as "this export's edges are good"."""
    got = pc.boundary(_view(defects=["end_inside_word"], eligible=True))
    assert got["state"] == pf.FAIL
    assert got["why"] == "end_inside_word"
    assert got["evidence"]["eligible_as_a_moment"] is True, "kept, not used"


def test_a_technical_defect_is_about_the_file_and_this_is_the_check_that_owns_it():
    """R5 left `clipped_release` out of `eligible` on purpose and said whether
    it may reach a board is R7's question. 22 of 58 pilot exports end within
    50ms of the last word."""
    got = pc.boundary(_view(defects=["clipped_release"], eligible=True))
    assert got["state"] == pf.FAIL and got["severity"] == pf.REVISABLE


def test_a_transcript_that_could_not_answer_is_unavailable_not_a_pass():
    got = pc.boundary(_view(unknown=["transcript_without_punctuation"]))
    assert got["state"] == pf.UNAVAILABLE
    assert "punctuation" in got["why"]
    assert pc.boundary(_view())["state"] == pf.PASS


def test_a_verdict_that_is_not_a_verdict_is_not_a_pass():
    """`0 is False` is False, so `if eligible is False` let an integer walk into
    the pass — the same shape as `x in (True, False, None)` one module over."""
    for bad in (None, 7, {"eligible": True}, {"defects": [], "unknown": 0},
                {"defects": "none", "unknown": []}):
        assert pc.boundary(bad)["state"] == pf.UNAVAILABLE, repr(bad)


# --- provenance ---------------------------------------------------------------


def test_a_stale_fingerprint_is_rejectable_and_a_missing_one_is_not():
    """Recomputed rather than copied: copying would let a plan edited after the
    render carry a stale digest and pass."""
    from services.clipper.render_input import input_fingerprint

    body = _sidecar()
    body["input_fingerprint"] = "0" * 16
    stale = pc.provenance(body)
    assert stale["state"] == pf.FAIL and stale["severity"] == pf.REJECTABLE

    body.pop("input_fingerprint")
    assert pc.provenance(body)["state"] == pf.UNAVAILABLE
    body["input_fingerprint"] = input_fingerprint(body)
    assert pc.provenance(body)["state"] != pf.FAIL


def test_a_valid_digest_over_an_empty_recipe_is_not_provenance():
    """`render_input` fills every absent key with `None`, so a sidecar carrying
    nothing but the digest of an empty recipe validates perfectly. The check
    was passing on the self-consistency of a projection with nothing in it."""
    from services.clipper.render_input import (FINGERPRINT_SCHEMA_V2,
                                               input_fingerprint)

    body = {"fingerprint_schema": FINGERPRINT_SCHEMA_V2}
    body["input_fingerprint"] = input_fingerprint(
        body, schema=FINGERPRINT_SCHEMA_V2)
    got = pc.provenance(body)
    assert got["state"] == pf.UNAVAILABLE
    assert "empty_recipe" in got["why"]
    assert got["evidence"]["recipe_keys_present"] == 0


def test_a_valid_digest_says_nothing_about_the_delivered_file():
    """The recipe never touches the mp4 — no size, no hash, no duration — so a
    match says the plan was not edited after the render and no more. There is
    no route to a provenance PASS today, and that is the finding."""
    from services.clipper.render_input import (FINGERPRINT_SCHEMA_V2,
                                               input_fingerprint)

    body = _sidecar()
    body["fingerprint_schema"] = FINGERPRINT_SCHEMA_V2
    body["input_fingerprint"] = input_fingerprint(
        body, schema=FINGERPRINT_SCHEMA_V2)
    got = pc.provenance(body)
    assert got["state"] == pf.UNAVAILABLE
    assert "delivered_file" in got["why"]
    assert got["evidence"]["fingerprint_status"] == "valid"


# --- all seven together -------------------------------------------------------


def test_checks_for_answers_all_seven_and_nothing_else():
    got = pc.checks_for(_sidecar())
    assert set(got) == set(pf.CHECKS)
    assert pf.preflight(got)["refused"] == []


def test_a_clip_with_no_extra_artefacts_is_undecided_not_approved():
    """The measurement this batch exists for: with only a sidecar, six of the
    seven have no input and the verdict says so.

    It was five, and the sixth is `cut_equivalence_and_profile_rhythm` — which
    used to pass on the equivalence half while asserting a rhythm nothing
    measures."""
    got = pf.preflight(pc.checks_for(_sidecar()))
    assert got["verdict"] == pf.UNDECIDED
    assert got["established"] == "1/7"
    assert set(got["unavailable"]) == {pf.EQUIVALENCE, pf.SUBJECT, pf.FRAME,
                                       pf.CAPTIONS, pf.BOUNDARY, pf.PROVENANCE}


# --- the container was validated and the contents were not -------------------
#
# Moving `boundary` from `eligible` to the defect lists closed one hole and
# moved another: a list is a list whatever is in it.


def test_a_defect_name_nothing_knows_is_refused_not_read_as_clean():
    for bad in ([7], ["end_inside_wrod"], ["", None]):
        got = pc.boundary({"defects": bad, "unknown": []})
        assert got["state"] == pf.UNAVAILABLE, repr(bad)
        assert "closed_list" in got["why"], repr(bad)


def test_an_unknown_name_nothing_knows_is_refused_too():
    got = pc.boundary({"defects": [], "unknown": ["because_reasons"]})
    assert got["state"] == pf.UNAVAILABLE and "closed_list" in got["why"]


def test_an_empty_contrast_record_is_not_a_readable_palette():
    """`{}` is a dict with no `refused` key, so it walked into the branch, found
    neither leg to object to, and left the palette half looking demonstrated."""
    got = pc.captions(_placed(), {}, _clean_source())
    assert got["state"] == pf.UNAVAILABLE
    assert "contrast_unavailable" in got["why"]


def test_a_contrast_record_missing_a_leg_is_not_readable_either():
    got = pc.captions(_placed(), {"fill": None, "highlight": None},
                      _clean_source())
    assert got["state"] == pf.UNAVAILABLE
