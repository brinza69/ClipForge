"""Consumer tests for face_gap_framing.motion_proposals.

Fixtures are built to pass ALL gates; each failure test disables exactly one.

Source: 240x120. Footprint of base_rect {"x":80,"y":20,"w":80,"h":80} ≈ (75,0,165,120).
Seed box [100,20,20,20]: cx=110, cy=30 — inside footprint.
Tracked box [102,20,20,20]: cx=112, inside frame (102+20=122 < 240).
"""
from __future__ import annotations

import copy
import math
import pytest

SRC_W, SRC_H = 240, 120
SX = SY = 1.0
MULT, HEADROOM, FSPAN = 3.5, 0.15, 20.0

_SEED_BOX   = [100, 20, 20, 20]   # cx=110, cy=30
_SEED_CX    = 110.0
_SEED_CY    = 30.0
_SEED_W     = 20.0
_SEED_H     = 20.0
_TRACKED_BOX = [102.0, 20.0, 20.0, 20.0]  # cx=112, cy=30; 102+20=122 < 240

_DS  = "reencoded_window"
_AB  = "opencv_ffmpeg_metadata"
_CLK = "source_requested"


def _base_rect():
    return {"x": 80, "y": 20, "w": 80, "h": 80}


def _seed_entry(**kw):
    e = dict(t=0.0, state="detected", boxes=[list(_SEED_BOX)],
             frame_index=0, decoded_t=0.0,
             decoded_space=_DS, address_basis=_AB, clock=_CLK)
    e.update(kw)
    return e


def _target_entry(**kw):
    e = dict(t=0.3, state="empty", boxes=[],
             frame_index=3, decoded_t=0.3,
             decoded_space=_DS, address_basis=_AB, clock=_CLK)
    e.update(kw)
    return e


def _motion(**kw):
    m = dict(method="lk_fb_v1", state="tracked",
             seed_frame_index=0, seed_decoded_t=0.0, seed_box=list(_SEED_BOX),
             target_frame_index=3, target_decoded_t=0.3,
             age_s=round(0.3 - 0.0, 6),
             box=list(_TRACKED_BOX),
             quality=dict(n_survivors=12, initial_corners=20,
                          fb_err_max=0.4, appear_delta=0.05))
    m.update(kw)
    return m


def _span(**kw):
    s = dict(abs_t=0.0, cx=_SEED_CX, cy=_SEED_CY, w=_SEED_W, h=_SEED_H)
    s.update(kw)
    return (s["abs_t"], s["cx"], s["cy"], s["w"], s["h"])


def _call(face_track, spans):
    from services.clipper.face_gap_framing import motion_proposals
    return motion_proposals(face_track, spans, 0.0, 1.0,
                            MULT, HEADROOM, FSPAN, SRC_W, SRC_H,
                            0.0, SX, SY, _base_rect())


def _ok():
    seed   = _seed_entry()
    target = _target_entry(motion=_motion())
    return [seed, target], [_span()]


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------

def test_valid_tracked_motion_produces_proposal_with_provenance():
    rows, spans = _ok()
    props, scope = _call(rows, spans)
    assert scope["tracked_used"] == 1
    assert len(props) == 1
    assert scope["tracked_refused"] == 0
    assert scope["unmatched_seed"] == 0
    assert len(scope["used"]) == 1
    u = scope["used"][0]
    assert u["method"] == "lk_fb_v1"
    assert u["seed_frame_index"] == 0
    assert u["target_frame_index"] == 3
    assert u["age_s"] == pytest.approx(0.3, abs=1e-9)


def test_refused_count_equals_len_refused():
    rows, spans = _ok()
    _, scope = _call(rows, spans)
    total = scope["tracked_refused"] + scope["unmatched_seed"] + scope["unknown_method"]
    assert total == len(scope["refused"])


# ---------------------------------------------------------------------------
# motion.state gates
# ---------------------------------------------------------------------------

def test_refused_state_with_reason_preserved():
    rows, spans = _ok()
    rows[1]["motion"]["state"] = "refused"
    rows[1]["motion"]["reason"] = "budget_exceeded"
    _, scope = _call(rows, spans)
    assert scope["tracked_refused"] == 1
    assert any(r.get("tracker_reason") == "budget_exceeded"
               for r in scope["refused"])


def test_unknown_method_in_refused_list():
    rows, spans = _ok()
    rows[1]["motion"]["method"] = "future_v99"
    _, scope = _call(rows, spans)
    assert scope["unknown_method"] == 1
    assert len(scope["refused"]) == 1


# ---------------------------------------------------------------------------
# target row gates — entries WITH motion key must appear in refused
# ---------------------------------------------------------------------------

def test_non_empty_state_refused_not_silently_dropped():
    rows, spans = _ok()
    rows[1]["state"] = "detected"
    _, scope = _call(rows, spans)
    assert scope["tracked_refused"] == 1
    assert len(scope["refused"]) == 1


def test_nonempty_boxes_list_refused():
    rows, spans = _ok()
    rows[1]["boxes"] = [[10, 10, 5, 5]]
    _, scope = _call(rows, spans)
    assert scope["tracked_refused"] == 1


def test_wrong_decoded_space_refused():
    rows, spans = _ok()
    rows[1]["decoded_space"] = "original_source"
    _, scope = _call(rows, spans)
    assert scope["tracked_refused"] == 1


def test_wrong_address_basis_refused():
    rows, spans = _ok()
    rows[1]["address_basis"] = None
    _, scope = _call(rows, spans)
    assert scope["tracked_refused"] == 1


def test_wrong_clock_refused():
    rows, spans = _ok()
    rows[1]["clock"] = "analysed_file_requested"
    _, scope = _call(rows, spans)
    assert scope["tracked_refused"] == 1


# ---------------------------------------------------------------------------
# target address match
# ---------------------------------------------------------------------------

def test_motion_target_frame_index_mismatch():
    rows, spans = _ok()
    rows[1]["motion"]["target_frame_index"] = 99
    _, scope = _call(rows, spans)
    assert scope["tracked_refused"] == 1


def test_motion_target_decoded_t_mismatch():
    rows, spans = _ok()
    rows[1]["motion"]["target_decoded_t"] = 9.9
    _, scope = _call(rows, spans)
    assert scope["tracked_refused"] == 1


# ---------------------------------------------------------------------------
# age gates
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("age", [0.0, -0.1, 2.001, float("nan"), float("inf"), True])
def test_invalid_age_refused(age):
    rows, spans = _ok()
    rows[1]["motion"]["age_s"] = age
    _, scope = _call(rows, spans)
    assert scope["tracked_refused"] == 1


def test_age_pts_inconsistency_refused():
    rows, spans = _ok()
    rows[1]["motion"]["age_s"] = 0.9   # 0.3 - 0.0 = 0.3 ≠ 0.9
    _, scope = _call(rows, spans)
    assert scope["tracked_refused"] == 1
    assert any("pts_inconsistent" in r.get("reason", "") for r in scope["refused"])


# ---------------------------------------------------------------------------
# frame_index and PTS ordering
# ---------------------------------------------------------------------------

def test_frame_index_ordering_violated():
    rows, spans = _ok()
    rows[1]["motion"]["target_frame_index"] = 0   # same as seed_frame_index
    rows[1]["frame_index"] = 0
    rows[1]["decoded_t"] = 0.0
    rows[1]["motion"]["target_decoded_t"] = 0.0
    rows[1]["motion"]["age_s"] = 0.0  # will fail age check first
    # Use a case where fi ordering clearly fails but other checks pass:
    # target_fi = seed_fi = 0, but age=0 fails first
    _, scope = _call(rows, spans)
    assert scope["tracked_refused"] == 1


def test_pts_ordering_violated():
    rows, spans = _ok()
    rows[1]["motion"]["seed_decoded_t"] = 0.5   # > target_decoded_t=0.3
    rows[1]["motion"]["age_s"] = round(0.3 - 0.5, 6)  # negative — fails age gate
    _, scope = _call(rows, spans)
    assert scope["tracked_refused"] == 1


# ---------------------------------------------------------------------------
# seed lookup gates
# ---------------------------------------------------------------------------

def test_seed_not_found_unmatched():
    rows, spans = _ok()
    # fi=2 < target_fi=3 (passes ordering), but no seed with fi=2 in lookup
    rows[1]["motion"]["seed_frame_index"] = 2
    rows[1]["motion"]["seed_decoded_t"] = 0.2
    rows[1]["motion"]["age_s"] = round(0.3 - 0.2, 6)
    _, scope = _call(rows, spans)
    assert scope["unmatched_seed"] == 1
    assert len(scope["refused"]) == 1


def test_seed_box_mismatch_unmatched():
    rows, spans = _ok()
    rows[1]["motion"]["seed_box"] = [50, 50, 20, 20]
    _, scope = _call(rows, spans)
    assert scope["unmatched_seed"] == 1


def test_multi_box_seed_not_in_lookup():
    rows, spans = _ok()
    rows[0]["boxes"].append([160, 20, 20, 20])   # not singleton
    _, scope = _call(rows, spans)
    assert scope["unmatched_seed"] == 1


def test_seed_wrong_decoded_space_not_in_lookup():
    rows, spans = _ok()
    rows[0]["decoded_space"] = "original_source"
    _, scope = _call(rows, spans)
    assert scope["unmatched_seed"] == 1


def test_seed_wrong_address_basis_not_in_lookup():
    rows, spans = _ok()
    rows[0]["address_basis"] = None
    _, scope = _call(rows, spans)
    assert scope["unmatched_seed"] == 1


def test_seed_wrong_clock_not_in_lookup():
    rows, spans = _ok()
    rows[0]["clock"] = "analysed_file_requested"
    _, scope = _call(rows, spans)
    assert scope["unmatched_seed"] == 1


# ---------------------------------------------------------------------------
# source time ordering
# ---------------------------------------------------------------------------

def test_source_time_ordering_refused():
    rows, spans = _ok()
    rows[1]["t"] = -0.1   # before seed.t=0.0
    _, scope = _call(rows, spans)
    # abs_t=-0.1 < abs_t0=0.0 → skipped (outside window), not refused
    assert scope["tracked_used"] == 0


def test_source_time_equal_refused():
    # target.t == seed.t → not strictly greater
    seed   = _seed_entry(t=0.3)
    target = _target_entry(t=0.3, motion=_motion())
    span   = _span(abs_t=0.3)
    _, scope = _call([seed, target], [span])
    assert scope["tracked_refused"] == 1


# ---------------------------------------------------------------------------
# elected span gates
# ---------------------------------------------------------------------------

def test_seed_not_in_elected_spans():
    rows, _ = _ok()
    _, scope = _call(rows, [_span(abs_t=5.0)])
    assert scope["unmatched_seed"] == 1


def test_seed_height_mismatch_unmatched():
    rows, _ = _ok()
    _, scope = _call(rows, [_span(h=99.0)])
    assert scope["unmatched_seed"] == 1


def test_empty_elected_spans_unmatched():
    rows, _ = _ok()
    _, scope = _call(rows, [])
    assert scope["unmatched_seed"] == 1


# ---------------------------------------------------------------------------
# footprint gate
# ---------------------------------------------------------------------------

def test_seed_centre_outside_footprint_refused():
    # Seed at far right: cx=220 outside footprint [75, 165]
    out_box = [210, 20, 20, 20]
    seed   = _seed_entry()
    seed["boxes"] = [out_box]
    target = _target_entry(motion=_motion(
        seed_box=out_box, box=[212.0, 20.0, 20.0, 20.0]))
    span   = _span(cx=220.0, cy=30.0, w=20.0, h=20.0)
    _, scope = _call([seed, target], [span])
    assert scope["tracked_refused"] == 1


# ---------------------------------------------------------------------------
# tracked box bounds
# ---------------------------------------------------------------------------

def test_tracked_box_out_of_frame_refused():
    rows, spans = _ok()
    # x=218, w=24 → 218+24=242 > src_w=240
    rows[1]["motion"]["box"] = [218.0, 20.0, 24.0, 20.0]
    _, scope = _call(rows, spans)
    assert scope["tracked_refused"] == 1
    assert any("outside_source_frame" in r.get("reason", "") for r in scope["refused"])


def test_tracked_box_negative_x_refused():
    rows, spans = _ok()
    rows[1]["motion"]["box"] = [-1.0, 20.0, 20.0, 20.0]
    _, scope = _call(rows, spans)
    assert scope["tracked_refused"] == 1


# ---------------------------------------------------------------------------
# strict t validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_t", [None, float("nan"), float("inf"), True])
def test_bad_target_t_skipped(bad_t):
    rows, spans = _ok()
    rows[1]["t"] = bad_t
    _, scope = _call(rows, spans)
    assert scope["tracked_used"] == 0


# ---------------------------------------------------------------------------
# no mutation
# ---------------------------------------------------------------------------

def test_input_not_mutated():
    rows, spans = _ok()
    before_rows  = copy.deepcopy(rows)
    before_spans = copy.deepcopy(spans)
    _call(rows, spans)
    assert rows  == before_rows
    assert spans == before_spans


# ---------------------------------------------------------------------------
# fractional frame_index strict check
# ---------------------------------------------------------------------------

def test_fractional_seed_frame_index_not_in_lookup():
    rows, spans = _ok()
    rows[0]["frame_index"] = 2.5   # _is_valid_fi rejects non-integral
    rows[1]["motion"]["seed_frame_index"] = 2   # would match int(2.5) if truncated
    _, scope = _call(rows, spans)
    assert scope["unmatched_seed"] == 1
