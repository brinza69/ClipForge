"""A clip median must not turn a local face shot into a curtain shot."""
from __future__ import annotations

import shutil

import pytest

from services.clipper import dynamic_edit, dynamic_geometry, dynamic_render
from services.clipper.dynamic_face_framing import face_framing
from services.clipper.evidence_map import crop_window
from services.clipper.ffmpeg_tools import ffmpeg_bin, run


def _shot(rect, *, fit=False):
    return {"t0": 0.0, "t1": 1.0, "rect": rect,
            "anchor": [rect["x"] + rect["w"] // 2, rect["y"] + rect["h"] // 2],
            "composition": "fit" if fit else "crop", "move": "hold"}


def test_curtain_counterexample_keeps_both_proposals_in_delivered_window():
    base = {"x": 1866, "y": 358, "w": 314, "h": 560}
    face = {"cx": 2024, "cy": 588}
    rect, fit, reason = face_framing(base, (2292, 684), face, .41, 3840, 2160,
                                    anchored=False, observed_centres=[(2292, 684)])
    assert not fit
    assert reason == "unanchored_face_positions_disagree_widened"
    # Local proposal x=2134..2448, y=454..1014; old camera must also survive.
    window = crop_window(_shot(rect), style={}, src_w=3840, src_h=2160)
    assert not isinstance(window, str)
    x, y, w, h = window
    y -= dynamic_geometry.canvas_size(3840, 2160)[2]
    assert x <= 1866 and y <= 358
    assert x + w >= 2448 and y + h >= 1014


def test_fixed_source_anchor_still_rejects_the_stray_detection():
    base = {"x": 1546, "y": 34, "w": 186, "h": 334}
    rect, fit, reason = face_framing(base, (1640, 400), {"cx": 1640, "cy": 176},
                                    .42, 1920, 1080, anchored=True,
                                    observed_centres=[(1640, 400)])
    assert rect == base
    assert not fit and reason is None


def test_conflict_too_wide_for_portrait_uses_full_frame():
    base = {"x": 20, "y": 10, "w": 100, "h": 180}
    rect, fit, reason = face_framing(base, (430, 100), {"cx": 70, "cy": 85},
                                    .42, 480, 270, anchored=False,
                                    observed_centres=[(430, 100)])
    assert fit and reason == "unanchored_face_positions_disagree_fit"
    window = crop_window(_shot(rect, fit=fit), style={}, src_w=480, src_h=270)
    assert not isinstance(window, str)
    x, y, w, h = window
    y -= dynamic_geometry.canvas_size(480, 270)[2]
    assert x <= 0 and y <= 0 and x + w >= 480 and y + h >= 270


def test_agreeing_local_and_clip_positions_keep_the_existing_crop():
    base = {"x": 1546, "y": 34, "w": 186, "h": 334}
    rect, fit, reason = face_framing(base, (1640, 176), {"cx": 1640, "cy": 176},
                                    .42, 1920, 1080, anchored=False,
                                    observed_centres=[(1640, 176)])
    assert rect == dynamic_edit._rect(0, 334, 1640, 176, .42, 1920, 1080)
    assert not fit and reason is None


def test_a_phantom_local_centre_does_not_widen_the_webcam():
    base = {"x": 1546, "y": 34, "w": 186, "h": 334}
    rect, fit, reason = face_framing(base, (1640, 402), {"cx": 1640, "cy": 176},
                                    .42, 1920, 1080, anchored=False,
                                    observed_centres=[(1640, 172), (1350, 402)])
    assert rect == base
    assert not fit and reason is None


def test_nearby_fallback_is_not_reported_as_local_evidence():
    base = {"x": 1866, "y": 358, "w": 314, "h": 560}
    rect, fit, reason = face_framing(base, (2616, 512), {"cx": 2024, "cy": 588},
                                    .41, 3840, 2160, anchored=False, observed_centres=[])
    assert reason.endswith("_nearby_sample_only")
    assert rect["w"] > base["w"] or fit


def _moving_plan(monkeypatch, *, anchored=False):
    # 4s establish the clip median, then the visible target shifts sideways.
    # These are proxy observations, not an override of the planner's median.
    track = [{"t": i / 4, "boxes": [[120 if i < 16 else 137, 30, 14, 14]]}
             for i in range(24)]
    monkeypatch.setattr(dynamic_edit, "_cut_times", lambda *_: [4.0])
    monkeypatch.setattr(dynamic_edit, "_pick_camera", lambda *_: "face_tight")
    return dynamic_edit.plan_dynamic_edit(
        {"start": 0, "end": 6, "words": []}, {}, track,
        src_w=960, src_h=540, proxy_w=240, proxy_h=135,
        stable_track={"cx": 127, "cy": 37, "w": 14} if anchored else None,
        # Deliberately nonzero: the protection must survive active effects too.
        style={"face_rung_energy": [-1, -1], "push_amount": .25,
               "snap_amount": .15, "shake_px": 8})


def test_planner_keeps_the_local_target_and_does_not_zoom_out_the_protection(monkeypatch):
    plan = _moving_plan(monkeypatch)
    shot = plan["shots"][-1]
    assert shot["framing_adjustment"] == "unanchored_face_positions_disagree_widened"
    assert shot["move"] == "hold" and not shot["snap"] and shot["shake"] == 0
    assert len(dynamic_geometry._size_timeline(shot, plan["style"], 960, 540)) == 1
    window = crop_window(shot, style=plan["style"], src_w=960, src_h=540)
    assert not isinstance(window, str)
    x, y, w, h = window
    y -= dynamic_geometry.canvas_size(960, 540)[2]
    # Entire known local fixture box at source x=548..604, y=120..176.
    assert x <= 548 and x + w >= 604 and y <= 120 and y + h >= 176


def test_planner_preserves_the_fixed_anchor_branch(monkeypatch):
    plan = _moving_plan(monkeypatch, anchored=True)
    shot = plan["shots"][-1]
    assert "framing_adjustment" not in shot
    assert shot["rect"] == plan["cameras"]["face_tight"]


def test_actual_encoded_pixels_keep_the_local_target(monkeypatch, tmp_path):
    import cv2
    import numpy as np

    if shutil.which(ffmpeg_bin()) is None:
        pytest.skip("ffmpeg is required for actual crop validation")
    source, out = tmp_path / "source.mp4", tmp_path / "out.mp4"
    # A green local target at the NEW location, on a red background. The old
    # median crop ends at x~560 and cuts most of it off. Check decoded pixels,
    # independently of the geometry helper that produced the new crop.
    run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         "color=c=red:s=960x540:r=10:d=6,drawbox=x=548:y=120:w=56:h=56:color=lime:t=fill",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(source)],
        what="face crop fixture")
    plan = _moving_plan(monkeypatch)
    dynamic_render.render_dynamic_clip(str(source), plan, str(out), start=0,
                                       work_dir=tmp_path, src_w=960, src_h=540,
                                       out_w=180, out_h=320, fps=10, has_audio=False)
    cap = cv2.VideoCapture(str(out))
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, 5000)
        ok, frame = cap.read()
        assert ok
        b, g, r = cv2.split(frame.astype(np.int16))
        ys, xs = np.where((g > r + 60) & (g > b + 60))
        assert len(xs) > 100
        assert 2 < xs.min() < xs.max() < 177
        # A full square survives with equal scales. A lateral crop leaves a
        # narrow strip; a bad fit stretches it. Neither passes this assertion.
        assert (xs.max() - xs.min() + 1) / (ys.max() - ys.min() + 1) == pytest.approx(1, abs=.12)
    finally:
        cap.release()
