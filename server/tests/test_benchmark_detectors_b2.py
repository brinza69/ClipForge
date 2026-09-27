"""B2 tests: --profile CLI flag and actual-instance settings in benchmark output.

Tests confirm:
- --profile native_v1 (default): benchmark output shows score_thresh=0.9.
- --profile small_faces_v1: benchmark output shows score_thresh=0.75, not 0.9.
- Benchmark report uses detect() result for settings, not module constants.
- Default profile is native_v1.
- profile and scale fields appear in per-frame yunet row.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

from services.clipper.face_yunet import (
    MODEL_FILENAME, MODEL_SHA256, SCORE_THRESH, STATE_EMPTY,
)
from benchmark_face_detectors import main


# ── shared fixtures ───────────────────────────────────────────────────────────

def _write_manifest(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _write_blank_png(tmp_path: Path) -> Path:
    try:
        import cv2
        img = tmp_path / "frame.png"
        cv2.imwrite(str(img), np.zeros((32, 32, 3), dtype=np.uint8))
        return img
    except ImportError:
        pytest.skip("opencv not available")


def _confirmed_frame(img_path: Path, *, width=32, height=32,
                     faces=None) -> dict:
    sha = hashlib.sha256(img_path.read_bytes()).hexdigest()
    return {"id": "f1", "source": "src", "split": "dev",
            "image": str(img_path), "sha256": sha,
            "width": width, "height": height,
            "annotation_state": "confirmed",
            "faces": faces if faces is not None else [[0, 0, 10, 10]]}


class _MockCascade:
    def detectMultiScale(self, *a, **kw):
        return []


def _make_yunet_mock(score_thresh: float = 0.9, profile: str = "native_v1"):
    """Return a mock detector whose detect() includes actual instance settings."""
    class _MockYuNet:
        init_error = None
        _measured_hash = MODEL_SHA256

        def detect(self, bgr: Any) -> dict:
            return {
                "state": STATE_EMPTY, "reason": None, "boxes": [],
                "scores": [], "inference_ms": 1.0,
                "model_id": MODEL_FILENAME,
                "expected_model_hash": MODEL_SHA256,
                "model_hash": MODEL_SHA256,
                "hash_verified": True,
                "profile": profile,
                "score_thresh": score_thresh,
                "nms_thresh": 0.3,
                "top_k": 5000,
                "scale": 2.0 if profile == "small_faces_v1" else 1.0,
                "raw_boxes_model_space": None,
            }
    return _MockYuNet()


# ── default profile is native_v1 ─────────────────────────────────────────────

def test_default_profile_is_native_v1(tmp_path):
    img = _write_blank_png(tmp_path)
    frame = _confirmed_frame(img)
    m = _write_manifest(tmp_path, {"schema": "clipper_face_benchmark_v1",
                                   "frames": [frame]})
    out = tmp_path / "out.json"

    import benchmark_face_detectors as bench
    from services.clipper import face_detector
    with patch.object(face_detector._LOCAL, "cascades",
                      [_MockCascade(), _MockCascade()], create=True):
        with patch.object(bench, "YuNetDetector",
                          lambda path, profile="native_v1": _make_yunet_mock(
                              score_thresh=0.9, profile=profile)):
            with pytest.raises(SystemExit) as exc:
                main(["--manifest", str(m), "--model", "stub", "--out", str(out)])
    data = json.loads(out.read_text())
    yunet = data["results"][0]["yunet"]
    assert yunet["profile"] == "native_v1"
    assert yunet["score_thresh"] == 0.9


# ── --profile native_v1 reports 0.9 ──────────────────────────────────────────

def test_profile_native_v1_score_thresh_09(tmp_path):
    img = _write_blank_png(tmp_path)
    frame = _confirmed_frame(img)
    m = _write_manifest(tmp_path, {"schema": "clipper_face_benchmark_v1",
                                   "frames": [frame]})
    out = tmp_path / "out.json"

    import benchmark_face_detectors as bench
    from services.clipper import face_detector
    with patch.object(face_detector._LOCAL, "cascades",
                      [_MockCascade(), _MockCascade()], create=True):
        with patch.object(bench, "YuNetDetector",
                          lambda path, profile="native_v1": _make_yunet_mock(
                              score_thresh=0.9, profile=profile)):
            with pytest.raises(SystemExit):
                main(["--manifest", str(m), "--model", "stub", "--out", str(out),
                      "--profile", "native_v1"])
    data = json.loads(out.read_text())
    yunet = data["results"][0]["yunet"]
    assert yunet["score_thresh"] == 0.9
    assert yunet["profile"] == "native_v1"


# ── --profile small_faces_v1 reports 0.75, not module constant 0.9 ───────────

def test_profile_small_faces_v1_score_thresh_075(tmp_path):
    img = _write_blank_png(tmp_path)
    frame = _confirmed_frame(img)
    m = _write_manifest(tmp_path, {"schema": "clipper_face_benchmark_v1",
                                   "frames": [frame]})
    out = tmp_path / "out.json"

    import benchmark_face_detectors as bench
    from services.clipper import face_detector
    with patch.object(face_detector._LOCAL, "cascades",
                      [_MockCascade(), _MockCascade()], create=True):
        with patch.object(bench, "YuNetDetector",
                          lambda path, profile="native_v1": _make_yunet_mock(
                              score_thresh=0.75 if profile == "small_faces_v1" else 0.9,
                              profile=profile)):
            with pytest.raises(SystemExit):
                main(["--manifest", str(m), "--model", "stub", "--out", str(out),
                      "--profile", "small_faces_v1"])
    data = json.loads(out.read_text())
    yunet = data["results"][0]["yunet"]
    assert yunet["score_thresh"] == 0.75, (
        "small_faces_v1 must report 0.75, not the module constant 0.9")
    assert yunet["score_thresh"] != SCORE_THRESH
    assert yunet["profile"] == "small_faces_v1"


# ── scale field in yunet row ──────────────────────────────────────────────────

def test_native_v1_scale_is_10(tmp_path):
    img = _write_blank_png(tmp_path)
    frame = _confirmed_frame(img)
    m = _write_manifest(tmp_path, {"schema": "clipper_face_benchmark_v1",
                                   "frames": [frame]})
    out = tmp_path / "out.json"

    import benchmark_face_detectors as bench
    from services.clipper import face_detector
    with patch.object(face_detector._LOCAL, "cascades",
                      [_MockCascade(), _MockCascade()], create=True):
        with patch.object(bench, "YuNetDetector",
                          lambda path, profile="native_v1": _make_yunet_mock(
                              score_thresh=0.9, profile=profile)):
            with pytest.raises(SystemExit):
                main(["--manifest", str(m), "--model", "stub", "--out", str(out),
                      "--profile", "native_v1"])
    data = json.loads(out.read_text())
    assert data["results"][0]["yunet"]["scale"] == 1.0


def test_small_faces_v1_scale_is_20(tmp_path):
    img = _write_blank_png(tmp_path)
    frame = _confirmed_frame(img)
    m = _write_manifest(tmp_path, {"schema": "clipper_face_benchmark_v1",
                                   "frames": [frame]})
    out = tmp_path / "out.json"

    import benchmark_face_detectors as bench
    from services.clipper import face_detector
    with patch.object(face_detector._LOCAL, "cascades",
                      [_MockCascade(), _MockCascade()], create=True):
        with patch.object(bench, "YuNetDetector",
                          lambda path, profile="native_v1": _make_yunet_mock(
                              score_thresh=0.75 if profile == "small_faces_v1" else 0.9,
                              profile=profile)):
            with pytest.raises(SystemExit):
                main(["--manifest", str(m), "--model", "stub", "--out", str(out),
                      "--profile", "small_faces_v1"])
    data = json.loads(out.read_text())
    assert data["results"][0]["yunet"]["scale"] == 2.0
