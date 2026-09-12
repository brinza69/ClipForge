"""B1 report-contract tests: count semantics (inferred/scored/paired_scored/
unknown_gt_frames), gt_count field, aggregate error propagation from uncertain
frames, and field type validation (issue 2–5 fixes).

All tests use tmp_path; no labelled dataset is touched.
Core benchmark exit-code tests live in test_benchmark_detectors.py.
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

from services.clipper.face_yunet import STATE_EMPTY, STATE_UNAVAILABLE
from benchmark_face_detectors import main


@pytest.mark.parametrize("field", ["source", "annotation_state"])
def test_unhashable_invalid_field_is_a_reported_refusal(tmp_path, field):
    frame = {**_confirmed_frame(_write_blank_png(tmp_path)), field: []}
    manifest = _write_manifest(tmp_path, {"schema": "clipper_face_benchmark_v1", "frames": [frame]})
    out = tmp_path / "refused.json"
    with patch("benchmark_face_detectors.YuNetDetector", return_value=_NoopDetector()):
        with pytest.raises(SystemExit) as exc:
            main(["--manifest", str(manifest), "--model", "stub", "--out", str(out)])
    assert exc.value.code == 1
    report = json.loads(out.read_text())
    assert report["counts"]["requested"] == report["counts"]["refused"] == 1
    assert report["counts"]["unknown_gt_frames"] == 1
    assert report["counts"]["known_gt_faces"] == 0


def test_uncertain_inference_failure_has_consistent_unavailable_counts(tmp_path):
    frame = _confirmed_frame(_write_blank_png(tmp_path), annotation_state="uncertain")
    manifest = _write_manifest(tmp_path, {"schema": "clipper_face_benchmark_v1", "frames": [frame]})
    out = tmp_path / "failure.json"
    with patch("benchmark_face_detectors.YuNetDetector", return_value=_UnavailYuNet()), \
         patch("benchmark_face_detectors.run_haar", return_value=_NoopDetector().detect(None)):
        with pytest.raises(SystemExit) as exc:
            main(["--manifest", str(manifest), "--model", "stub", "--out", str(out)])
    assert exc.value.code == 1
    report = json.loads(out.read_text())
    assert report["counts"]["yunet_unavailable"] == 1
    assert report["totals"]["yunet_iou3"]["unavailable"] == 1
    assert report["by_source"]["src"]["yunet_iou3"]["unavailable"] == 1
    assert report["counts"]["unknown_gt_frames"] == 1
    assert report["counts"]["known_gt_faces"] == 0


# ── fixtures ──────────────────────────────────────────────────────────────────

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
                     faces=None, annotation_state="confirmed") -> dict:
    sha = hashlib.sha256(img_path.read_bytes()).hexdigest()
    return {"id": "f1", "source": "src", "split": "dev",
            "image": str(img_path), "sha256": sha,
            "width": width, "height": height,
            "annotation_state": annotation_state,
            "faces": faces if faces is not None else [[0, 0, 10, 10]]}


class _NoopDetector:
    init_error = None
    def detect(self, bgr):
        return {"state": STATE_EMPTY, "reason": None, "boxes": [],
                "scores": [], "inference_ms": 1.0}


class _UnavailYuNet:
    init_error = None
    def detect(self, bgr):
        return {"state": STATE_UNAVAILABLE, "reason": "inference_error",
                "boxes": [], "scores": [], "inference_ms": None}


class _MockCascade:
    def detectMultiScale(self, *a, **kw):
        return []


# ── issue 2: count semantics ──────────────────────────────────────────────────

def test_inferred_scored_paired_scored_counts(tmp_path):
    """confirmed+uncertain: each_inferred=2, each_scored=1, paired_scored=1,
    unknown_gt_frames=1; exit 2."""
    img = _write_blank_png(tmp_path)
    confirmed = _confirmed_frame(img)
    uncertain = {**_confirmed_frame(img, annotation_state="uncertain"), "id": "f2"}
    m = _write_manifest(tmp_path, {"schema": "clipper_face_benchmark_v1",
                                   "frames": [confirmed, uncertain]})
    out = tmp_path / "out.json"

    import benchmark_face_detectors as bench
    from services.clipper import face_detector
    with patch.object(face_detector, "_FACE_CASCADES",
                      [_MockCascade(), _MockCascade()]):
        with patch.object(bench, "YuNetDetector", lambda _: _NoopDetector()):
            with pytest.raises(SystemExit) as exc:
                main(["--manifest", str(m), "--model", "stub", "--out", str(out)])
    assert exc.value.code == 2
    counts = json.loads(out.read_text())["counts"]
    assert counts["haar_inferred"] == 2
    assert counts["yunet_inferred"] == 2
    assert counts["haar_scored"] == 1     # only confirmed has validated GT
    assert counts["yunet_scored"] == 1
    assert counts["paired_scored"] == 1
    assert counts["unknown_gt_frames"] == 1   # uncertain frame


def test_yunet_unavailable_counts_not_paired(tmp_path):
    """YuNet unavailable on confirmed frame: haar_scored=1, yunet_scored=0,
    paired_scored=0, unscored_gt_faces=1 in totals."""
    img = _write_blank_png(tmp_path)
    frame = _confirmed_frame(img, faces=[[0, 0, 10, 10]])
    m = _write_manifest(tmp_path, {"schema": "clipper_face_benchmark_v1",
                                   "frames": [frame]})
    out = tmp_path / "out.json"

    import benchmark_face_detectors as bench
    from services.clipper import face_detector
    with patch.object(face_detector, "_FACE_CASCADES",
                      [_MockCascade(), _MockCascade()]):
        with patch.object(bench, "YuNetDetector", lambda _: _UnavailYuNet()):
            with pytest.raises(SystemExit) as exc:
                main(["--manifest", str(m), "--model", "stub", "--out", str(out)])
    assert exc.value.code == 1
    data = json.loads(out.read_text())
    counts = data["counts"]
    assert counts["haar_inferred"] == 1
    assert counts["haar_scored"] == 1
    assert counts["yunet_scored"] == 0
    assert counts["yunet_unavailable"] == 1
    assert counts["paired_scored"] == 0
    assert data["totals"]["yunet_iou3"]["unscored_gt_faces"] == 1


def test_by_source_includes_counts(tmp_path):
    """Each by_source entry has a 'counts' dict with all frame_count keys."""
    img = _write_blank_png(tmp_path)
    frame = _confirmed_frame(img)
    m = _write_manifest(tmp_path, {"schema": "clipper_face_benchmark_v1",
                                   "frames": [frame]})
    out = tmp_path / "out.json"

    import benchmark_face_detectors as bench
    from services.clipper import face_detector
    with patch.object(face_detector, "_FACE_CASCADES",
                      [_MockCascade(), _MockCascade()]):
        with patch.object(bench, "YuNetDetector", lambda _: _NoopDetector()):
            with pytest.raises(SystemExit):
                main(["--manifest", str(m), "--model", "stub", "--out", str(out)])
    data = json.loads(out.read_text())
    src_entry = data["by_source"].get("src")
    assert src_entry is not None, "source 'src' must appear in by_source"
    assert "counts" in src_entry, "by_source entry must have a counts dict"
    assert set(src_entry["counts"].keys()) == set(data["counts"].keys())


# ── issue 3: gt_count field ───────────────────────────────────────────────────

def test_gt_count_set_for_confirmed_frame(tmp_path):
    """Confirmed+validated frame: gt_count == len(faces)."""
    img = _write_blank_png(tmp_path)
    frame = _confirmed_frame(img, faces=[[0, 0, 5, 5], [10, 10, 5, 5]])
    m = _write_manifest(tmp_path, {"schema": "clipper_face_benchmark_v1",
                                   "frames": [frame]})
    out = tmp_path / "out.json"

    import benchmark_face_detectors as bench
    from services.clipper import face_detector
    with patch.object(face_detector, "_FACE_CASCADES",
                      [_MockCascade(), _MockCascade()]):
        with patch.object(bench, "YuNetDetector", lambda _: _NoopDetector()):
            with pytest.raises(SystemExit):
                main(["--manifest", str(m), "--model", "stub", "--out", str(out)])
    row = json.loads(out.read_text())["results"][0]
    assert row["gt_count"] == 2


def test_gt_count_null_for_refused(tmp_path):
    """Refused frame (invalid annotation_state): gt_count must be null."""
    img = _write_blank_png(tmp_path)
    frame = _confirmed_frame(img, annotation_state="typo")
    m = _write_manifest(tmp_path, {"schema": "clipper_face_benchmark_v1",
                                   "frames": [frame]})
    out = tmp_path / "out.json"
    with pytest.raises(SystemExit):
        main(["--manifest", str(m), "--model", "/nonexistent.onnx",
              "--out", str(out)])
    row = json.loads(out.read_text())["results"][0]
    assert row["refused"] is True
    assert row["gt_count"] is None


def test_gt_count_null_for_uncertain(tmp_path):
    """Uncertain frame: gt_count must be null even though faces is a valid list."""
    img = _write_blank_png(tmp_path)
    frame = {**_confirmed_frame(img, annotation_state="uncertain"), "id": "f1"}
    m = _write_manifest(tmp_path, {"schema": "clipper_face_benchmark_v1",
                                   "frames": [frame]})
    out = tmp_path / "out.json"

    import benchmark_face_detectors as bench
    from services.clipper import face_detector
    with patch.object(face_detector, "_FACE_CASCADES",
                      [_MockCascade(), _MockCascade()]):
        with patch.object(bench, "YuNetDetector", lambda _: _NoopDetector()):
            with pytest.raises(SystemExit):
                main(["--manifest", str(m), "--model", "stub", "--out", str(out)])
    row = json.loads(out.read_text())["results"][0]
    assert row["gt_count"] is None


def test_unknown_gt_frames_counted(tmp_path):
    """refused and uncertain frames each increment unknown_gt_frames."""
    img = _write_blank_png(tmp_path)
    uncertain = {**_confirmed_frame(img, annotation_state="uncertain"), "id": "f2"}
    refused = {"id": "f3", "source": "s", "split": "dev",
               "annotation_state": "confirmed",
               "image": str(img),
               "sha256": hashlib.sha256(img.read_bytes()).hexdigest(),
               "width": 32, "height": 32}  # no faces key → refused
    m = _write_manifest(tmp_path, {"schema": "clipper_face_benchmark_v1",
                                   "frames": [uncertain, refused]})
    out = tmp_path / "out.json"

    import benchmark_face_detectors as bench
    from services.clipper import face_detector
    with patch.object(face_detector, "_FACE_CASCADES",
                      [_MockCascade(), _MockCascade()]):
        with patch.object(bench, "YuNetDetector", lambda _: _NoopDetector()):
            with pytest.raises(SystemExit):
                main(["--manifest", str(m), "--model", "stub", "--out", str(out)])
    counts = json.loads(out.read_text())["counts"]
    assert counts["unknown_gt_frames"] == 2


# ── issue 4: uncertain-frame errors in aggregate ──────────────────────────────

def test_uncertain_image_error_appears_in_aggregate_errors(tmp_path):
    """Uncertain frame with unreadable image: exit 1 AND aggregate errors==1."""
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
    assert exc.value.code == 1
    data = json.loads(out.read_text())
    for key in ("haar_iou3", "yunet_iou3"):
        assert data["totals"][key]["errors"] == 1, (
            f"{key}: uncertain image error must appear in aggregate errors")


# ── issue 5: field type validation ───────────────────────────────────────────

def test_string_width_refused(tmp_path):
    """width='32' (string) → refused row, no crash."""
    img = _write_blank_png(tmp_path)
    frame = {**_confirmed_frame(img), "width": "32"}
    m = _write_manifest(tmp_path, {"schema": "clipper_face_benchmark_v1",
                                   "frames": [frame]})
    out = tmp_path / "out.json"
    with pytest.raises(SystemExit):
        main(["--manifest", str(m), "--model", "/nonexistent.onnx",
              "--out", str(out)])
    row = json.loads(out.read_text())["results"][0]
    assert row["refused"] is True
    assert "width" in row["refused_reason"]


def test_bool_width_refused(tmp_path):
    """width=True (bool) must be refused — bool is a subtype of int in Python."""
    img = _write_blank_png(tmp_path)
    frame = {**_confirmed_frame(img), "width": True}
    m = _write_manifest(tmp_path, {"schema": "clipper_face_benchmark_v1",
                                   "frames": [frame]})
    out = tmp_path / "out.json"
    with pytest.raises(SystemExit):
        main(["--manifest", str(m), "--model", "/nonexistent.onnx",
              "--out", str(out)])
    row = json.loads(out.read_text())["results"][0]
    assert row["refused"] is True
    assert "width" in row["refused_reason"]


def test_invalid_sha256_refused(tmp_path):
    """sha256 not 64-hex → refused, no crash into sha256_path()."""
    img = _write_blank_png(tmp_path)
    frame = {**_confirmed_frame(img), "sha256": "not-hex-at-all"}
    m = _write_manifest(tmp_path, {"schema": "clipper_face_benchmark_v1",
                                   "frames": [frame]})
    out = tmp_path / "out.json"
    with pytest.raises(SystemExit):
        main(["--manifest", str(m), "--model", "/nonexistent.onnx",
              "--out", str(out)])
    row = json.loads(out.read_text())["results"][0]
    assert row["refused"] is True
    assert "sha256" in row["refused_reason"]


def test_missing_sha256_refused(tmp_path):
    """sha256 absent → refused, no KeyError."""
    img = _write_blank_png(tmp_path)
    frame = {k: v for k, v in _confirmed_frame(img).items() if k != "sha256"}
    m = _write_manifest(tmp_path, {"schema": "clipper_face_benchmark_v1",
                                   "frames": [frame]})
    out = tmp_path / "out.json"
    with pytest.raises(SystemExit):
        main(["--manifest", str(m), "--model", "/nonexistent.onnx",
              "--out", str(out)])
    row = json.loads(out.read_text())["results"][0]
    assert row["refused"] is True
    assert "sha256" in row["refused_reason"]
