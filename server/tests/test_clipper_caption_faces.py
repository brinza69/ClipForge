"""Caption avoidance needs local output geometry, then proof in burned pixels."""
from copy import deepcopy
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from services.clipper.caption_faces import place
from workers.clipper_captions import _caption_faces


def observed():
    return {"style": {"push_amount": 0}, "shots": [
        {"t0": 0, "t1": 2, "composition": "crop", "anchor": [540, 960],
         "rect": {"x": 0, "y": 0, "w": 1080, "h": 1920}}],
        "_face_space": {"width": 108, "height": 192, "clock": "source_requested"},
        "_review_faces": [
            {"t": 100.5, "boxes": [[20, 40, 60, 93.5]], "state": "detected",
             "frame_index": 42, "decoded_t": 0.5},
            {"t": 101.5, "boxes": [], "state": "empty",
             "frame_index": 43, "decoded_t": 1.5},
        ]}


def decide(dyn=None, plan=None, y=.51, keep=None):
    return place(plan or {}, dyn if dyn is not None else observed(), start=100,
                 src_w=1080, src_h=1920, current_y=y, keep_out=keep or [])


def test_exact_bottom_preset_can_clear_a_face_when_the_grid_cannot():
    dyn = observed()
    untouched = deepcopy(dyn)
    report = decide(dyn)
    assert report["applied"] and report["y_pct"] == .75
    assert report["face_conflicts_before"] == 1
    assert report["face_conflicts_after"] == 0
    assert report["empty_samples"] == 1 and not report["coverage_complete"]
    assert dyn == untouched
    assert not decide(y=.75)["applied"]


@pytest.mark.parametrize("state,boxes", [
    ("future_state", []), ("future_state", [[20, 40, 60, 93.5]]),
    ("empty", [[20, 40, 60, 93.5]]), ("unreadable", [[20, 40, 60, 93.5]]),
    ("detector_unavailable", [[20, 40, 60, 93.5]]), ("detected", []),
    ("empty", None)])
def test_unknown_or_contradictory_observation_cannot_move_caption(state, boxes):
    dyn = observed()
    dyn["_review_faces"] = [{"t": 100.5, "state": state, "boxes": boxes}]
    report = decide(dyn)
    assert report["samples_in_shots"] == report["invalid_state_samples"] == 1
    assert report["empty_samples"] == report["mapped_boxes"] == 0
    assert not report["applied"] and report["y_pct"] == .51


@pytest.mark.parametrize("change,reason", [
    (lambda d: d.pop("_face_space"), "face_coordinate_space_unknown"),
    (lambda d: d["_face_space"].update(clock="clip"), "face_coordinate_space_unknown"),
    (lambda d: d["shots"][0].update(shake=4), "moving_crop_not_supported"),
    (lambda d: d["shots"][0].update(move="push"), "shot_crop_changes_size_across_the_shot"),
    (lambda d: d.update(_review_faces=[]), "no_mapped_face_observations"),
    (lambda d: d["_review_faces"][0].update(boxes=[[0, 0, 108, 192]]),
     "no_clear_position_in_observations"),
    (lambda d: d["_review_faces"][0].update(boxes=[[0, 0, float('nan'), 4]]),
     "box_is_not_four_finite_numbers"),
])
def test_uncertainty_or_no_room_does_not_move_the_caption(change, reason):
    dyn = observed()
    # A positive push amplitude is only active on shots marked as pushes.
    dyn["style"]["push_amount"] = .05
    change(dyn)
    report = decide(dyn)
    assert not report["applied"] and report["y_pct"] == .51
    assert report["reason"] == reason
    assert not report["coverage_complete"]


def test_source_time_and_delivered_anchor_both_matter():
    dyn = observed()
    dyn["shots"][0].update(t1=1, anchor=[810, 960],
                           rect={"x": 0, "y": 0, "w": 540, "h": 960})
    dyn["shots"].append({**dyn["shots"][0], "t0": 1, "t1": 2, "anchor": [270, 960]})
    dyn["_review_faces"] = [{"t": t, "boxes": [[72, 84, 10, 30]]}
                            for t in (100.5, 101.5)]
    report = decide(dyn)
    assert report["mapped_boxes"] == 1 and report["off_frame_boxes"] == 1
    assert report["shots_without_mapped_faces"] == 1
    assert report["applied"] and .73 < report["y_pct"] <= .75


@pytest.mark.parametrize("plan,reason", [
    ({"y_pct_manual": True}, "manual_position"),
    ({"scale": 1.5}, "caption_extent_not_supported"),
    ({"style": {"font_size": 120}}, "caption_extent_not_supported"),
    ({"entry_pop": True}, "caption_extent_not_supported"),
])
def test_manual_edits_and_unsupported_caption_sizes_keep_their_position(plan, reason):
    report = decide(plan=plan)
    assert not report["applied"] and report["reason"] == reason


def test_existing_normalized_ui_reservations_cannot_be_overwritten():
    clip = SimpleNamespace(id="c", start_time=100, caption_plan={"y_pct": .51},
        layout_plan={"safe_zones": {"keep_out": [
            {"x": 0, "y": .69, "w": 1, "h": .31, "kind": "ui"}]}})
    project = SimpleNamespace(width=1080, height=1920)
    y, report = _caption_faces(clip, project, observed(), None)
    # Bottom is reserved; the small band above the face remains available.
    assert y < .2 and report["applied"]


@pytest.mark.parametrize("manual,suppress", [(False, False), (True, False), (False, True)])
async def test_decision_reaches_the_burned_pixels_and_sidecar(
        tmp_path, monkeypatch, manual, suppress):
    from models import ClipModel, ProjectModel
    from services.clipper.ffmpeg_tools import ffmpeg_bin, run
    from services.clipper import render_input
    from workers import clipper_render_plan as planning, clipper_render_output as output
    from workers.clipper_preview_frame import _render_frame

    source = tmp_path / "source.mp4"
    run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         "color=c=black:s=108x192:r=24:d=2", "-c:v", "libx264", str(source)],
        what="face avoidance fixture")
    project = ProjectModel(id="face", width=108, height=192, fps=24,
        video_path=str(source), clipper_settings={"source_has_burned_captions": suppress})
    clip = ClipModel(id="face", project_id="face", start_time=0, end_time=2, duration=2,
        caption_plan={"y_pct": .51, "y_pct_manual": manual, "preset_id": "clean_minimal",
                      "chunks": [{"start": 0, "end": 2, "text": "VISIBLE WORDS"}]})
    dyn = observed()
    dyn["shots"][0].update(anchor=[54, 96], rect={"x": 0, "y": 0, "w": 108, "h": 192})
    dyn.update(duration=2)
    dyn["_review_faces"][0]["t"] = .5
    dyn["_review_faces"][1]["t"] = 1.5

    async def dynamic(*args, **kwargs):
        return deepcopy(dyn)

    async def drops(*args):
        return []

    monkeypatch.setattr(planning, "_dynamic_plan", dynamic)
    monkeypatch.setattr(planning, "_dead_spans", drops)
    monkeypatch.setattr(planning, "_layout_plan", lambda *a: {})
    decision = await planning._decide_render(clip, project, tmp_path)
    moved = not (manual or suppress)
    assert decision["caption_y"] == (.75 if moved else None)
    if suppress:
        assert decision["caption_face_placement"] is None
    else:
        assert decision["caption_face_placement"]["applied"] is moved
    still = _render_frame(clip, project, decision, str(source), tmp_path, .5)
    image = cv2.imdecode(np.frombuffer(still, np.uint8), cv2.IMREAD_COLOR)
    decision["render"] = {"preset": "ultrafast", "crf": 18}
    out = tmp_path / "clip.mp4"
    result = await output.render_export(clip, project, decision, out, src=str(source))
    cap = cv2.VideoCapture(str(out))
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, 500)
        ok, delivered = cap.read()
    finally:
        cap.release()
    assert ok and np.abs(image.astype(float) - delivered.astype(float)).mean() < 2
    # Independent pixel coordinates: no search helper or sidecar y in this oracle.
    ys, _ = np.where(delivered.min(axis=2) > 160)
    if suppress:
        assert len(ys) == 0
    else:
        assert len(ys) > 500
        assert (ys.min() > 1350) if moved else (900 < ys.min() < 1000)
    body = result["sidecar"]
    assert body["caption_face_placement"] == decision["caption_face_placement"]
    assert not [k for k in body["dynamic_plan"] if k.startswith("_")]
    schema = render_input.FINGERPRINT_SCHEMA_V2
    assert body["input_fingerprint"] == render_input.input_fingerprint(body, schema=schema)
    changed = {**body, "caption_y": .51 if moved else .75}
    assert render_input.input_fingerprint(changed, schema=schema) != body["input_fingerprint"]
