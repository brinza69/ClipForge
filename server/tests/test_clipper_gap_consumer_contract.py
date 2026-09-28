"""Motion provenance must refer to exactly the pixels and seed it claims."""
from copy import deepcopy

import pytest


def _case():
    seed = dict(t=100., state='detected', boxes=[[64,30,24,24]],
        frame_index=70, decoded_t=7., address_basis='opencv_ffmpeg_metadata',
        clock='source_requested', decoded_space='reencoded_window')
    target = dict(t=100.75, state='empty', boxes=[], frame_index=78,
        decoded_t=7.8, address_basis='opencv_ffmpeg_metadata',
        clock='source_requested', decoded_space='reencoded_window', motion=dict(
            method='lk_fb_v1', state='tracked', seed_frame_index=70,
            seed_decoded_t=7., seed_box=[64,30,24,24], target_frame_index=78,
            target_decoded_t=7.8, age_s=.8, box=[110.,30.,24.,24.],
            quality=dict(n_survivors=12, initial_corners=16,
                         fb_err_max=.1, appear_delta=.08)))
    return [seed, target], [(100.,76.,42.,24.,24.)]


def _call(rows, elected):
    from services.clipper.face_gap_framing import motion_proposals
    return motion_proposals(rows, elected, 5., 8., 3.8, .3, 24.,
        240, 120, 93., 1., 1., {'x':50,'y':0,'w':60,'h':110})


def test_exact_seed_can_support_motion_outside_old_camera():
    rows, elected = _case()
    before = deepcopy(rows)
    proposals, scope = _call(rows, elected)
    assert len(proposals) == 1 and scope['tracked_used'] == 1
    assert rows == before


@pytest.mark.parametrize('where,key,value', [
    ('target','state','detected'), ('target','state','detector_unavailable'),
    ('target','boxes',[[64,30,24,24]]), ('target','frame_index',79),
    ('target','decoded_t',7.7), ('target','t',99.75),
    ('target','decoded_space','original_source'),
    ('target','address_basis',None),
    ('target','clock','analysed_file_requested'), ('target','t',True),
    ('target','t','100.75'),
    ('motion','target_frame_index',79), ('motion','target_decoded_t',7.7),
    ('motion','seed_frame_index',70.9), ('motion','seed_box',[65,30,24,24]),
    ('motion','age_s',.1), ('motion','age_s',True),
    ('motion','box',[None,30,24,24]), ('motion','box',[float('nan'),30,24,24]),
    ('motion','box',[110,30,-1,24]), ('motion','box',[230,30,24,24]),
    ('seed','frame_index',float('nan')), ('seed','frame_index',70.1),
    ('seed','decoded_space','original_source'), ('seed','address_basis',None),
    ('seed','clock','analysed_file_requested'), ('seed','t','100'),
])
def test_inconsistent_record_cannot_authorize_crop(where, key, value):
    rows, elected = _case()
    node = rows[1]['motion'] if where == 'motion' else rows[0 if where == 'seed' else 1]
    node[key] = value
    proposals, scope = _call(rows, elected)
    assert proposals == []
    assert scope['tracked_used'] == 0


def test_height_is_part_of_seed_identity_too():
    rows, elected = _case()
    elected[0] = (*elected[0][:4], 25.)
    assert _call(rows, elected)[0] == []


def test_no_elected_seed_does_not_silently_elect_motion():
    rows, _ = _case()
    proposals, scope = _call(rows, [])
    assert proposals == [] and scope['unmatched_seed'] == 1


def test_refused_scope_retains_tracker_stop_reason():
    rows, elected = _case()
    rows[1]['motion'].update(state='refused', reason='budget_exceeded')
    proposals, scope = _call(rows, elected)
    assert proposals == [] and scope['tracked_refused'] == 1
    assert 'budget_exceeded' in str(scope['refused'])


def test_reverse_frame_order_cannot_claim_forward_time():
    rows, elected = _case()
    rows[1]['frame_index'] = rows[1]['motion']['target_frame_index'] = 69
    assert _call(rows, elected)[0] == []


def test_elected_centre_inside_camera_cannot_authorize_out_of_frame_seed():
    rows, _ = _case()
    rows[0]['boxes'] = [[-4,30,160,24]]
    rows[1]['motion']['seed_box'] = [-4,30,160,24]
    assert _call(rows, [(100.,76.,42.,160.,24.)])[0] == []


def test_accepted_motion_retains_measured_quality_and_both_clocks():
    rows, elected = _case()
    _, scope = _call(rows, elected)
    used = scope['used'][0]
    assert used['quality'] == rows[1]['motion']['quality']
    assert used['target_source_requested_t'] == 100.75
    assert used['target_decoded_t'] == 7.8
    used['quality']['n_survivors'] = 0
    assert rows[1]['motion']['quality']['n_survivors'] == 12


@pytest.mark.parametrize('field,value', [('state','detected'),
    ('decoded_space','original_source'), ('address_basis',None)])
def test_inconsistent_target_is_an_explicit_refusal(field, value):
    rows, elected = _case()
    rows[1][field] = value
    proposals, scope = _call(rows, elected)
    assert proposals == []
    assert scope['tracked_refused'] == len(scope['refused']) == 1


def test_fixed_subject_anchor_never_consumes_motion(monkeypatch):
    from services.clipper import dynamic_edit, face_gap_framing
    rows, _ = _case()
    monkeypatch.setattr(dynamic_edit, '_cut_times', lambda *_: [])

    def forbidden(*args, **kwargs):
        pytest.fail('fixed anchor consulted the motion consumer')

    monkeypatch.setattr(face_gap_framing, 'motion_proposals', forbidden)
    plan = dynamic_edit.plan_dynamic_edit(
        {'start':100.,'end':102.,'words':[]}, {}, rows,
        src_w=240, src_h=120, proxy_w=240, proxy_h=120,
        stable_track={'cx':76.,'cy':42.,'w':24.}, no_second_camera=True)
    assert plan['shots']
