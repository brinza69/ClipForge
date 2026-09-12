"""B2 tests: optional detector injection into face_presence.

Tests confirm:
- When a detector is supplied, Haar cascades are NOT loaded.
- Detector metadata (profile, score_thresh, etc.) is preserved in detector_meta.
- Unavailable inference still carries decoded address (frame_index, decoded_t).
- Decoding failure (STATE_UNREADABLE) is distinct from inference failure.
- Default Haar behaviour (no detector kwarg) is unchanged.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from services.clipper import face_detector
from services.clipper.face_detector import (
    STATE_DETECTED, STATE_EMPTY, STATE_UNAVAILABLE, STATE_UNREADABLE,
    face_presence,
)


# ── mock helpers ──────────────────────────────────────────────────────────────

class _EmptyDetector:
    """Duck-typed detector that always returns STATE_EMPTY."""
    def detect(self, bgr: Any) -> dict[str, Any]:
        return {"state": STATE_EMPTY, "reason": None, "boxes": [],
                "scores": [], "model_id": "test_model",
                "score_thresh": 0.75, "profile": "small_faces_v1",
                "inference_ms": 2.5}


class _DetectedDetector:
    """Returns one detected box."""
    def detect(self, bgr: Any) -> dict[str, Any]:
        h, w = bgr.shape[:2]
        return {"state": STATE_DETECTED, "reason": None, "boxes": [[1, 1, 5, 5]],
                "scores": [0.98], "model_id": "test_model",
                "score_thresh": 0.9, "profile": "native_v1", "inference_ms": 1.0}


class _UnavailDetector:
    """Always returns STATE_UNAVAILABLE."""
    def detect(self, bgr: Any) -> dict[str, Any]:
        return {"state": STATE_UNAVAILABLE, "reason": "inference_error",
                "boxes": [], "scores": [], "model_id": "test_model",
                "score_thresh": 0.9, "profile": "native_v1", "inference_ms": None}


def _make_video_mp4(tmp_path: Path) -> Path:
    """Create a minimal valid MP4 that cv2.VideoCapture can open."""
    try:
        import cv2
    except ImportError:
        pytest.skip("opencv not available")
    out = tmp_path / "probe.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    vw = cv2.VideoWriter(str(out), fourcc, 1.0, (32, 32))
    for _ in range(3):
        vw.write(np.zeros((32, 32, 3), dtype=np.uint8))
    vw.release()
    if not out.exists() or out.stat().st_size < 100:
        pytest.skip("VideoWriter did not produce a valid file")
    return out


# ── cascade not loaded when detector supplied ─────────────────────────────────

def test_haar_cascades_not_checked_when_detector_supplied(tmp_path):
    """face_cascades() must not be called at all when a detector is passed."""
    proxy = _make_video_mp4(tmp_path)
    det = _EmptyDetector()
    with patch.object(face_detector, "face_cascades") as mock_cascades:
        mock_cascades.return_value = []  # empty — would block Haar path
        results = face_presence(str(proxy), [0.0], detector=det)
    mock_cascades.assert_not_called()
    # Even with an empty cascade list, the detector path must succeed
    assert len(results) == 1
    assert results[0]["state"] in (STATE_EMPTY, STATE_DETECTED, STATE_UNAVAILABLE)


def test_no_detector_still_uses_haar(tmp_path, monkeypatch):
    """Default path (no detector) uses Haar — regression guard."""
    proxy = _make_video_mp4(tmp_path)
    mock_cascade = MagicMock()
    mock_cascade.detectMultiScale.return_value = []
    monkeypatch.setattr(face_detector, "_FACE_CASCADES",
                        [mock_cascade, mock_cascade])
    results = face_presence(str(proxy), [0.0])
    # Haar was consulted (detectMultiScale called at least once)
    assert mock_cascade.detectMultiScale.call_count >= 1
    assert results[0]["state"] in (STATE_EMPTY, STATE_DETECTED)


# ── detector result propagated ────────────────────────────────────────────────

def test_detector_boxes_appear_in_result(tmp_path):
    proxy = _make_video_mp4(tmp_path)
    results = face_presence(str(proxy), [0.0], detector=_DetectedDetector())
    assert len(results) == 1
    r = results[0]
    assert r["state"] == STATE_DETECTED
    assert r["boxes"] == [[1, 1, 5, 5]]


def test_injected_exception_preserves_each_decoded_observation(tmp_path):
    class BrokenDetector:
        def detect(self, frame):
            raise RuntimeError("backend failed")
    proxy = _make_video_mp4(tmp_path)
    rows = face_presence(str(proxy), [0., 1.], detector=BrokenDetector())
    assert len(rows) == 2
    for index, row in enumerate(rows):
        assert row["state"] == STATE_UNAVAILABLE
        assert row["boxes"] == []
        assert row["frame_index"] == index
        assert row["decoded_t"] == float(index)
        assert row["detector_meta"]["detail"] == "backend failed"


def test_detector_empty_result_propagated(tmp_path):
    proxy = _make_video_mp4(tmp_path)
    results = face_presence(str(proxy), [0.0], detector=_EmptyDetector())
    assert results[0]["state"] == STATE_EMPTY
    assert results[0]["boxes"] == []


# ── detector_meta preserved alongside decoded address ─────────────────────────

def test_detector_meta_present_when_detector_supplied(tmp_path):
    proxy = _make_video_mp4(tmp_path)
    results = face_presence(str(proxy), [0.0], detector=_EmptyDetector())
    r = results[0]
    assert "detector_meta" in r, "detector_meta key must be present"
    meta = r["detector_meta"]
    assert meta["model_id"] == "test_model"
    assert meta["score_thresh"] == 0.75
    assert meta["profile"] == "small_faces_v1"
    assert meta["inference_ms"] == pytest.approx(2.5)


def test_detector_meta_excludes_promoted_fields(tmp_path):
    """state, reason, boxes are at top level; they must not duplicate in detector_meta."""
    proxy = _make_video_mp4(tmp_path)
    results = face_presence(str(proxy), [0.0], detector=_DetectedDetector())
    meta = results[0]["detector_meta"]
    for promoted in ("state", "reason", "boxes"):
        assert promoted not in meta, f"{promoted!r} must not appear in detector_meta"


def test_no_detector_no_detector_meta_key(tmp_path, monkeypatch):
    """Haar path must not add detector_meta key."""
    proxy = _make_video_mp4(tmp_path)
    mock_cascade = MagicMock()
    mock_cascade.detectMultiScale.return_value = []
    monkeypatch.setattr(face_detector, "_FACE_CASCADES",
                        [mock_cascade, mock_cascade])
    results = face_presence(str(proxy), [0.0])
    assert "detector_meta" not in results[0]


# ── unavailable inference carries decoded address ─────────────────────────────

def test_unavailable_inference_carries_decoded_address(tmp_path):
    """STATE_UNAVAILABLE from detector: frame_index and decoded_t must be present."""
    proxy = _make_video_mp4(tmp_path)
    results = face_presence(str(proxy), [0.0], detector=_UnavailDetector())
    r = results[0]
    assert r["state"] == STATE_UNAVAILABLE
    # frame was successfully decoded; decoded address must be populated
    assert r["frame_index"] is not None or r["decoded_t"] is not None, (
        "at least one decoded-address field must be non-null when frame was read")
    # detector_meta must be present too
    assert "detector_meta" in r


def test_unavailable_inference_carries_reason(tmp_path):
    proxy = _make_video_mp4(tmp_path)
    results = face_presence(str(proxy), [0.0], detector=_UnavailDetector())
    assert results[0]["reason"] == "inference_error"


# ── decoding failure distinct from detector failure ───────────────────────────

def test_missing_file_is_unreadable_not_unavailable():
    """Missing proxy → STATE_UNREADABLE, detector never called."""
    called = []

    class _TrackingDetector:
        def detect(self, bgr):
            called.append(True)
            return {"state": STATE_EMPTY, "reason": None, "boxes": []}

    results = face_presence("/nonexistent/no.mp4", [0.0], detector=_TrackingDetector())
    assert results[0]["state"] == STATE_UNREADABLE
    assert not called, "detector must not be called when file is missing"


def test_decoding_failure_has_no_detector_meta(tmp_path):
    """On STATE_UNREADABLE, detector_meta key must be absent (decoder never ran)."""
    results = face_presence("/nonexistent/no.mp4", [0.0], detector=_EmptyDetector())
    assert "detector_meta" not in results[0]


# ── same decoded-pixel address convention ─────────────────────────────────────

def test_decoded_address_fields_present_in_both_paths(tmp_path, monkeypatch):
    """frame_index and decoded_t exist in entries from both Haar and injected detector."""
    proxy = _make_video_mp4(tmp_path)

    # Haar path
    mock_cascade = MagicMock()
    mock_cascade.detectMultiScale.return_value = []
    monkeypatch.setattr(face_detector, "_FACE_CASCADES",
                        [mock_cascade, mock_cascade])
    haar_results = face_presence(str(proxy), [0.0])

    # detector path
    det_results = face_presence(str(proxy), [0.0], detector=_EmptyDetector())

    for label, results in (("haar", haar_results), ("detector", det_results)):
        r = results[0]
        assert "frame_index" in r, f"{label}: frame_index key missing"
        assert "decoded_t" in r, f"{label}: decoded_t key missing"
        assert "clock" in r, f"{label}: clock key missing"
        assert "address_basis" in r, f"{label}: address_basis key missing"
