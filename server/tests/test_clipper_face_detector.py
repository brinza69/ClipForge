"""Tests for face_detector.face_presence — four distinct observation states
plus a real-video seek test that verifies frame_index and decoded_t against
decoded pixels, not against the same formula used in the implementation.
"""
from __future__ import annotations

import math
from unittest.mock import patch

import cv2
import numpy as np
import pytest

from services.clipper.face_detector import (
    STATE_DETECTED, STATE_EMPTY, STATE_UNAVAILABLE, STATE_UNREADABLE,
    face_presence,
)
from services.clipper.ffmpeg_tools import ffmpeg_bin, run
from services.clipper import face_detector

# Pixel-step used in the clock video fixture: frame N has all pixels = N*STEP.
# 10 is large enough to survive mp4v lossy encoding (±4 levels) unambiguously.
_CLOCK_STEP = 10
_CLOCK_FPS = 5
_CLOCK_FRAMES = 20
_CLOCK_W, _CLOCK_H = 64, 36


def _make_clock_video(path) -> None:
    """Video where frame N has mean luma ≈ N * _CLOCK_STEP.

    Uses cv2.VideoWriter (mp4v) so _CLOCK_STEP=10 survives compression.
    5fps: frame grid is 0.0, 0.2, 0.4 … so 0.15 / 0.35 / 0.85 are non-grid.
    """
    w = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"),
        _CLOCK_FPS, (_CLOCK_W, _CLOCK_H), isColor=True)
    for i in range(_CLOCK_FRAMES):
        w.write(np.full((_CLOCK_H, _CLOCK_W, 3), i * _CLOCK_STEP, dtype=np.uint8))
    w.release()


# ── four-state contract ───────────────────────────────────────────────────────

def test_state_detector_unavailable_when_cascades_missing(tmp_path):
    """No cascades → every sample is detector_unavailable, boxes and index None."""
    video = tmp_path / "dummy.mp4"
    run([ffmpeg_bin(), "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=black:s=64x36:r=10:d=1",
         "-c:v", "libx264", str(video)], what="dummy fixture")
    with patch("services.clipper.face_detector.face_cascades", return_value=[]):
        result = face_presence(str(video), [0.1, 0.5])
    assert all(s["state"] == STATE_UNAVAILABLE for s in result)
    assert all(s["boxes"] == [] for s in result)
    assert all(s["frame_index"] is None and s["decoded_t"] is None for s in result)


def test_state_unreadable_when_file_missing():
    """Missing proxy is a media failure, not a missing detector."""
    result = face_presence("/nonexistent/proxy.mp4", [0.0, 1.0])
    assert len(result) == 2
    assert all(s["state"] == STATE_UNREADABLE and s["reason"] == "file_missing"
               for s in result)
    assert all(s["frame_index"] is None and s["decoded_t"] is None for s in result)


def test_state_unreadable_when_seek_past_eof(tmp_path):
    """Seek past EOF: cap.read() returns False → state=unreadable, not empty."""
    video = tmp_path / "dummy.mp4"
    run([ffmpeg_bin(), "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=black:s=64x36:r=10:d=1",
         "-c:v", "libx264", str(video)], what="dummy fixture")
    result = face_presence(str(video), [9999.0])
    assert len(result) == 1
    s = result[0]
    assert s["state"] == STATE_UNREADABLE
    assert s["boxes"] == [] and s["frame_index"] is None and s["decoded_t"] is None


def test_state_empty_for_valid_frame_with_no_faces(tmp_path):
    """Solid-colour frame where no face fires → state=empty (not unreadable)."""
    video = tmp_path / "blank.mp4"
    run([ffmpeg_bin(), "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=black:s=64x36:r=10:d=1",
         "-c:v", "libx264", str(video)], what="blank fixture")
    result = face_presence(str(video), [0.5])
    assert len(result) == 1
    s = result[0]
    assert s["state"] == STATE_EMPTY
    assert s["boxes"] == []
    assert s["frame_index"] is not None and s["decoded_t"] is not None
    assert math.isfinite(s["decoded_t"]) and s["decoded_t"] >= 0


def test_state_detected_carries_boxes(tmp_path):
    """When detect_faces returns boxes, state is detected and boxes are forwarded."""
    video = tmp_path / "blank.mp4"
    run([ffmpeg_bin(), "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=black:s=64x36:r=10:d=1",
         "-c:v", "libx264", str(video)], what="blank fixture")
    fake_box = [5, 5, 20, 20]
    with patch("services.clipper.face_detector.detect_faces", return_value=[fake_box]):
        result = face_presence(str(video), [0.1])
    s = result[0]
    assert s["state"] == STATE_DETECTED
    assert s["boxes"] == [fake_box]
    assert s["frame_index"] is not None


# ── real-video seek: pixel oracle checks both frame_index and decoded_t ───────

def test_frame_index_and_decoded_t_verified_against_pixels(tmp_path):
    """Identify the actual pixels given to detection; never re-seek a reported index.

    The fixture's 200ms frame period is exact. 1ms allows timestamp quantization,
    but rejects reading the previous frame's PTS (the original bug).
    """
    video = tmp_path / "clock.mp4"
    _make_clock_video(video)

    # 0.15 / 0.35 / 0.85 are between frame boundaries at 5fps (grid: 0.0, 0.2, …)
    seeks = [0.15, 0.35, 0.85, 2.71, 0.61]
    pixel_indices = []

    def identify(grey):
        pixel_indices.append(round(float(grey.mean()) / _CLOCK_STEP))
        return []

    with patch("services.clipper.face_detector.detect_faces", side_effect=identify):
        samples = face_presence(str(video), seeks)
    assert len(pixel_indices) == len(samples) == len(seeks)
    assert len(set(pixel_indices)) == len(seeks)
    for sample, fi in zip(samples, pixel_indices):
        assert sample["state"] == STATE_EMPTY
        assert sample["frame_index"] == fi
        assert sample["decoded_t"] == pytest.approx(fi / _CLOCK_FPS, abs=.001)
        assert sample["decoded_space"] == "analysed_file"


# ── state routing in caption_faces ───────────────────────────────────────────

def test_legacy_samples_without_state_field_go_to_legacy_counter():
    """Legacy state is unknown whether boxes are present or absent."""
    from services.clipper.caption_faces import place

    # start=100, shot t0=0 t1=5 → shot covers source time [100, 105).
    # Legacy sample at t=101.5: t - start = 1.5, which is in [0, 5).
    dyn = {
        "style": {"push_amount": 0}, "shots": [
            {"t0": 0, "t1": 5, "composition": "crop", "anchor": [540, 960],
             "rect": {"x": 0, "y": 0, "w": 1080, "h": 1920}}],
        "_face_space": {"width": 108, "height": 192, "clock": "source_requested"},
        "_review_faces": [
            {"t": 100.5, "boxes": [[20, 40, 60, 93.5]]},   # legacy WITH boxes
            {"t": 101.5, "boxes": []},                      # legacy NO state key
        ],
    }
    report = place({}, dyn, start=100, src_w=1080, src_h=1920, current_y=0.51, keep_out=[])
    assert report["legacy_unknown_samples"] == 2
    assert report["empty_samples"] == 0


def test_unreadable_and_unavailable_counted_separately_from_empty():
    """unreadable and detector_unavailable states each get their own counter."""
    from services.clipper.caption_faces import place

    dyn = {
        "style": {"push_amount": 0}, "shots": [
            {"t0": 0, "t1": 5, "composition": "crop", "anchor": [540, 960],
             "rect": {"x": 0, "y": 0, "w": 1080, "h": 1920}}],
        "_face_space": {"width": 108, "height": 192, "clock": "source_requested"},
        "_review_faces": [
            {"t": 100.5, "boxes": [[20, 40, 60, 93.5]], "state": STATE_DETECTED,
             "frame_index": 5, "decoded_t": 0.5},
            {"t": 101.0, "boxes": [], "state": STATE_UNREADABLE,
             "frame_index": None, "decoded_t": None},
            {"t": 101.5, "boxes": [], "state": STATE_UNAVAILABLE,
             "frame_index": None, "decoded_t": None},
            {"t": 102.0, "boxes": [], "state": STATE_EMPTY,
             "frame_index": 10, "decoded_t": 2.0},
        ],
    }
    report = place({}, dyn, start=100, src_w=1080, src_h=1920, current_y=0.51, keep_out=[])
    assert report["empty_samples"] == 1
    assert report["unreadable_samples"] == 1
    assert report["detector_unavailable_samples"] == 1
    assert report["legacy_unknown_samples"] == 0
    assert not report["coverage_complete"]


@pytest.mark.parametrize("field,delta", [("decoded_t", -.2), ("frame_index", 1)])
def test_pixel_oracle_rejects_a_one_frame_lie(tmp_path, monkeypatch, field, delta):
    real = face_presence

    def wrong(*args):
        samples = real(*args)
        for s in samples:
            s[field] += delta
        return samples

    monkeypatch.setitem(globals(), "face_presence", wrong)
    with pytest.raises(AssertionError):
        test_frame_index_and_decoded_t_verified_against_pixels(tmp_path)


class MetadataCapture:
    def __init__(self, index=2., msec=200., *, opened=True, seek=True):
        self.index, self.msec = index, msec
        self.opened, self.seek = opened, seek
        self.released = False

    def isOpened(self):
        return self.opened

    def getBackendName(self):
        return "FFMPEG"

    def set(self, *args):
        return self.seek

    def get(self, prop):
        return self.index if prop == cv2.CAP_PROP_POS_FRAMES else self.msec

    def read(self):
        return True, np.zeros((36, 64, 3), np.uint8)

    def release(self):
        self.released = True


@pytest.mark.parametrize("value", [float('nan'), float('inf'), -1., 2.5])
def test_invalid_index_keeps_the_frame_but_never_invents_its_address(tmp_path, monkeypatch, value):
    cap = MetadataCapture(index=value)
    monkeypatch.setattr(cv2, "VideoCapture", lambda _: cap)
    sample = face_presence(str(tmp_path), [.2])[0]
    assert sample["state"] == STATE_EMPTY and sample["frame_index"] is None
    assert sample["decoded_t"] == .2 and cap.released


@pytest.mark.parametrize("value", [float('nan'), float('inf'), -1.])
def test_invalid_pts_is_not_reconstructed_from_index(tmp_path, monkeypatch, value):
    monkeypatch.setattr(cv2, "VideoCapture", lambda _: MetadataCapture(msec=value))
    sample = face_presence(str(tmp_path), [.2])[0]
    assert sample["state"] == STATE_EMPTY and sample["frame_index"] == 2
    assert sample["decoded_t"] is None


@pytest.mark.parametrize("options,reason", [
    ({"opened": False}, "capture_not_open"), ({"seek": False}, "seek_failed")])
def test_capture_failure_cannot_become_detection_absence(tmp_path, monkeypatch, options, reason):
    cap = MetadataCapture(**options)
    monkeypatch.setattr(cv2, "VideoCapture", lambda _: cap)
    samples = face_presence(str(tmp_path), [.2, .5])
    assert len(samples) == 2 and cap.released
    assert all(s["state"] == STATE_UNREADABLE and s["reason"] == reason for s in samples)
    assert all(s["frame_index"] is None and s["decoded_t"] is None for s in samples)


def test_detector_error_preserves_decoded_address_and_later_reads(tmp_path, monkeypatch):
    monkeypatch.setattr(cv2, "VideoCapture", lambda _: MetadataCapture())
    with patch.object(face_detector, "detect_faces", side_effect=[cv2.error("failed"), []]):
        first, second = face_presence(str(tmp_path), [.2, .5])
    assert first["state"] == STATE_UNAVAILABLE and first["reason"] == "detection_error"
    assert first["frame_index"] == 2 and first["decoded_t"] == .2
    assert second["state"] == STATE_EMPTY


def test_opencv_unavailable_is_distinct_from_empty(monkeypatch):
    monkeypatch.setattr(face_detector, "_cv2", lambda: None)
    samples = face_presence("irrelevant.mp4", [.2, .5])
    assert len(samples) == 2
    assert all(s["state"] == STATE_UNAVAILABLE and s["reason"] == "opencv_unavailable"
               for s in samples)


def test_unverified_backend_cannot_certify_even_plausible_metadata(tmp_path, monkeypatch):
    cap = MetadataCapture()
    monkeypatch.setattr(cap, "getBackendName", lambda: "OTHER_BACKEND")
    monkeypatch.setattr(cv2, "VideoCapture", lambda _: cap)
    sample = face_presence(str(tmp_path), [.2])[0]
    assert sample["state"] == STATE_EMPTY
    assert sample["address_basis"] is None
    assert sample["decoded_t"] is None and sample["frame_index"] is None


@pytest.mark.parametrize("failed_property", [cv2.CAP_PROP_POS_FRAMES, cv2.CAP_PROP_POS_MSEC])
def test_unreadable_metadata_does_not_discard_a_readable_frame(tmp_path, monkeypatch, failed_property):
    cap = MetadataCapture()
    original = cap.get

    def get(prop):
        if prop == failed_property:
            raise cv2.error("metadata unavailable")
        return original(prop)

    monkeypatch.setattr(cap, "get", get)
    monkeypatch.setattr(cv2, "VideoCapture", lambda _: cap)
    sample = face_presence(str(tmp_path), [.2])[0]
    assert sample["state"] == STATE_EMPTY
    assert sample["frame_index"] == (None if failed_property == cv2.CAP_PROP_POS_FRAMES else 2)
    assert sample["decoded_t"] == (None if failed_property == cv2.CAP_PROP_POS_MSEC else .2)
