"""Batch R1: a cut exists only if the delivered picture changes.

The merge this replaced compared the planned RECTANGLE. Two `fit` shots have
different rects and deliver the identical full frame, so 116 of the 1.341 shots
in the four pilot exports were separated by a cut nobody could see. The rule now
comes from the renderer itself — the size timeline and the position expressions
`build_sendcmd` schedules — so a shot that moves, or that lands a different
frame, can never be absorbed.

Do not widen this into perceptual similarity here. R1 removes the cuts nobody
can defend; the ones somebody might need a measurement first.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from services.clipper import dynamic_geometry as geo

SRC_W, SRC_H = 1920, 1080

# Everything off, which is the measured default: `push_amount`, `snap_amount`
# and `shake_px` are all 0 in the style the pilots were rendered with, so every
# shot there is a single size held for its whole length.
STILL: dict = {}
MOVING = {"push_amount": 0.12, "push_hz": 10.0}


def _shot(t0: float, t1: float, composition: str = "crop", **extra) -> dict:
    shot = {
        "index": 0, "t0": t0, "t1": t1, "camera": "face",
        "rect": {"x": 100, "y": 100, "w": 540, "h": 960},
        "anchor": [370, 580], "move": "hold", "snap": False, "shake": 0.0,
        "composition": composition,
    }
    shot.update(extra)
    return shot


def _plan(shots: list[dict], style: dict | None = None) -> dict:
    return {"duration": shots[-1]["t1"], "shots": shots, "style": style or STILL}


def _merge(shots: list[dict], style: dict | None = None) -> list[dict]:
    return geo.merge_equivalent_shots(_plan(shots, style), SRC_W, SRC_H)["shots"]


def _crop_fit_fit_crop() -> list[dict]:
    """The case the whole batch exists for. The two `fit` shots are one image;
    the boundaries either side of them are real cuts and must survive."""
    return [
        _shot(0.0, 2.0, "crop"),
        _shot(2.0, 4.0, "fit", rect={"x": 0, "y": 0, "w": 1920, "h": 1080}),
        _shot(4.0, 6.0, "fit", rect={"x": 300, "y": 20, "w": 800, "h": 600}),
        _shot(6.0, 10.0, "crop"),
    ]


def test_crop_fit_fit_crop_becomes_three_shots():
    merged = _merge(_crop_fit_fit_crop())
    assert [s["composition"] for s in merged] == ["crop", "fit", "crop"]
    assert [s["index"] for s in merged] == [0, 1, 2]


def test_the_join_takes_no_time_out_of_the_clip():
    """Removing a cut must not remove any of the clip with it."""
    merged = _merge(_crop_fit_fit_crop())
    assert merged[1]["t0"] == 2.0 and merged[1]["t1"] == 6.0
    assert merged[0]["t0"] == 0.0 and merged[-1]["t1"] == 10.0
    assert sum(s["t1"] - s["t0"] for s in merged) == 10.0


def test_the_absorbed_shot_leaves_no_command_in_the_sendcmd():
    """The proof that the picture did not change: the entry the merge removed
    was a `crop w/h/x/y` identical to the one already in force."""
    before = geo.build_sendcmd(_plan(_crop_fit_fit_crop()), SRC_W, SRC_H)
    after = geo.build_sendcmd(
        geo.merge_equivalent_shots(_plan(_crop_fit_fit_crop()), SRC_W, SRC_H),
        SRC_W, SRC_H)
    lines_before = [l for l in before.splitlines() if l.strip()]
    lines_after = [l for l in after.splitlines() if l.strip()]
    assert len(lines_before) - len(lines_after) == 1
    # The two `fit` entries were the same command at different timestamps.
    dropped = [l for l in lines_before if l not in lines_after]
    assert len(dropped) == 1 and dropped[0].startswith("4.000 ")
    assert "4.000" not in after


def test_the_first_shots_metadata_is_what_survives():
    """No "dominant reason" is invented: shots carry no `reason` or
    `confidence` yet, so choosing between two sets of energies would be a rule
    dressed as a measurement. Only `t1` grows."""
    shots = [_shot(0.0, 2.0, "fit", energy=0.9, text="first", camera="face"),
             _shot(2.0, 4.0, "fit", energy=0.1, text="second", camera="game",
                   rect={"x": 9, "y": 9, "w": 90, "h": 160})]
    merged = _merge(shots)
    assert len(merged) == 1
    assert merged[0]["energy"] == 0.9
    assert merged[0]["text"] == "first"
    assert merged[0]["camera"] == "face"
    assert merged[0]["t1"] == 4.0


def test_the_real_cuts_on_either_side_survive():
    for pair in (["crop", "fit"], ["fit", "crop"]):
        shots = [_shot(0.0, 2.0, pair[0]), _shot(2.0, 4.0, pair[1])]
        assert len(_merge(shots)) == 2, pair


def test_the_same_rect_with_a_different_final_frame_does_not_merge():
    """The rect is not the picture. A `crop` and a `fit` shot can carry the
    identical rectangle and deliver completely different frames — which is
    exactly what the merge this replaced could not see."""
    rect = {"x": 0, "y": 0, "w": 1920, "h": 1080}
    shots = [_shot(0.0, 2.0, "crop", rect=rect), _shot(2.0, 4.0, "fit", rect=rect)]
    assert len(_merge(shots)) == 2


def test_two_crops_on_the_same_frame_still_merge():
    """The old rule's one true case, kept: with the game ladder collapsed,
    `game` and `game_tight` are two names for one rectangle."""
    shots = [_shot(0.0, 2.0, "crop", camera="game"),
             _shot(2.0, 4.0, "crop", camera="game_tight")]
    assert len(_merge(shots)) == 1


def test_a_shot_that_moves_is_never_absorbed():
    """Its size changes across its own length, so joining it to a neighbour
    would restart that movement mid-shot."""
    shots = [_shot(0.0, 2.0, "crop", move="push"), _shot(2.0, 4.0, "crop", move="push")]
    assert geo.visual_key(shots[0], MOVING, SRC_W, SRC_H) is None
    assert len(_merge(shots, MOVING)) == 2
    # The same two shots, with the movement disabled, are one.
    assert len(_merge(shots, STILL)) == 1


def test_an_identical_shake_merges_and_a_different_one_does_not():
    """Shake is a function of ABSOLUTE `t`, so the same expression continues
    unbroken across a join: a single size point means static SIZE, not a static
    image. Change the anchor or the amplitude and it is a different expression,
    and a different picture."""
    same = [_shot(0.0, 2.0, "crop", shake=3.0), _shot(2.0, 4.0, "crop", shake=3.0)]
    assert len(_merge(same)) == 1

    louder = [_shot(0.0, 2.0, "crop", shake=3.0), _shot(2.0, 4.0, "crop", shake=6.0)]
    assert len(_merge(louder)) == 2

    elsewhere = [_shot(0.0, 2.0, "crop", shake=3.0),
                 _shot(2.0, 4.0, "crop", shake=3.0, anchor=[900, 580])]
    assert len(_merge(elsewhere)) == 2


def test_a_hole_between_two_identical_shots_is_not_a_join():
    """Something else was on screen. Two shots either side of it are not one
    shot however alike they look."""
    shots = [_shot(0.0, 2.0, "fit"), _shot(3.5, 6.0, "fit")]
    assert len(_merge(shots)) == 2


def test_the_merge_is_idempotent_in_the_provenance_too():
    """The WHOLE plan, not just its shots. Recomputing `shot_count_before_merge`
    from the shots it was handed made a second application report the merged
    count as the planned one — and that count is the only thing standing between
    a clip with one invisible cut and the static renderer."""
    once = geo.merge_equivalent_shots(_plan(_crop_fit_fit_crop()), SRC_W, SRC_H)
    twice = geo.merge_equivalent_shots(once, SRC_W, SRC_H)
    assert once == twice
    assert once["shot_count_before_merge"] == 4
    assert once["equivalent_cuts_removed"] == 1


def test_the_merge_does_not_touch_the_plan_it_was_given():
    """Pure. The planner hands it the finished plan and keeps nothing else."""
    plan = _plan(_crop_fit_fit_crop())
    before = json.dumps(plan, sort_keys=True)
    merged = geo.merge_equivalent_shots(plan, SRC_W, SRC_H)
    assert json.dumps(plan, sort_keys=True) == before
    assert merged["duration"] == plan["duration"]
    assert len(merged["shots"]) < len(plan["shots"])


def test_an_empty_or_single_shot_plan_survives():
    assert geo.merge_equivalent_shots({"shots": []}, SRC_W, SRC_H)["shots"] == []
    one = _merge([_shot(0.0, 3.0, "fit")])
    assert len(one) == 1 and one[0]["index"] == 0


def test_the_geometry_and_the_planner_import_in_either_order():
    """`dynamic_geometry` used to reach `ASPECT` through `dynamic_edit`'s
    re-export, which made the geometry import the planner and stopped the
    planner importing the geometry it plans."""
    root = Path(__file__).resolve().parents[1]
    for first, second in (("dynamic_edit", "dynamic_geometry"),
                          ("dynamic_geometry", "dynamic_edit")):
        code = f"import services.clipper.{first}, services.clipper.{second}"
        done = subprocess.run([sys.executable, "-c", code], cwd=root,
                              capture_output=True, text=True)
        assert done.returncode == 0, f"{first} first: {done.stderr}"


# --- the corpus gate ---------------------------------------------------------

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "pilot_shot_plans.json"


def test_the_frozen_corpus_loses_exactly_the_invisible_cuts():
    """R1's gate, on the four pilot projects' plans AS THEY WERE when the rule
    was measured: 1.341 shots become 1.225 and nothing else moves.

    Against a fixture, not against `data/clipper`. Reading the live corpus made
    this skip on every machine but one — a gate that does not exist — and worse,
    a correct R1 re-render rewrites those sidecars with the merged shot list, so
    the test would then assert 1.341 against files that say 1.225 and fail for
    being right. Regenerate with `scripts/build_shot_merge_fixture.py --write`,
    which also reports the same numbers off the real corpus.
    """
    corpus = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    before = after = 0
    for plan in corpus["plans"]:
        src_w = int(plan.get("src_w") or SRC_W)
        src_h = int(plan.get("src_h") or SRC_H)
        merged = geo.merge_equivalent_shots(plan, src_w, src_h)
        before += len(plan["shots"])
        after += len(merged["shots"])

        # Nothing may be lost with the cut: same span, same order.
        assert merged["shots"][0]["t0"] == plan["shots"][0]["t0"]
        assert merged["shots"][-1]["t1"] == plan["shots"][-1]["t1"]

        style = plan.get("style") or {}
        for a, b in zip(merged["shots"], merged["shots"][1:]):
            assert not geo._joins(a, b, style, src_w, src_h), "an invisible cut survived"

    assert len(corpus["plans"]) == 58
    assert (before, after) == (1341, 1225)
    assert (before, after) == (corpus["shots_before"], corpus["shots_after"])


def test_the_planner_itself_emits_no_invisible_cut():
    """End to end: the merge runs INSIDE `plan_dynamic_edit`, so a caller that
    never heard of it still gets a plan in which every cut is visible. A second
    pass afterwards would be too late — the plan no longer records what the
    absorbed shot's composition had been."""
    from tests.test_clipper_dynamic import _plan as plan_a_clip

    for style in ({}, {"game_height_pct": 1.0, "game_zoom": 1.0},
                  {"game_height_pct": 0.86, "game_zoom": 0.64}):
        plan = plan_a_clip(**style)
        shots, shot_style = plan["shots"], plan.get("style") or {}
        for a, b in zip(shots, shots[1:]):
            assert not geo._joins(a, b, shot_style, SRC_W, SRC_H), \
                f"an invisible cut at {b['t0']}s, style={style}"
        assert [s["index"] for s in shots] == list(range(len(shots)))
