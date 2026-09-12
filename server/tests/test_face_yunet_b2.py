"""B2 tests: profiles, actual-instance provenance, 2x resize and inverse mapping.

Tests confirm:
- Unknown profile raises ValueError at construction.
- detect() reports profile/score_thresh/scale from the INSTANCE, not module constants.
- small_faces_v1 resizes 2x, runs detection on enlarged image, and maps boxes back
  to caller's input pixel space before clamping/rounding.
- raw_boxes_model_space carries model-space floats for small_faces_v1, None for native_v1.
- Non-square and odd-size images are handled correctly.
- Clamping after inverse mapping works correctly.
- All existing degenerate/error paths still produce STATE_UNAVAILABLE.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch, call
import numpy as np
import pytest

from services.clipper.face_yunet import (
    MODEL_BYTES, MODEL_FILENAME, MODEL_SHA256, SCORE_THRESH, NMS_THRESH, TOP_K,
    STATE_DETECTED, STATE_EMPTY, STATE_UNAVAILABLE,
    PROFILES, YuNetDetector,
)


# ── fixtures ──────────────────────────────────────────────────────────────────

def _blank(h=64, w=64):
    return np.zeros((h, w, 3), dtype=np.uint8)


def _faces_row(x, y, w, h, sc=0.95):
    """Build a single-row YuNet output array."""
    row = [x, y, w, h] + [0.0] * 10 + [sc]
    return np.array([row], dtype=np.float32)


def _valid_detector(tmp_path, monkeypatch, profile="native_v1",
                    faces=None, count=0, resize_call_recorder=None):
    """Return a YuNetDetector with mocked hash check and cv2 backend."""
    model = tmp_path / MODEL_FILENAME
    model.write_bytes(b"\x00" * MODEL_BYTES)
    monkeypatch.setattr("services.clipper.face_yunet._sha256_file",
                        lambda _: MODEL_SHA256)
    mock_det = MagicMock()
    mock_det.detect.return_value = (count, faces)
    mock_cv2 = MagicMock()
    mock_cv2.FaceDetectorYN.create.return_value = mock_det
    mock_cv2.INTER_LINEAR = 1  # any sentinel value

    if resize_call_recorder is not None:
        def recording_resize(src, dsize, **kwargs):
            resize_call_recorder.append({"src_shape": src.shape, "dsize": dsize,
                                         "kwargs": kwargs})
            return np.zeros((dsize[1], dsize[0], 3), dtype=np.uint8)
        mock_cv2.resize.side_effect = recording_resize
    else:
        mock_cv2.resize.side_effect = (
            lambda src, dsize, **kw: np.zeros((dsize[1], dsize[0], 3), dtype=np.uint8))

    monkeypatch.setattr("services.clipper.face_yunet._cv2", lambda: mock_cv2)
    return YuNetDetector(str(model), profile=profile), mock_cv2


# ── profile construction ──────────────────────────────────────────────────────

def test_unknown_profile_raises_value_error(tmp_path):
    model = tmp_path / MODEL_FILENAME
    model.write_bytes(b"\x00" * MODEL_BYTES)
    with pytest.raises(ValueError, match="unknown profile"):
        YuNetDetector(str(model), profile="does_not_exist")


def test_native_v1_is_default(tmp_path, monkeypatch):
    det, _ = _valid_detector(tmp_path, monkeypatch, profile="native_v1")
    assert det._profile == "native_v1"
    assert det._score_thresh == 0.9
    assert det._scale == 1.0


def test_small_faces_v1_settings_frozen(tmp_path, monkeypatch):
    det, _ = _valid_detector(tmp_path, monkeypatch, profile="small_faces_v1")
    assert det._profile == "small_faces_v1"
    assert det._score_thresh == 0.75
    assert det._scale == 2.0


def test_preprocess_failure_is_unavailable_with_input_identity(tmp_path, monkeypatch):
    det, cv = _valid_detector(tmp_path, monkeypatch, profile="small_faces_v1")
    cv.resize.side_effect = RuntimeError("resize failed")
    result = det.detect(_blank(h=51, w=83))
    assert result["state"] == STATE_UNAVAILABLE
    assert result["reason"] == "preprocess_error"
    assert result["input_size"] == [83, 51]
    assert result["model_input_size"] is None
    cv.FaceDetectorYN.create.return_value.detect.assert_not_called()


def test_observed_dimensions_match_backend_input(tmp_path, monkeypatch):
    det, cv = _valid_detector(tmp_path, monkeypatch, profile="small_faces_v1")
    result = det.detect(_blank(h=51, w=83))
    image = cv.FaceDetectorYN.create.return_value.detect.call_args.args[0]
    assert result["input_size"] == [83, 51]
    assert result["model_input_size"] == [image.shape[1], image.shape[0]] == [166, 102]
    assert result["interpolation"] == "linear"


def test_known_profiles_complete(tmp_path, monkeypatch):
    """Both documented profiles must construct without error."""
    for profile in ("native_v1", "small_faces_v1"):
        det, _ = _valid_detector(tmp_path, monkeypatch, profile=profile)
        assert det.init_error is None


# ── detect() reports actual instance settings, not module constants ───────────

def test_native_v1_detect_reports_09_not_module_constant(tmp_path, monkeypatch):
    det, _ = _valid_detector(tmp_path, monkeypatch, profile="native_v1",
                             faces=None, count=0)
    r = det.detect(_blank())
    assert r["state"] == STATE_EMPTY
    assert r["score_thresh"] == 0.9
    assert r["profile"] == "native_v1"
    assert r["scale"] == 1.0


def test_small_faces_v1_detect_reports_075_not_09(tmp_path, monkeypatch):
    det, _ = _valid_detector(tmp_path, monkeypatch, profile="small_faces_v1",
                             faces=None, count=0)
    r = det.detect(_blank())
    assert r["score_thresh"] == 0.75
    assert r["score_thresh"] != SCORE_THRESH, "must differ from module constant"
    assert r["profile"] == "small_faces_v1"
    assert r["scale"] == 2.0


def test_detect_always_includes_top_k(tmp_path, monkeypatch):
    for profile in ("native_v1", "small_faces_v1"):
        det, _ = _valid_detector(tmp_path, monkeypatch, profile=profile)
        r = det.detect(_blank())
        assert r["top_k"] == 5000


def test_detect_always_includes_nms_thresh(tmp_path, monkeypatch):
    for profile in ("native_v1", "small_faces_v1"):
        det, _ = _valid_detector(tmp_path, monkeypatch, profile=profile)
        r = det.detect(_blank())
        assert r["nms_thresh"] == 0.3


# ── 2x resize path ────────────────────────────────────────────────────────────

def test_native_v1_does_not_resize(tmp_path, monkeypatch):
    calls = []
    det, mock_cv2 = _valid_detector(tmp_path, monkeypatch, profile="native_v1",
                                    faces=None, resize_call_recorder=calls)
    det.detect(_blank(48, 64))
    assert calls == [], "native_v1 must not call cv2.resize"


def test_small_faces_v1_resizes_2x(tmp_path, monkeypatch):
    calls = []
    det, _ = _valid_detector(tmp_path, monkeypatch, profile="small_faces_v1",
                             faces=None, resize_call_recorder=calls)
    det.detect(_blank(48, 64))
    assert len(calls) == 1
    assert calls[0]["dsize"] == (128, 96), "dsize is (w*2, h*2)"


def test_small_faces_v1_resize_non_square(tmp_path, monkeypatch):
    calls = []
    det, _ = _valid_detector(tmp_path, monkeypatch, profile="small_faces_v1",
                             faces=None, resize_call_recorder=calls)
    det.detect(_blank(30, 80))
    assert calls[0]["dsize"] == (160, 60)


def test_small_faces_v1_resize_odd_size(tmp_path, monkeypatch):
    """Odd-size image: 2x gives exact integer (31*2=62, 47*2=94)."""
    calls = []
    det, _ = _valid_detector(tmp_path, monkeypatch, profile="small_faces_v1",
                             faces=None, resize_call_recorder=calls)
    det.detect(_blank(31, 47))
    assert calls[0]["dsize"] == (94, 62)


def test_small_faces_v1_setInputSize_uses_scaled_dims(tmp_path, monkeypatch):
    """setInputSize must receive scaled dimensions, not original."""
    det, mock_cv2 = _valid_detector(tmp_path, monkeypatch, profile="small_faces_v1",
                                    faces=None)
    det.detect(_blank(48, 64))
    # setInputSize should have been called with (128, 96)
    det._detector.setInputSize.assert_called_once_with((128, 96))


# ── inverse mapping ───────────────────────────────────────────────────────────

def test_inverse_mapping_divides_by_scale(tmp_path, monkeypatch):
    """Box at (20, 40, 30, 20) in 2x scaled space → (10, 20, 15, 10) in original."""
    # scaled image is 2x → box coords in scaled space are 2x what we expect in original
    faces = _faces_row(20, 40, 30, 20)
    det, _ = _valid_detector(tmp_path, monkeypatch, profile="small_faces_v1",
                             faces=faces, count=1)
    r = det.detect(_blank(64, 80))
    assert r["state"] == STATE_DETECTED
    x, y, w, h = r["boxes"][0]
    assert x == 10 and y == 20 and w == 15 and h == 10


def test_inverse_mapping_non_square_image(tmp_path, monkeypatch):
    """30x80 image (h=30, w=80); scaled 60x160; box at (40, 20, 20, 10) in scaled."""
    faces = _faces_row(40, 20, 20, 10)
    det, _ = _valid_detector(tmp_path, monkeypatch, profile="small_faces_v1",
                             faces=faces, count=1)
    r = det.detect(_blank(30, 80))
    assert r["state"] == STATE_DETECTED
    x, y, w, h = r["boxes"][0]
    assert x == 20 and y == 10 and w == 10 and h == 5


def test_inverse_mapping_odd_size(tmp_path, monkeypatch):
    """31x47 → scaled 62x94; box at (10, 12, 14, 8) in scaled → (5, 6, 7, 4)."""
    faces = _faces_row(10, 12, 14, 8)
    det, _ = _valid_detector(tmp_path, monkeypatch, profile="small_faces_v1",
                             faces=faces, count=1)
    r = det.detect(_blank(31, 47))
    assert r["state"] == STATE_DETECTED
    x, y, w, h = r["boxes"][0]
    assert x == 5 and y == 6 and w == 7 and h == 4


def test_inverse_mapping_clamp_to_original_bounds(tmp_path, monkeypatch):
    """Box partially outside scaled space clamps to original image bounds."""
    # Original 64x64. Scaled 128x128.
    # Box at (-20, -20, 60, 60) in scaled space.
    # Mapped: x0=max(0, -10)=0, y0=max(0, -10)=0, x1=min(64, 20)=20, y1=min(64, 20)=20
    faces = _faces_row(-20, -20, 60, 60)
    det, _ = _valid_detector(tmp_path, monkeypatch, profile="small_faces_v1",
                             faces=faces, count=1)
    r = det.detect(_blank(64, 64))
    assert r["state"] == STATE_DETECTED
    x, y, w, h = r["boxes"][0]
    assert x == 0 and y == 0 and w == 20 and h == 20


def test_inverse_mapping_wholly_outside_returns_unavailable(tmp_path, monkeypatch):
    """Box wholly outside the original image → degenerate after clamp → unavailable."""
    # Original 64x64. Scaled 128x128.
    # Box at (200, 200, 20, 20) in scaled space.
    # Mapped: x0=100, x1=min(64, 110)=64 → x0(100) > x1(64) → degenerate
    faces = _faces_row(200, 200, 20, 20)
    det, _ = _valid_detector(tmp_path, monkeypatch, profile="small_faces_v1",
                             faces=faces, count=1)
    r = det.detect(_blank(64, 64))
    assert r["state"] == STATE_UNAVAILABLE
    assert r["reason"] == "invalid_box"


# ── raw_boxes_model_space ─────────────────────────────────────────────────────

def test_native_v1_raw_boxes_model_space_is_none(tmp_path, monkeypatch):
    faces = _faces_row(5, 5, 20, 20)
    det, _ = _valid_detector(tmp_path, monkeypatch, profile="native_v1",
                             faces=faces, count=1)
    r = det.detect(_blank(64, 64))
    assert r["raw_boxes_model_space"] is None


def test_small_faces_v1_raw_boxes_model_space_in_scaled_coords(tmp_path, monkeypatch):
    """raw_boxes_model_space must contain UNSCALED (model-space) float coords."""
    faces = _faces_row(20.5, 40.5, 30.0, 20.0)
    det, _ = _valid_detector(tmp_path, monkeypatch, profile="small_faces_v1",
                             faces=faces, count=1)
    r = det.detect(_blank(64, 80))
    assert r["state"] == STATE_DETECTED
    raw = r["raw_boxes_model_space"]
    assert raw is not None and len(raw) == 1
    rx, ry, rw, rh = raw[0]
    assert abs(rx - 20.5) < 1e-4
    assert abs(ry - 40.5) < 1e-4
    assert abs(rw - 30.0) < 1e-4
    assert abs(rh - 20.0) < 1e-4


def test_raw_boxes_model_space_none_on_empty_result(tmp_path, monkeypatch):
    det, _ = _valid_detector(tmp_path, monkeypatch, profile="small_faces_v1",
                             faces=None, count=0)
    r = det.detect(_blank())
    assert r["state"] == STATE_EMPTY
    assert r["raw_boxes_model_space"] is None


def test_raw_boxes_model_space_none_on_unavailable(tmp_path, monkeypatch):
    det, _ = _valid_detector(tmp_path, monkeypatch, profile="small_faces_v1")
    det._reason = "init_error"
    r = det.detect(_blank())
    assert r["state"] == STATE_UNAVAILABLE
    assert r["raw_boxes_model_space"] is None


# ── native_v1 backward compat: box clamping still works ──────────────────────

def test_native_v1_box_clamping_still_works(tmp_path, monkeypatch):
    faces = _faces_row(-10, -10, 50, 50)
    det, _ = _valid_detector(tmp_path, monkeypatch, profile="native_v1",
                             faces=faces, count=1)
    r = det.detect(_blank(64, 64))
    assert r["state"] == STATE_DETECTED
    x, y, w, h = r["boxes"][0]
    assert x == 0 and y == 0 and w == 40 and h == 40
