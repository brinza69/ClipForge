"""B1 tests: benchmark CLI exit codes, annotation/GT validation, cascade
availability, uncertain-frame handling, refused-frame accounting, F1 edge
case, and model identity preservation in per-frame rows.

All tests use tmp_path; no labelled dataset is touched.
Adapter unit tests live in test_face_yunet.py.
Report-contract tests (count semantics, gt_count, type validation) live in
test_benchmark_report_contract.py.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

from services.clipper.face_yunet import (
    MODEL_FILENAME, MODEL_SHA256,
    STATE_EMPTY, STATE_UNAVAILABLE,
)
from benchmark_face_detectors import main, run_haar


# ── fixtures ──────────────────────────────────────────────────────────────────

def _write_manifest(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _write_blank_png(tmp_path: Path) -> Path:
    """Write a real 32×32 PNG (OpenCV-readable)."""
    try:
        import cv2
        img = tmp_path / "frame.png"
        cv2.imwrite(str(img), np.zeros((32, 32, 3), dtype=np.uint8))
        return img
    except ImportError:
        pytest.skip("opencv not available")


def _confirmed_frame(img_path: Path, *, width=32, height=32,
                     faces=None, annotation_state="confirmed") -> dict:
    sha = hashlib.sha256(img_path.read_bytes()).hexdigest()
    return {"id": "f1", "source": "src", "split": "dev",
            "image": str(img_path), "sha256": sha,
            "width": width, "height": height,
            "annotation_state": annotation_state,
            "faces": faces if faces is not None else [[0, 0, 10, 10]]}


class _NoopDetector:
    """YuNet mock that returns empty with no errors."""
    init_error = None
    def detect(self, bgr):
        return {"state": STATE_EMPTY, "reason": None, "boxes": [],
                "scores": [], "inference_ms": 1.0}


class _UnavailYuNet:
    """YuNet mock that always returns detector_unavailable."""
    init_error = None
    def detect(self, bgr):
        return {"state": STATE_UNAVAILABLE, "reason": "inference_error",
                "boxes": [], "scores": [], "inference_ms": None}


class _MockCascade:
    """Cascade that detects nothing (returns no boxes)."""
    def detectMultiScale(self, *a, **kw):
        return []


# ── empty manifest ────────────────────────────────────────────────────────────

def test_empty_manifest_exits_nonzero(tmp_path):
    m = _write_manifest(tmp_path, {"schema": "clipper_face_benchmark_v1", "frames": []})
    with pytest.raises(SystemExit) as exc:
        main(["--manifest", str(m), "--model", "dummy",
              "--out", str(tmp_path / "out.json")])
    assert exc.value.code != 0


def test_empty_manifest_writes_stub_output(tmp_path):
    out = tmp_path / "out.json"
    m = _write_manifest(tmp_path, {"schema": "clipper_face_benchmark_v1", "frames": []})
    with pytest.raises(SystemExit):
        main(["--manifest", str(m), "--model", "dummy", "--out", str(out)])
    assert out.exists(), "stub output must be written on early exit"
    assert json.loads(out.read_text())["total_frames"] == 0


# ── annotation validation ─────────────────────────────────────────────────────

def test_unannotated_frame_exits_nonzero_and_total_is_one(tmp_path):
    """Unannotated frame: exit nonzero AND total_frames=1 (refused row retained)."""
    img = _write_blank_png(tmp_path)
    sha = hashlib.sha256(img.read_bytes()).hexdigest()
    frame = {"id": "f1", "source": "s", "split": "dev",
             "image": str(img), "sha256": sha, "width": 32, "height": 32,
             "annotation_state": "confirmed"}  # no 'faces' key
    m = _write_manifest(tmp_path, {"schema": "clipper_face_benchmark_v1",
                                   "frames": [frame]})
    out = tmp_path / "out.json"
    with pytest.raises(SystemExit) as exc:
        main(["--manifest", str(m), "--model", "/nonexistent/model.onnx",
              "--out", str(out)])
    assert exc.value.code != 0
    data = json.loads(out.read_text())
    assert data["total_frames"] == 1
    assert data["results"][0]["refused"] is True


def test_invalid_annotation_state_exits_nonzero(tmp_path):
    """annotation_state='typo' must exit nonzero."""
    img = _write_blank_png(tmp_path)
    frame = _confirmed_frame(img, annotation_state="typo")
    m = _write_manifest(tmp_path, {"schema": "clipper_face_benchmark_v1",
                                   "frames": [frame]})
    out = tmp_path / "out.json"
    with pytest.raises(SystemExit) as exc:
        main(["--manifest", str(m), "--model", "/nonexistent/model.onnx",
              "--out", str(out)])
    assert exc.value.code != 0
    assert json.loads(out.read_text())["results"][0]["refused"] is True


def test_null_faces_refused_no_crash(tmp_path):
    """faces=null → refused row written, no crash."""
    img = _write_blank_png(tmp_path)
    frame = {**_confirmed_frame(img), "faces": None}
    m = _write_manifest(tmp_path, {"schema": "clipper_face_benchmark_v1",
                                   "frames": [frame]})
    out = tmp_path / "out.json"
    with pytest.raises(SystemExit) as exc:
        main(["--manifest", str(m), "--model", "/nonexistent.onnx",
              "--out", str(out)])
    assert exc.value.code != 0
    assert out.exists(), "report must be written even for null-faces"
    data = json.loads(out.read_text())
    assert data["total_frames"] == 1
    assert data["results"][0]["refused"] is True
    assert data["results"][0]["refused_reason"] == "faces_is_null"


# ── cascade availability ──────────────────────────────────────────────────────

def test_haar_missing_cascades_returns_unavailable():
    """Empty cascade list → detector_unavailable (not empty)."""
    from services.clipper import face_detector
    with patch.object(face_detector._LOCAL, "cascades", [], create=True):
        r = run_haar(np.zeros((32, 32, 3), dtype=np.uint8))
    assert r["state"] == "detector_unavailable"
    assert r["reason"] == "cascades_unavailable"


def test_haar_single_cascade_returns_unavailable():
    """One cascade (instead of both) → detector_unavailable."""
    from services.clipper import face_detector
    with patch.object(face_detector._LOCAL, "cascades", [_MockCascade()], create=True):
        r = run_haar(np.zeros((32, 32, 3), dtype=np.uint8))
    assert r["state"] == "detector_unavailable"
    assert r["reason"] == "cascades_unavailable"


# ── inference unavailability ──────────────────────────────────────────────────

def test_inference_unavailable_exits_nonzero_and_scores_null(tmp_path):
    """YuNet unavailable: exit 1, per-frame yunet scores null, model_id present."""
    img = _write_blank_png(tmp_path)
    frame = _confirmed_frame(img)
    m = _write_manifest(tmp_path, {"schema": "clipper_face_benchmark_v1",
                                   "frames": [frame]})
    out = tmp_path / "out.json"

    import benchmark_face_detectors as bench
    from services.clipper import face_detector
    with patch.object(face_detector._LOCAL, "cascades",
                      [_MockCascade(), _MockCascade()], create=True):
        with patch.object(bench, "YuNetDetector", lambda _, **kw: _UnavailYuNet()):
            with pytest.raises(SystemExit) as exc:
                main(["--manifest", str(m), "--model", "stub", "--out", str(out)])
    assert exc.value.code != 0
    data = json.loads(out.read_text())
    assert data["errors"], "run_errors must be non-empty"
    yunet = data["results"][0]["yunet"]
    assert yunet["scores"] is None, "per-frame scores must be null when unavailable"
    assert yunet["model_id"] == MODEL_FILENAME, "model identity must always be present"
    assert data["counts"]["yunet_unavailable"] == 1


# ── refused frames ────────────────────────────────────────────────────────────

def test_refused_frames_gt_not_counted_as_fn(tmp_path):
    """Refused frames' GT faces must not appear as FN."""
    img = _write_blank_png(tmp_path)
    frame = _confirmed_frame(img)
    frame.pop("faces")
    m = _write_manifest(tmp_path, {"schema": "clipper_face_benchmark_v1",
                                   "frames": [frame]})
    out = tmp_path / "out.json"
    with pytest.raises(SystemExit):
        main(["--manifest", str(m), "--model", "/nonexistent/model.onnx",
              "--out", str(out)])
    data = json.loads(out.read_text())
    for key in ("haar_iou3", "yunet_iou3"):
        assert data["totals"][key]["fn"] == 0, f"{key}: FN must be 0 for refused frames"


def test_error_frame_gt_in_unscored_not_fn(tmp_path):
    """Hash-mismatch frame: GT goes to unscored_gt_faces, fn=0, output written."""
    img = tmp_path / "frame.png"
    img.write_bytes(b"FAKEPNG")
    frame = {"id": "f1", "source": "src", "split": "dev",
             "image": str(img), "sha256": "a" * 64,  # wrong hash
             "width": 32, "height": 32,
             "annotation_state": "confirmed", "faces": [[0, 0, 10, 10]]}
    m = _write_manifest(tmp_path, {"schema": "clipper_face_benchmark_v1",
                                   "frames": [frame]})
    out = tmp_path / "out.json"
    with pytest.raises(SystemExit) as exc:
        main(["--manifest", str(m), "--model", "/nonexistent/model.onnx",
              "--out", str(out)])
    assert exc.value.code != 0
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["results"][0]["error"] is not None
    for key in ("haar_iou3", "yunet_iou3"):
        assert data["totals"][key]["fn"] == 0, f"{key}: FN must be 0 (error frame)"
        assert data["totals"][key]["unscored_gt_faces"] == 1


# ── uncertain-frame handling ──────────────────────────────────────────────────

def test_mixed_confirmed_uncertain_exits_two(tmp_path):
    """Confirmed + uncertain frames with no errors → exit 2 (not 0)."""
    img = _write_blank_png(tmp_path)
    confirmed = _confirmed_frame(img)
    uncertain = {**_confirmed_frame(img, annotation_state="uncertain"), "id": "f2"}
    m = _write_manifest(tmp_path, {"schema": "clipper_face_benchmark_v1",
                                   "frames": [confirmed, uncertain]})
    out = tmp_path / "out.json"

    import benchmark_face_detectors as bench
    from services.clipper import face_detector
    with patch.object(face_detector._LOCAL, "cascades",
                      [_MockCascade(), _MockCascade()], create=True):
        with patch.object(bench, "YuNetDetector", lambda _, **kw: _NoopDetector()):
            with pytest.raises(SystemExit) as exc:
                main(["--manifest", str(m), "--model", "stub", "--out", str(out)])
    assert exc.value.code == 2, "exit must be 2 when uncertain frames remain"
    data = json.loads(out.read_text())
    assert data["counts"]["confirmed"] == 1
    assert data["counts"]["uncertain"] == 1
    assert data["counts"]["image_errors"] == 0


def test_uncertain_unreadable_exits_one(tmp_path):
    """Uncertain frame with unreadable image → error → exit 1, not 2."""
    frame = {"id": "f1", "source": "s", "split": "dev",
             "annotation_state": "uncertain",
             "image": str(tmp_path / "does_not_exist.png"),
             "sha256": "a" * 64, "width": 32, "height": 32, "faces": []}
    m = _write_manifest(tmp_path, {"schema": "clipper_face_benchmark_v1",
                                   "frames": [frame]})
    out = tmp_path / "out.json"
    with pytest.raises(SystemExit) as exc:
        main(["--manifest", str(m), "--model", "/nonexistent.onnx",
              "--out", str(out)])
    assert exc.value.code == 1, "unreadable image on uncertain frame must be exit 1"
    data = json.loads(out.read_text())
    row = data["results"][0]
    assert row["uncertain"] is True
    assert row["error"] is not None


# ── F1 edge case ──────────────────────────────────────────────────────────────

def test_f1_zero_not_null_when_denominator_nonzero(tmp_path):
    """tp=0 fp=0 fn>0 → F1 must be 0.0 not None."""
    img = _write_blank_png(tmp_path)
    frame = _confirmed_frame(img, faces=[[0, 0, 10, 10]])
    m = _write_manifest(tmp_path, {"schema": "clipper_face_benchmark_v1",
                                   "frames": [frame]})
    out = tmp_path / "out.json"

    import benchmark_face_detectors as bench
    from services.clipper import face_detector
    with patch.object(face_detector._LOCAL, "cascades",
                      [_MockCascade(), _MockCascade()], create=True):
        with patch.object(bench, "YuNetDetector", lambda _, **kw: _NoopDetector()):
            with pytest.raises(SystemExit) as exc:
                main(["--manifest", str(m), "--model", "stub", "--out", str(out)])
    data = json.loads(out.read_text())
    yunet = data["totals"]["yunet_iou3"]
    assert yunet["fn"] == 1
    assert yunet["f1"] == 0.0


# ── model identity in rows ────────────────────────────────────────────────────

def test_yunet_model_identity_always_present_in_row(tmp_path):
    """model_id and expected_model_hash must appear in per-frame row even with mock."""
    img = _write_blank_png(tmp_path)
    frame = _confirmed_frame(img)
    m = _write_manifest(tmp_path, {"schema": "clipper_face_benchmark_v1",
                                   "frames": [frame]})
    out = tmp_path / "out.json"

    import benchmark_face_detectors as bench
    from services.clipper import face_detector
    with patch.object(face_detector._LOCAL, "cascades",
                      [_MockCascade(), _MockCascade()], create=True):
        with patch.object(bench, "YuNetDetector", lambda _, **kw: _NoopDetector()):
            with pytest.raises(SystemExit):
                main(["--manifest", str(m), "--model", "stub", "--out", str(out)])
    data = json.loads(out.read_text())
    yunet = data["results"][0]["yunet"]
    assert yunet["model_id"] == MODEL_FILENAME
    assert yunet["expected_model_hash"] == MODEL_SHA256
    # This detector double never supplied settings; they are unknown.
    assert yunet["score_thresh"] is None
    assert yunet["nms_thresh"] is None
    assert yunet["top_k"] is None


def test_frame_identity_fields_preserved_in_result(tmp_path):
    """image, sha256_declared, sha256_measured, width, height in every result row."""
    img = _write_blank_png(tmp_path)
    sha = hashlib.sha256(img.read_bytes()).hexdigest()
    frame = _confirmed_frame(img)
    m = _write_manifest(tmp_path, {"schema": "clipper_face_benchmark_v1",
                                   "frames": [frame]})
    out = tmp_path / "out.json"

    import benchmark_face_detectors as bench
    from services.clipper import face_detector
    with patch.object(face_detector._LOCAL, "cascades",
                      [_MockCascade(), _MockCascade()], create=True):
        with patch.object(bench, "YuNetDetector", lambda _, **kw: _NoopDetector()):
            with pytest.raises(SystemExit):
                main(["--manifest", str(m), "--model", "stub", "--out", str(out)])
    row = json.loads(out.read_text())["results"][0]
    assert row["image"] == str(img)
    assert row["sha256_declared"] == sha
    assert row["sha256_measured"] == sha
    assert row["width"] == 32
    assert row["height"] == 32
