"""A `fit` stretch too short to be worth its junction goes back to `crop`.

THE MEASUREMENT THIS EXISTS FOR. A human watched 20 pilot clips on 31 August and
timestamped what was broken; on the clip with exact times, all four composition
changes fell inside the four reported windows and nothing else did. Anchor jumps
of 960px, 844px and 1138px — the largest in that clip — went unreported, so it
is not "where the framing jumps", it is the `crop`<->`fit` junction.

The junction is a hard 3.16x change in apparent size, and that is an identity of
16:9 geometry rather than a tunable: `crop` blows a 607.5px window up to 1080,
`fit` squeezes 1920 down to it. Across the 58 exports: 84 junctions, median
3.58x, none below 3x.

WHY ONE DIRECTION ONLY. The interior runs split cleanly — `crop` islands are 18
with a median of 14.2s and a SHORTEST of 3.6s, `fit` islands are 39 with a
median of 5.3s and 15 below 4s. So a short `fit` island is the common case and
reads as the subject detector blinking; a short `crop` island barely exists.
"""

from __future__ import annotations

from services.clipper import dynamic_geometry as dg


def _shots(*spec) -> list[dict]:
    """`(composition, t0, t1)` triples into shots with a rect each."""
    return [{"index": i, "composition": c, "t0": t0, "t1": t1,
             "rect": {"x": 0, "y": 0, "w": 608, "h": 1080}}
            for i, (c, t0, t1) in enumerate(spec)]


def _comps(shots) -> list[str]:
    return [dg.composition_of(s) for s in shots]


def _keeps(_run) -> bool:
    """Somebody's evidence that the proposed crop preserves the content."""
    return True


def test_nothing_is_absorbed_without_evidence_that_the_crop_keeps_content():
    """THE RULE AS SHIPPED WAS WRONG, and a frame from the corpus showed it.

    It absorbed a brief `fit` run into `crop` on duration alone, reasoning that
    the subject was there before and after so it did not really leave. On
    `pilotee0e/aaf5f324e832`, 4.82-7.36s, the old `fit` export held both people
    and the new `crop` cut the man off at the frame edge and pushed the woman's
    head to the bottom.

    A short duration says the planner changed its mind quickly. That is a fact
    about the plan, not about the picture.
    """
    shots = _shots(("crop", 0.0, 10.0), ("fit", 10.0, 12.0), ("crop", 12.0, 20.0))
    assert _comps(dg.absorb_brief_fit_islands(shots)) == ["crop", "fit", "crop"]


def test_a_brief_fit_island_goes_back_to_crop_when_the_content_survives():
    got = dg.absorb_brief_fit_islands(
        _shots(("crop", 0.0, 10.0), ("fit", 10.0, 12.0), ("crop", 12.0, 20.0)),
        crop_keeps_content=_keeps)
    assert _comps(got) == ["crop", "crop", "crop"]
    assert got[1]["composition_absorbed"] == 2.0, "and it says why it is not fit"


def test_only_True_absorbs():
    """`None` is a check that could not answer and `0` is not a verdict."""
    for answer in (None, False, 0, "", "yes", []):
        got = dg.absorb_brief_fit_islands(
            _shots(("crop", 0.0, 10.0), ("fit", 10.0, 12.0), ("crop", 12.0, 20.0)),
            crop_keeps_content=lambda _r, a=answer: a)
        assert _comps(got)[1] == "fit", repr(answer)


def test_a_verifier_that_throws_leaves_the_run_alone():
    def boom(_run):
        raise RuntimeError("no evidence")

    got = dg.absorb_brief_fit_islands(
        _shots(("crop", 0.0, 10.0), ("fit", 10.0, 12.0), ("crop", 12.0, 20.0)),
        crop_keeps_content=boom)
    assert _comps(got)[1] == "fit"


def test_the_verifier_is_handed_the_whole_run():
    """The question is about the INTERVAL, not one shot of it — which is the
    same reason the island is measured as a run."""
    seen = []
    dg.absorb_brief_fit_islands(
        _shots(("crop", 0.0, 5.0), ("fit", 5.0, 6.0), ("fit", 6.0, 7.0),
               ("crop", 7.0, 15.0)),
        crop_keeps_content=lambda run: seen.append(len(run)) or True)
    assert seen == [2]


def test_an_earned_fit_run_is_left_alone():
    """A diagram or a screen share is why `fit` exists. On the clip a human
    timestamped, the runs are 8.8s, 13.8s, 15.8s, 14.6s and 7.5s — this rule
    removes none of them, and claiming otherwise would be the bigger lie."""
    got = dg.absorb_brief_fit_islands(
        _shots(("crop", 0.0, 8.82), ("fit", 8.82, 22.65), ("crop", 22.65, 38.4),
               ("fit", 38.4, 52.97), ("crop", 52.97, 60.48)),
        crop_keeps_content=_keeps)
    assert _comps(got) == ["crop", "fit", "crop", "fit", "crop"]


def test_a_run_of_several_short_fit_shots_is_absorbed_as_one_island():
    """The island is the RUN, not the shot. Three 1.5s `fit` shots in a row are
    4.5s of `fit` and stay — measuring each shot alone would absorb a run the
    rule means to keep."""
    got = dg.absorb_brief_fit_islands(
        _shots(("crop", 0.0, 5.0), ("fit", 5.0, 6.5), ("fit", 6.5, 8.0),
               ("fit", 8.0, 9.5), ("crop", 9.5, 15.0)),
        crop_keeps_content=_keeps)
    assert _comps(got) == ["crop", "fit", "fit", "fit", "crop"]


def test_a_leading_or_trailing_fit_run_is_not_an_island():
    """The claim is "the subject was there on both sides". An opening or an
    ending has no evidence on one side, so it is not this rule's to make."""
    lead = dg.absorb_brief_fit_islands(
        _shots(("fit", 0.0, 1.0), ("crop", 1.0, 10.0), ("crop", 10.0, 20.0)),
        crop_keeps_content=_keeps)
    assert _comps(lead)[0] == "fit"
    tail = dg.absorb_brief_fit_islands(
        _shots(("crop", 0.0, 10.0), ("crop", 10.0, 19.0), ("fit", 19.0, 20.0)),
        crop_keeps_content=_keeps)
    assert _comps(tail)[-1] == "fit"


def test_a_short_crop_island_is_never_absorbed_into_fit():
    """It would shrink a subject that was demonstrably present, and the corpus
    says the case barely exists: the shortest `crop` island is 3.6s."""
    got = dg.absorb_brief_fit_islands(
        _shots(("fit", 0.0, 10.0), ("crop", 10.0, 11.0), ("fit", 11.0, 20.0)),
        crop_keeps_content=_keeps)
    assert _comps(got) == ["fit", "crop", "fit"]


def test_the_threshold_is_exclusive_at_the_boundary():
    got = dg.absorb_brief_fit_islands(
        _shots(("crop", 0.0, 5.0), ("fit", 5.0, 5.0 + dg.MIN_FIT_DWELL_S),
               ("crop", 5.0 + dg.MIN_FIT_DWELL_S, 20.0)),
        crop_keeps_content=_keeps)
    assert _comps(got)[1] == "fit", "exactly the dwell is long enough"


def test_a_clock_nobody_can_read_is_not_a_short_island():
    """Absent is not zero. A shot whose times cannot be read would otherwise
    measure as a 0.0s island and be absorbed in silence."""
    for bad in (None, "soon", float("nan")):
        shots = _shots(("crop", 0.0, 5.0), ("fit", 5.0, 6.0), ("crop", 6.0, 20.0))
        shots[1]["t1"] = bad
        assert _comps(dg.absorb_brief_fit_islands(
            shots, crop_keeps_content=_keeps))[1] == "fit", repr(bad)


def test_a_list_too_short_to_have_an_interior_is_returned_unchanged():
    for spec in ((), (("fit", 0.0, 1.0),),
                 (("crop", 0.0, 1.0), ("fit", 1.0, 2.0))):
        shots = _shots(*spec)
        assert dg.absorb_brief_fit_islands(shots) is shots


def test_the_absorbed_shot_keeps_the_rect_it_will_now_be_cropped_to():
    """`rect` is computed for every shot and `composition` only decides whether
    the renderer uses it, so flipping the label needs no geometry."""
    got = dg.absorb_brief_fit_islands(
        _shots(("crop", 0.0, 10.0), ("fit", 10.0, 12.0), ("crop", 12.0, 20.0)),
        crop_keeps_content=_keeps)
    assert got[1]["rect"] == {"x": 0, "y": 0, "w": 608, "h": 1080}


# --- and the junctions the absorption cannot touch ---------------------------
#
# A human timestamped four of them on one clip and every one belongs to a `fit`
# run of 7.5s or more. What to do there has not been decided; this exists to be
# DEMONSTRATED on those windows and compared against the hard cut.


def _junction_plan() -> dict:
    return {"shots": [
        {"index": 0, "composition": "crop", "t0": 0.0, "t1": 5.0, "move": "hold",
         "rect": {"x": 0, "y": 0, "w": 608, "h": 1080}, "anchor": [304, 540]},
        {"index": 1, "composition": "fit", "t0": 5.0, "t1": 10.0, "move": "hold",
         "rect": {"x": 0, "y": 0, "w": 1920, "h": 1080}, "anchor": [960, 540]}],
        "style": {}}


def test_the_ease_is_off_for_every_caller_today():
    """Nothing has been decided about the long junctions, so the default has to
    deliver exactly what shipped."""
    plan = _junction_plan()
    assert dg.build_sendcmd(plan, 1920, 1080) == dg.build_sendcmd(
        plan, 1920, 1080, ease_s=0.0)
    assert dg.build_sendcmd(plan, 1920, 1080).count("crop w") == 2


def test_the_ease_sizes_against_the_canvas_and_not_the_source():
    """`_size` clamps to `src_h` because a crop window lives inside the frame —
    but the `fit` window is the PADDED canvas, 3412 against 1080 on a 16:9
    input. Sized against the source, every step came back clamped to 1080, the
    ramp collapsed to one repeated size, and the junction cut exactly as hard as
    before while the script looked longer."""
    script = dg.build_sendcmd(_junction_plan(), 1920, 1080, ease_s=0.3)
    heights = [int(line.split("crop h ")[1].rstrip(";").split(",")[0])
               for line in script.splitlines() if "crop h " in line]
    assert len(set(heights)) > 3, "the ramp has to actually ramp"
    assert max(heights) == 3412, "and reach the canvas"
    assert heights == sorted(heights), "monotonically, without a step back"


def test_the_ramp_ends_exactly_on_the_shots_own_size():
    """An eased junction that stopped short would leave the shot framed at
    something the planner never chose."""
    script = dg.build_sendcmd(_junction_plan(), 1920, 1080, ease_s=0.3)
    assert "5.300 crop w 1920, crop h 3412," in script


def test_the_ramp_moves_the_centre_as_well_as_the_size():
    """MEASURED FROM A FRAME, and the sendcmd script looked fine without it.

    `_position_exprs` returns a fixed `0, 0` for a `fit` shot, which is correct
    only when the window IS the whole canvas. Pinned there, a mid-ramp window
    sits in the canvas's top-left — transparent padding — so the composite
    showed the blurred background and nothing else. Every step carries its own
    centre now, interpolated from the shot it came from.
    """
    script = dg.build_sendcmd(_junction_plan(), 1920, 1080, ease_s=0.3)
    ramp = [l for l in script.splitlines() if l.startswith("5.") and "crop x" in l]
    assert len(ramp) > 3, "every ramp entry positions itself"
    centres = [float(l.split("crop x '")[1].split("-out_w")[0])
               for l in ramp if "-out_w" in l]
    assert centres == sorted(centres), "and travels one way"


def test_the_ramp_lands_on_the_shots_own_expression():
    """The last entry hands back to `_position_exprs`, so nothing downstream
    has to know a ramp happened."""
    script = dg.build_sendcmd(_junction_plan(), 1920, 1080, ease_s=0.3)
    assert "5.300 crop w 1920, crop h 3412, crop x '0', crop y '0';" in script


def test_an_ease_between_two_shots_of_the_same_composition_does_not_happen():
    plan = _junction_plan()
    plan["shots"][1]["composition"] = "crop"
    plan["shots"][1]["rect"] = {"x": 0, "y": 0, "w": 608, "h": 1080}
    assert dg.build_sendcmd(plan, 1920, 1080, ease_s=0.3) == dg.build_sendcmd(
        plan, 1920, 1080)
