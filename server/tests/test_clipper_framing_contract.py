"""Independent counterexamples for the automatic-framing implementation."""
import pytest

from services.clipper.dynamic_cameras import camera_rects, _rect
from services.clipper.dynamic_face_envelope import elected_spans, widen_for_envelope
from services.clipper import dynamic_geometry, evidence_map


def test_editorial_span_cannot_move_the_game_camera():
    face = {"cx": 232, "cy": 116, "w": 64}
    old = camera_rects(face, {}, 1920, 1080)
    new = camera_rects(face, {}, 1920, 1080, framing_span=84)
    assert new["face"]["h"] > old["face"]["h"]
    assert new["game"] == old["game"]
    assert new["game_tight"] == old["game_tight"]
    assert face["w"] == 64


def test_missing_observation_does_not_invent_a_square_measurement():
    assert elected_spans([], [(1, 30, 40, 20)], 1, 1, 0) == []


def test_other_face_at_same_time_cannot_supply_the_height():
    track = [{"t": 1, "boxes": [[100, 100, 20, 200]]}]
    # Equal width/time but the centre names a different face.
    assert elected_spans(track, [(1, 30, 40, 20)], 1, 1, 0) == []


def test_nearby_time_cannot_supply_the_height():
    track = [{"t": 1.001, "boxes": [[20, 30, 20, 20]]}]
    assert elected_spans(track, [(1, 30, 40, 20)], 1, 1, 0) == []


def test_same_timestamp_recovers_only_the_matching_elected_face():
    track = [{"t": 1, "boxes": [[20, 30, 20, 20]]},
             {"t": 1, "boxes": [[200, 100, 20, 100]]}]
    assert elected_spans(track, [(1, 30, 40, 20)], 1, 1, 0) == [(1, 30, 40, 20, 20)]


@pytest.mark.parametrize("box", [[20,30,20,0], [20,30,20,-4], [20,30,20,float("nan")],
                                  [float("nan"),30,20,20], [20,float("inf"),20,20]])
def test_invalid_box_cannot_be_counted_as_an_extent_observation(box):
    from services.clipper.dynamic_cameras import _face_samples
    track=[{"t":1,"boxes":[box]}]
    assert elected_spans(track, _face_samples(track,1,1,0),1,1,0)==[]


def test_unreadable_footprint_cannot_disappear_from_containment(monkeypatch):
    from services.clipper import dynamic_face_envelope as module
    base={"x":200,"y":100,"w":200,"h":360}
    unreadable={"x":0,"y":0,"w":100,"h":180}
    original=module._footprint
    monkeypatch.setattr(module,"_footprint",lambda r,w,h: None if r==unreadable else original(r,w,h))
    _,_,reason=module.widen_for_envelope(base,[dict(base),unreadable],960,540)
    assert reason is not None and "unavailable" in reason


def test_missing_base_geometry_cannot_disable_the_spatial_filter(monkeypatch):
    from services.clipper import dynamic_face_envelope as module
    monkeypatch.setattr(module,"_footprint",lambda *_: None)
    proposals,scope=module.local_proposals(
        [(1,300,200,40,50)],0,2,3.8,.44,50,960,540,0,
        {"x":200,"y":100,"w":200,"h":360})
    assert proposals==[]
    assert scope["seen"]==1 and scope["included"]==0
    assert scope["unavailable"]==1 and "unavailable" in scope["reason"]


def test_minecraft_wall_outside_existing_camera_cannot_force_full_fit(monkeypatch):
    from services.clipper import dynamic_edit
    monkeypatch.setattr(dynamic_edit,"_cut_times",lambda *_:[5.13])
    monkeypatch.setattr(dynamic_edit,"_pick_camera",lambda *_:"face")
    track=[{"t":i/4,"boxes":[[397,31,27,27]]} for i in range(20)]
    track += [{"t":7,"boxes":[[299,41,80,80]]},
              {"t":7.5,"boxes":[[397,31,27,27]]}]
    plan=dynamic_edit.plan_dynamic_edit(
        {"start":0,"end":8,"words":[]},{},track,
        src_w=1920,src_h=1080,proxy_w=480,proxy_h=270,no_second_camera=True)
    assert all(s["composition"]=="crop" for s in plan["shots"])
    shot=plan["shots"][-1]
    # The real inset face remains inside the delivered shot. The stray game's
    # centre is not permission to select the rest of the frame as our subject.
    x,y,w,h=evidence_map.crop_window(shot,src_w=1920,src_h=1080,style=plan["style"])
    y-=dynamic_geometry.canvas_size(1920,1080)[2]
    assert x<=1588 and x+w>=1696 and y<=124 and y+h>=232
    assert x>1356
    assert plan["subject"]["framing_scope"]=="observed_centres_inside_existing_camera"
    scopes=plan["subject"]["framing_windows"]
    assert sum(w["excluded"] for w in scopes)==1
    assert all(w["source_t0"]<w["source_t1"] for w in scopes)


def delivered(rect, fit=False):
    shot = {"t0": 0, "t1": 1, "rect": rect, "move": "hold", "shake": 0,
            "anchor": [rect["x"]+rect["w"]/2, rect["y"]+rect["h"]/2],
            "composition": "fit" if fit else "crop"}
    crop = evidence_map.crop_window(shot, src_w=960, src_h=540, style={})
    assert not isinstance(crop, str)
    x, y, w, h = crop
    return x, y-dynamic_geometry.canvas_size(960,540)[2], x+w, y+h-dynamic_geometry.canvas_size(960,540)[2]


@pytest.mark.parametrize("x,y", [(0,0),(960,0),(0,540),(960,540),(480,270)])
def test_union_keeps_the_delivered_proposals_at_source_edges(x, y):
    base = _rect(0, 200, x, y, .42, 960, 540)
    proposal = _rect(0, 230, x, y, .42, 960, 540)
    rect, fit, _ = widen_for_envelope(base, [proposal], 960, 540)
    # Edge geometry itself must not force a full-source fallback. The requested
    # camera already clamps 2px from edges; compare actual footprints, not an
    # impossible promise to include pixel zero using that renderer's crop mode.
    assert not fit
    a = delivered(rect, fit)
    for p in (base, proposal):
        b = delivered(p)
        assert a[0] <= b[0] and a[1] <= b[1]
        assert a[2] >= b[2] and a[3] >= b[3]


def test_planned_containment_does_not_hide_renderer_rounding():
    # _rect floors width, renderer rounds it. This proposal ends at x=712
    # in the plan, but at 713 in the delivered camera. The base ends at 712.
    base = {"x": 404, "y": 30, "w": 308, "h": 550}
    proposal = {"x": 464, "y": 70, "w": 248, "h": 444}
    rect, fit, _ = widen_for_envelope(base, [proposal], 1920, 1080)

    def bounds(r, is_fit=False):
        shot = {"t0": 0, "t1": 1, "rect": r, "move": "hold",
                "anchor": [r["x"]+r["w"]/2, r["y"]+r["h"]/2],
                "composition": "fit" if is_fit else "crop"}
        x,y,w,h = evidence_map.crop_window(shot, src_w=1920, src_h=1080, style={})
        return x,y,x+w,y+h

    a, b = bounds(rect, fit), bounds(proposal)
    assert a[0] <= b[0] and a[1] <= b[1]
    assert a[2] >= b[2] and a[3] >= b[3]


def test_moving_target_survives_in_decoded_frames_at_both_ends(monkeypatch, tmp_path):
    import shutil
    import cv2
    import numpy as np
    from services.clipper import dynamic_edit, dynamic_render
    from services.clipper.ffmpeg_tools import ffmpeg_bin, run

    if shutil.which(ffmpeg_bin()) is None:
        pytest.skip("real renderer requires ffmpeg")
    source, output = tmp_path / "source.mp4", tmp_path / "output.mp4"
    run([ffmpeg_bin(), "-y", "-v", "error", "-f", "lavfi", "-i",
         "color=red:s=960x540:r=20:d=2,"
         "drawbox=x=340:y=170:w=100:h=100:color=lime:t=fill:enable='lt(t,0.25)',"
         "drawbox=x=400:y=170:w=100:h=100:color=lime:t=fill:enable='between(t,0.25,1.7)',"
         "drawbox=x=460:y=170:w=100:h=100:color=lime:t=fill:enable='gte(t,1.75)'",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(source)],
        what="moving target fixture")
    monkeypatch.setattr(dynamic_edit, "_cut_times", lambda *_: [])
    track = [{"t": i/4, "boxes": [[340 if i==0 else 460 if i==7 else 400, 170, 100, 100]]}
             for i in range(8)]
    plan = dynamic_edit.plan_dynamic_edit(
        {"start": 0, "end": 2, "words": []}, {}, track,
        src_w=960, src_h=540, proxy_w=960, proxy_h=540,
        style={"face_rung_energy": [-1,-1], "push_amount": .25,
               "snap_amount": .15, "shake_px": 8})
    assert len(plan["shots"]) == 1
    shot = plan["shots"][0]
    assert "envelope" in shot["framing_adjustment"]
    assert shot["move"] == "hold" and not shot["snap"] and shot["shake"] == 0
    dynamic_render.render_dynamic_clip(
        str(source), plan, str(output), start=0, work_dir=tmp_path,
        src_w=960, src_h=540, out_w=180, out_h=320, fps=20, has_audio=False)
    cap=cv2.VideoCapture(str(output))
    try:
        for index in (2,37):  # 0.10s and 1.85s: between planner observations
            cap.set(cv2.CAP_PROP_POS_FRAMES,index)
            assert cap.get(cv2.CAP_PROP_POS_FRAMES)==index
            ok,frame=cap.read()
            assert ok
            b,g,r=cv2.split(frame.astype(np.int16))
            ys,xs=np.where((g>r+60)&(g>b+60))
            assert len(xs)>400
            assert 1<xs.min()<xs.max()<178
            assert 1<ys.min()<ys.max()<318
            assert (xs.max()-xs.min()+1)/(ys.max()-ys.min()+1)==pytest.approx(1,abs=.08)
    finally:
        cap.release()
