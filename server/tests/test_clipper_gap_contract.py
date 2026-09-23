"""Independent pixel/address contract for short motion-only face continuations."""
from copy import deepcopy

import cv2
import numpy as np
import pytest


def _fixture(tmp_path, *, frames=24, cut=None, texture=True, green=False, height=120):
    path = tmp_path / 'motion.avi'
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'FFV1'),
                             10, (240, height))
    assert writer.isOpened()
    rng = np.random.default_rng(782)
    patch = rng.integers(30, 225, (36, 36, 3), dtype=np.uint8)
    if green:
        patch[:, :, 0] //= 4
        patch[:, :, 2] //= 4
        patch[:, :, 1] = rng.integers(180, 245, (36,36), dtype=np.uint8)
    if not texture:
        patch[:] = 120
    for i in range(frames):
        frame = np.full((height, 240, 3), 70, dtype=np.uint8)
        frame[30:66, 30+2*i:66+2*i] = patch
        if cut is not None and i >= cut:
            frame[:] = 15
        writer.write(frame)
    writer.release()
    cap = cv2.VideoCapture(str(path))
    assert cap.getBackendName() == 'FFMPEG'
    samples = []
    for i in range(frames):
        assert cap.get(cv2.CAP_PROP_POS_FRAMES) == i
        ok, _ = cap.read()
        assert ok
        samples.append(dict(t=i/10, frame_index=i,
            decoded_t=cap.get(cv2.CAP_PROP_POS_MSEC)/1000,
            boxes=[[30,30,36,36]] if i == 0 else [],
            state='detected' if i == 0 else 'empty', reason=None,
            clock='analysed_file_requested', decoded_space='analysed_file',
            address_basis='opencv_ffmpeg_metadata'))
    cap.release()
    return path, samples


def test_pixels_follow_translation_without_rewriting_detections(tmp_path):
    from services.clipper.face_gap import continue_gaps
    path, samples = _fixture(tmp_path)
    before = deepcopy(samples)
    result = continue_gaps(path, samples)
    assert samples == before
    assert len(result) == len(samples)
    for original, updated in zip(samples, result):
        assert {k: updated[k] for k in original} == original
    for i in (5, 11, 19):
        motion = result[i]['motion']
        assert motion['state'] == 'tracked'
        assert motion['seed_frame_index'] == 0
        assert motion['target_frame_index'] == i
        assert motion['target_decoded_t'] == samples[i]['decoded_t']
        assert motion['box'] == pytest.approx([30+2*i,30,36,36], abs=1)
    assert result[21]['motion']['state'] == 'refused'
    assert 'budget' in result[21]['motion']['reason']


@pytest.mark.parametrize('field,value', [('frame_index', 500), ('decoded_t', 9),
                                        ('frame_index', None), ('decoded_t', float('nan'))])
def test_address_mismatch_is_not_a_motion_measurement(tmp_path, field, value):
    from services.clipper.face_gap import continue_gaps
    path, samples = _fixture(tmp_path, frames=7)
    samples[3][field] = value
    result = continue_gaps(path, samples)
    assert result[3]['motion']['state'] == 'refused'
    assert result[4]['motion']['state'] != 'tracked'


@pytest.mark.parametrize('state', ['unreadable', 'detector_unavailable', None])
def test_unknown_observations_do_not_bridge_the_track(tmp_path, state):
    from services.clipper.face_gap import continue_gaps
    path, samples = _fixture(tmp_path, frames=7)
    samples[3]['state'] = state
    result = continue_gaps(path, samples)
    assert result[3].get('motion', {}).get('state') != 'tracked'
    assert result[4].get('motion', {}).get('state') != 'tracked'


def test_flat_seed_has_no_measured_texture_support(tmp_path):
    from services.clipper.face_gap import continue_gaps
    path, samples = _fixture(tmp_path, frames=7, texture=False)
    result = continue_gaps(path, samples)
    assert result[-1].get('motion', {}).get('state') != 'tracked'


def test_replaced_scene_stops_the_original_track(tmp_path):
    from services.clipper.face_gap import continue_gaps
    path, samples = _fixture(tmp_path, frames=8, cut=4)
    result = continue_gaps(path, samples)
    assert result[3]['motion']['state'] == 'tracked'
    assert result[4]['motion']['state'] == 'refused'
    assert result[7]['motion']['state'] != 'tracked'


def test_several_faces_cannot_seed_a_gap(tmp_path):
    from services.clipper.face_gap import continue_gaps
    path, samples = _fixture(tmp_path, frames=7)
    samples[0]['boxes'].append([160,30,30,30])
    result = continue_gaps(path, samples)
    assert all(r.get('motion', {}).get('state') != 'tracked' for r in result)


def test_new_detection_ends_previous_seed_lineage(tmp_path):
    from services.clipper.face_gap import continue_gaps
    path, samples = _fixture(tmp_path, frames=8)
    samples[4].update(state='detected', boxes=[[38,30,36,36]])
    result = continue_gaps(path, samples)
    assert result[3]['motion']['seed_frame_index'] == 0
    assert result[4].get('motion', {}).get('state') != 'tracked'
    assert result[5]['motion']['seed_frame_index'] == 4


@pytest.mark.parametrize('row,field,value', [
    (0, 'decoded_t', .03), (3, 'decoded_t', .33),
    (0, 'frame_index', True), (3, 'frame_index', '3'),
    (0, 'address_basis', None), (3, 'decoded_space', 'original_source'),
])
def test_false_precision_or_unknown_clock_is_refused(tmp_path, row, field, value):
    from services.clipper.face_gap import continue_gaps
    path, samples = _fixture(tmp_path, frames=7)
    samples[row][field] = value
    result = continue_gaps(path, samples)
    assert result[max(1,row)]['motion']['state'] == 'refused'
    assert result[-1]['motion']['state'] == 'refused'


def test_integral_float_indices_do_not_bypass_actual_address_check(tmp_path):
    from services.clipper.face_gap import continue_gaps
    path, samples = _fixture(tmp_path, frames=7)
    for row in samples:
        row['frame_index'] = float(row['frame_index'])
    assert continue_gaps(path, samples)[5]['motion']['state'] == 'tracked'


def test_motion_metadata_reaches_the_actual_renderer(tmp_path, monkeypatch):
    from services.clipper import dynamic_window, dynamic_edit, dynamic_render, face_detector
    # A tall source permits the moving target's union to fit a portrait crop;
    # blurred letterbox copies would also match the green-pixel oracle.
    path, _ = _fixture(tmp_path, frames=24, green=True, height=320)
    calls = []

    def detect(_grey):
        calls.append(1)
        return [[30,30,36,36]] if len(calls) == 1 else []

    monkeypatch.setattr(face_detector, 'detect_faces', detect)
    window = dynamic_window.analyse_window(path, 0., 2., None, 240)
    assert window['faces'][0]['boxes'] == [[30,30,36,36]]
    assert all(not r['boxes'] for r in window['faces'][1:])
    assert any(r.get('motion',{}).get('state') == 'tracked' for r in window['faces'])
    monkeypatch.setattr(dynamic_edit, '_cut_times', lambda *_: [])
    plan = dynamic_edit.plan_dynamic_edit(
        {'start':0.,'end':2.,'words':[]}, {}, window['faces'],
        src_w=240, src_h=320, proxy_w=240, proxy_h=320,
        no_second_camera=True, style={'push_amount':.2,'shake_px':6})
    assert plan['subject']['samples'] == 1
    assert sum(s['motion']['tracked_used'] for s in plan['subject']['framing_windows']) > 0
    assert all(s.get('composition') != 'fit' for s in plan['shots'])
    output = tmp_path/'export.mp4'
    dynamic_render.render_dynamic_clip(str(path), plan, str(output), start=0.,
        work_dir=tmp_path, src_w=240, src_h=320, out_w=180, out_h=320,
        fps=10, has_audio=False)
    cap = cv2.VideoCapture(str(output))
    try:
        for n in (1, 18):
            cap.set(cv2.CAP_PROP_POS_FRAMES,n)
            assert cap.get(cv2.CAP_PROP_POS_FRAMES) == n
            ok, frame = cap.read()
            assert ok
            b,g,r = cv2.split(frame.astype(np.int16))
            ys,xs = np.where((g>r+60)&(g>b+60))
            assert len(xs)>300
            assert 1<xs.min()<xs.max()<178
            assert 1<ys.min()<ys.max()<318
            assert (xs.max()-xs.min()+1)/(ys.max()-ys.min()+1) == pytest.approx(1.,abs=.12)
    finally:
        cap.release()
