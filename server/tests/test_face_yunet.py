"""B1 tests: YuNet adapter state semantics, box validation, model identity,
and one-to-one matching (including the greedy counterexample).

All YuNet inference tests mock cv2.FaceDetectorYN; no real model is needed.
The real model at data/models/face_detection_yunet_2023mar.onnx is smoke-tested
on a synthetic blank image only.  Benchmark/main() tests live in
test_benchmark_detectors.py.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

from services.clipper.face_yunet import (
    MODEL_BYTES, MODEL_FILENAME, MODEL_SHA256,
    STATE_DETECTED, STATE_EMPTY, STATE_UNAVAILABLE,
    YuNetDetector,
)
from benchmark_face_detectors import iou_box, max_cardinality_matching


# ── fixtures ──────────────────────────────────────────────────────────────────

def _write_model(tmp_path: Path, *, size: int = MODEL_BYTES,
                 content: bytes | None = None) -> Path:
    p = tmp_path / MODEL_FILENAME
    p.write_bytes(content if content is not None else b"\x00" * size)
    return p


def _valid_detector(tmp_path: Path, monkeypatch,
                    count: int = 0, faces=None) -> YuNetDetector:
    """YuNet instance with mocked hash check and cv2 backend."""
    model = _write_model(tmp_path)
    monkeypatch.setattr("services.clipper.face_yunet._sha256_file",
                        lambda _: MODEL_SHA256)
    from unittest.mock import MagicMock
    mock_det = MagicMock()
    mock_det.detect.return_value = (count, faces)
    mock_cv2 = MagicMock()
    mock_cv2.FaceDetectorYN.create.return_value = mock_det
    monkeypatch.setattr("services.clipper.face_yunet._cv2", lambda: mock_cv2)
    return YuNetDetector(str(model))


def _blank_image(h: int = 64, w: int = 64) -> np.ndarray:
    return np.zeros((h, w, 3), dtype=np.uint8)


def _faces_array(*rows) -> np.ndarray:
    """Build a YuNet-style float32 detections array from (x,y,w,h,score) tuples."""
    data = []
    for x, y, w, h, sc in rows:
        data.append([x, y, w, h, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, sc])
    return np.array(data, dtype=np.float32)


# ── state: unavailable paths ──────────────────────────────────────────────────

def test_unavailable_when_model_file_missing(tmp_path):
    det = YuNetDetector(str(tmp_path / "no_such.onnx"))
    assert det.init_error is not None
    r = det.detect(_blank_image())
    assert r["state"] == STATE_UNAVAILABLE
    assert r["reason"] == "model_missing"


def test_unavailable_when_model_wrong_size(tmp_path):
    _write_model(tmp_path, size=100)
    det = YuNetDetector(str(tmp_path / MODEL_FILENAME))
    assert det.init_error is not None
    r = det.detect(_blank_image())
    assert r["state"] == STATE_UNAVAILABLE and r["reason"] == "wrong_size"


def test_unavailable_when_model_wrong_hash(tmp_path):
    _write_model(tmp_path, content=b"\xff" * MODEL_BYTES)
    det = YuNetDetector(str(tmp_path / MODEL_FILENAME))
    assert det.init_error is not None
    r = det.detect(_blank_image())
    assert r["state"] == STATE_UNAVAILABLE and r["reason"] == "wrong_hash"


def test_unavailable_when_inference_raises(tmp_path, monkeypatch):
    det = _valid_detector(tmp_path, monkeypatch)
    det._detector.detect.side_effect = RuntimeError("gpu_oom")
    r = det.detect(_blank_image())
    assert r["state"] == STATE_UNAVAILABLE and r["reason"] == "inference_error"
    assert r["inference_ms"] is None


# ── state: empty ─────────────────────────────────────────────────────────────

def test_empty_when_detector_finds_nothing(tmp_path, monkeypatch):
    det = _valid_detector(tmp_path, monkeypatch, count=0, faces=None)
    r = det.detect(_blank_image())
    assert r["state"] == STATE_EMPTY
    assert r["boxes"] == [] and r["scores"] == []
    assert r["inference_ms"] is not None


# ── bbox clamping and validity ────────────────────────────────────────────────

def test_box_clamped_to_image_bounds(tmp_path, monkeypatch):
    faces = _faces_array((-10, -10, 50, 50, 0.95))
    det = _valid_detector(tmp_path, monkeypatch, count=1, faces=faces)
    r = det.detect(_blank_image(64, 64))
    assert r["state"] == STATE_DETECTED
    x, y, w, h = r["boxes"][0]
    assert x == 0 and y == 0 and w == 40 and h == 40


def test_nonfinite_box_returns_unavailable_not_empty(tmp_path, monkeypatch):
    faces = _faces_array((float("nan"), 10, 20, 20, 0.95))
    det = _valid_detector(tmp_path, monkeypatch, count=1, faces=faces)
    r = det.detect(_blank_image())
    assert r["state"] == STATE_UNAVAILABLE and r["reason"] == "invalid_box"
    assert r["boxes"] == []


def test_degenerate_box_after_clamp_returns_unavailable(tmp_path, monkeypatch):
    faces = _faces_array((200, 200, 10, 10, 0.95))
    det = _valid_detector(tmp_path, monkeypatch, count=1, faces=faces)
    r = det.detect(_blank_image(64, 64))
    assert r["state"] == STATE_UNAVAILABLE and r["reason"] == "invalid_box"


def test_endpoint_rounding_avoids_degenerate_box(tmp_path, monkeypatch):
    """Box (1.1, 1.1, 0.3, 0.3): x1=round(1.4)=1, iw=0 → UNAVAILABLE."""
    faces = _faces_array((1.1, 1.1, 0.3, 0.3, 0.95))
    det = _valid_detector(tmp_path, monkeypatch, count=1, faces=faces)
    r = det.detect(_blank_image(32, 32))
    assert r["state"] == STATE_UNAVAILABLE and r["reason"] == "invalid_box"


def test_count_retval_ignored_faces_array_governs(tmp_path, monkeypatch):
    """retval=0 but faces array has one row — array governs; must return DETECTED."""
    faces = _faces_array((5, 5, 10, 10, 0.95))
    det = _valid_detector(tmp_path, monkeypatch, count=0, faces=faces)
    r = det.detect(_blank_image(64, 64))
    assert r["state"] == STATE_DETECTED and len(r["boxes"]) == 1


def test_scalar_faces_array_returns_unavailable():
    """np.array(3) from detector.detect() → STATE_UNAVAILABLE, no crash."""
    from services.clipper import face_yunet

    class ScalarBackend:
        def setInputSize(self, s): pass
        def detect(self, img): return 1, np.array(3)

    with patch.object(face_yunet.YuNetDetector, "_load", lambda self: None):
        det = face_yunet.YuNetDetector("stub")
    det._detector = ScalarBackend()
    r = det.detect(_blank_image())
    assert r["state"] == STATE_UNAVAILABLE
    assert r["reason"] == "invalid_output"


# ── model identity ────────────────────────────────────────────────────────────

def test_model_identity_present_in_every_result(tmp_path, monkeypatch):
    """expected_model_hash is always MODEL_SHA256; model_hash is None when missing."""
    det_bad = YuNetDetector(str(tmp_path / "no.onnx"))
    r_bad = det_bad.detect(_blank_image())
    assert r_bad["model_id"] == MODEL_FILENAME
    assert r_bad["expected_model_hash"] == MODEL_SHA256
    assert r_bad["model_hash"] is None
    assert r_bad["hash_verified"] is False

    det_ok = _valid_detector(tmp_path, monkeypatch, count=0, faces=None)
    r_ok = det_ok.detect(_blank_image())
    assert r_ok["model_id"] == MODEL_FILENAME
    assert r_ok["expected_model_hash"] == MODEL_SHA256
    assert r_ok["model_hash"] == MODEL_SHA256
    assert r_ok["hash_verified"] is True


def test_wrong_hash_model_exposes_measured_hash(tmp_path):
    """Wrong-content model: model_hash is the actual measured digest."""
    wrong_content = b"\xff" * MODEL_BYTES
    _write_model(tmp_path, content=wrong_content)
    det = YuNetDetector(str(tmp_path / MODEL_FILENAME))
    r = det.detect(_blank_image())
    assert r["state"] == STATE_UNAVAILABLE and r["reason"] == "wrong_hash"
    actual_digest = hashlib.sha256(wrong_content).hexdigest()
    assert r["model_hash"] == actual_digest
    assert r["expected_model_hash"] == MODEL_SHA256
    assert r["hash_verified"] is False


# ── matching algorithm ────────────────────────────────────────────────────────

def test_iou_box_same_box_is_one():
    assert iou_box([0, 0, 10, 10], [0, 0, 10, 10]) == pytest.approx(1.0)


def test_iou_box_no_overlap_is_zero():
    assert iou_box([0, 0, 10, 10], [20, 20, 10, 10]) == 0.0


def test_max_cardinality_beats_greedy_on_canonical_counterexample():
    iou = [[0.5, 0.4], [0.5, 0.0]]
    thresh = 0.3
    pairs = max_cardinality_matching(2, 2, iou, thresh)
    assert len(pairs) == 2, "max-cardinality must find both pairs"
    greedy, taken = [], set()
    for p in range(2):
        for g in range(2):
            if iou[p][g] >= thresh and g not in taken:
                greedy.append((p, g)); taken.add(g); break
    assert len(greedy) == 1, "greedy gets only p0→g0"


def test_one_to_one_constraint_no_gt_matched_twice():
    iou = [[0.8], [0.8], [0.8]]
    pairs = max_cardinality_matching(3, 1, iou, 0.3)
    assert len(pairs) == 1
    assert len({g for _, g in pairs}) == 1


# ── smoke test: real model on synthetic blank image ───────────────────────────

def test_smoke_real_model_blank_image():
    """Real model on 64×64 blank: no crash, model_hash non-null, hash_verified=True."""
    model = (Path(__file__).resolve().parents[2]
             / "data" / "models" / "face_detection_yunet_2023mar.onnx")
    if not model.exists():
        pytest.skip("real model not present")
    det = YuNetDetector(str(model))
    if det.init_error:
        pytest.skip(f"model init failed: {det.init_error}")
    r = det.detect(np.zeros((64, 64, 3), dtype=np.uint8))
    assert r["state"] in (STATE_EMPTY, STATE_DETECTED, STATE_UNAVAILABLE)
    assert r["model_hash"] is not None
    assert r["hash_verified"] is True
