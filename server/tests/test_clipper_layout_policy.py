"""Whether the source has a SECOND region, and who says so.

THE DEFECT. `camera_rects` builds `game` and `game_tight` from geometry alone —
"everything to the right of the facecam, minus the chat strip" — and nothing
asks whether anything is there. On `pilotf81b`, one man talking to camera on a
street, the planner cuts to that rectangle and the rectangle is the building
behind him. Measured on the DELIVERED windows: 377 of 2,016 across the stored
corpus exclude the clip's own subject centre, 372 of them a `game` camera, and
re-planned today `30d7c6d4eae5` spends 6.8 s of 18.1 s there while `speech`
reads 0.75 to 0.83.

THE EXISTING GUARD CANNOT CATCH IT. `alive` is motion, detail and menus — it
rejects a black loading screen. A street with traffic moves, has detail and is
not a menu, so it passes all three and is still not a second subject.
"""

from __future__ import annotations

from services.clipper import layout_policy as lp


# --- a person's or an agent's answer is applied ------------------------------


def test_saying_the_source_is_one_camera_removes_the_second_region():
    got = lp.decide(False)
    assert got["regions"] == lp.ONE_REGION and got["decided_by"] == lp.HUMAN


def test_saying_it_has_two_keeps_todays_behaviour():
    got = lp.decide(True)
    assert got["regions"] == lp.TWO_REGIONS and got["decided_by"] == lp.HUMAN


def test_an_agent_that_looked_may_answer_it():
    """Accepted here and not in `caption_policy` because the evidence differs in
    kind: whether the frame is a composite is settled by a contact sheet, where
    caption suppression turns on legibility no still can show."""
    got = lp.decide(False, by=lp.AGENT)
    assert got["regions"] == lp.ONE_REGION and got["decided_by"] == lp.AGENT


def test_nobody_having_said_is_not_somebody_saying_no():
    silent = lp.decide(None)
    assert silent["regions"] == lp.TWO_REGIONS
    assert silent["decided_by"] == lp.DEFAULT
    assert silent["why"] != lp.decide(True)["why"]


def test_a_setting_that_is_not_a_verdict_is_not_a_verdict():
    """A form posting "false" is truthy and `0 == False` is True, so an integer
    would pass as an answer and mute the second camera on a real stream."""
    for bad in ("true", "false", 1, 0, "", [], {}, None):
        got = lp.decide(bad)
        assert got["decided_by"] == lp.DEFAULT, repr(bad)
        assert got["regions"] == lp.TWO_REGIONS, repr(bad)


def test_the_evidence_is_recorded_and_applied_to_nothing():
    """None of it is calibrated. Face-position spread is BACKWARDS — the
    single-camera pilot is the most stable of the four — and face width as a
    fraction of the frame is a threshold chosen on four sources with the answer
    visible, which this repo has already paid for once."""
    got = lp.decide(None, evidence={"off_subject_s": 6.8, "face_w_frac": 0.196})
    assert got["regions"] == lp.TWO_REGIONS, "measured, and applied to nothing"
    assert got["evidence"]["off_subject_s"] == 6.8


# --- the measurement behind it -----------------------------------------------


def _plan(*shots, cx=1264.0, cy=570.0):
    return {"src_w": 2560, "src_h": 1440, "style": {},
            "subject": {"face": {"cx": cx, "cy": cy, "w": 602.0}},
            "shots": list(shots)}


def _shot(index, x, t0, t1, **kw):
    got = {"index": index, "t0": t0, "t1": t1,
           "rect": {"x": x, "y": 0, "w": 810, "h": 1440},
           "anchor": [x + 405, 720], "composition": "crop",
           "move": "hold", "snap": False, "shake": 0.0}
    got.update(kw)
    return got


def test_the_seconds_are_measured_on_the_delivered_window():
    """Not on `shot["rect"]`: 837 of the corpus's 1,965 crop shots have a
    delivered window that differs from the planner's rectangle."""
    got = lp.off_subject_seconds(_plan(_shot(0, 858, 0.0, 2.0),
                                       _shot(1, 0, 2.0, 3.4, speech=1.0)))
    assert got["shots"] == 2 and got["off_shots"] == 1
    assert got["seconds"] == 1.4 and got["total_s"] == 3.4
    assert got["speaking_off_s"] == 1.4, "and he was talking through it"


def test_a_window_that_holds_the_subject_costs_nothing():
    got = lp.off_subject_seconds(_plan(_shot(0, 858, 0.0, 2.0)))
    assert got["seconds"] == 0.0 and got["off_shots"] == 0


def test_no_subject_track_is_not_zero_seconds():
    """A clip whose subject nobody located has an UNKNOWN amount of off-subject
    video, and 0 would read as clean."""
    plan = _plan(_shot(0, 0, 0.0, 2.0))
    plan["subject"] = {}
    got = lp.off_subject_seconds(plan)
    assert got["seconds"] is None and got["why"]


def test_no_geometry_is_not_zero_seconds():
    plan = _plan(_shot(0, 0, 0.0, 2.0))
    plan["src_w"] = 0
    assert lp.off_subject_seconds(plan)["seconds"] is None


def test_a_shot_whose_window_cannot_be_read_is_counted_apart():
    """Not folded into the seconds either way: a window nobody could compute is
    not a window that held the subject."""
    bad = _shot(1, 0, 2.0, 3.4)
    bad.pop("t0")
    got = lp.off_subject_seconds(_plan(_shot(0, 858, 0.0, 2.0), bad))
    assert got["refused"] == 1 and got["seconds"] == 0.0


def test_a_plan_that_is_not_one_is_refused():
    for bad in (None, 7, "plan", []):
        got = lp.off_subject_seconds(bad)
        assert got["seconds"] is None and got["why"], repr(bad)


# --- and what it does to the planner -----------------------------------------


def test_one_region_never_selects_the_second_camera():
    """Through the SAME path a dead gameplay region already takes, so the
    behaviour is the one that was already tested rather than a second one."""
    from services.clipper.dynamic_edit import _pick_camera
    from services.clipper.dynamic_cameras import _FACE_CAMS

    # Action, no speech: the case that reaches for the game family hardest.
    got = _pick_camera(False, True, alive=False, energy=0.9, previous=None,
                       run=0, max_run=2)
    assert got in _FACE_CAMS


def test_the_planner_takes_the_declaration_and_does_not_measure_it():
    """`one_region` is an argument, not a computation. A planner that decided
    this for itself would be applying an uncalibrated threshold to what the
    renderer frames."""
    import inspect

    from services.clipper import dynamic_edit

    src = inspect.getsource(dynamic_edit.plan_dynamic_edit)
    assert "one_region" in inspect.signature(
        dynamic_edit.plan_dynamic_edit).parameters
    assert "alive = (not one_region" in src


def test_the_render_path_declares_it_and_records_it():
    import inspect

    from workers import clipper_render_jobs, clipper_render_plan

    plan_src = inspect.getsource(clipper_render_plan._decide_render)
    assert "layout_policy.decide(" in plan_src
    assert "one_region=layout_decision" in plan_src
    # On the sidecar, because a plan that never chose the second camera and a
    # source that never had one produce the same shot list.
    assert '"layout_policy": decision["layout_policy"]' in inspect.getsource(
        clipper_render_jobs.handle_export)
