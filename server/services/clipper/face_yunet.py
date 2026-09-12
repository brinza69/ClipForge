"""YuNet face detector adapter — OpenCV 4.x cv2.FaceDetectorYN.

Independent of face_detector.py and signals.py.  Does NOT alter any live
caller in B1.  Each YuNetDetector instance owns a mutable cv2.FaceDetectorYN;
instances are not thread-safe.

States match face_detector.py's vocabulary: detected / empty /
detector_unavailable.  An invalid detector output (non-finite coordinate or
degenerate box after clamping to image bounds) is STATE_UNAVAILABLE, never
STATE_EMPTY — a thing that could not be read must never read as a pass.
No face identity claim is made at any inference depth.
"""
from __future__ import annotations

import hashlib
import math
import time
from pathlib import Path
from typing import Any

# Pinned model — supplied at data/models/face_detection_yunet_2023mar.onnx.
MODEL_FILENAME = "face_detection_yunet_2023mar.onnx"
MODEL_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
MODEL_BYTES = 232589

# Frozen inference thresholds — no tuning to labels in B1.
SCORE_THRESH = 0.9
NMS_THRESH = 0.3
TOP_K = 5000

STATE_DETECTED = "detected"
STATE_EMPTY = "empty"
STATE_UNAVAILABLE = "detector_unavailable"


def _cv2() -> Any | None:
    try:
        import cv2  # noqa: PLC0415
        return cv2
    except ImportError:
        return None


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class YuNetDetector:
    """Single-threaded YuNet face detector.  Create one instance per thread.

    Construction verifies the model file's identity (size + SHA-256).
    detect() returns STATE_UNAVAILABLE for every call until the model is
    valid and the backend initialised without error.
    """

    def __init__(self, model_path: str) -> None:
        self._path = str(model_path)
        self._detector: Any = None
        self._reason: str | None = None
        self._detail: str | None = None
        # _measured_hash: actual hash read from disk (None if file unread/missing).
        # Distinct from MODEL_SHA256 (the expected constant).
        self._measured_hash: str | None = None
        self._load()

    def _load(self) -> None:
        path = Path(self._path)
        try:
            exists = path.exists()
        except OSError as exc:
            self._reason, self._detail = "model_missing", str(exc)
            return
        if not exists:
            self._reason, self._detail = "model_missing", self._path
            return
        try:
            size = path.stat().st_size
        except OSError as exc:
            self._reason, self._detail = "model_missing", str(exc)
            return
        if size != MODEL_BYTES:
            self._reason = "wrong_size"
            self._detail = f"expected {MODEL_BYTES} bytes, got {size}"
            return
        try:
            digest = _sha256_file(path)
        except OSError as exc:
            self._reason, self._detail = "model_missing", str(exc)
            return
        self._measured_hash = digest
        if digest != MODEL_SHA256:
            self._reason = "wrong_hash"
            self._detail = f"expected {MODEL_SHA256[:12]}…, got {digest[:12]}…"
            return
        cv2 = _cv2()
        if cv2 is None:
            self._reason, self._detail = "opencv_unavailable", "cv2 not installed"
            return
        try:
            self._detector = cv2.FaceDetectorYN.create(
                self._path, "", (1, 1), SCORE_THRESH, NMS_THRESH, TOP_K)
        except Exception as exc:
            self._reason, self._detail = "init_error", str(exc)

    @property
    def init_error(self) -> tuple[str, str] | None:
        """(reason, detail) if init failed; None when ready."""
        return (self._reason, self._detail or "") if self._reason else None

    def detect(self, bgr: Any) -> dict[str, Any]:
        """Detect faces in a BGR image (H×W×3 uint8).

        Returns a dict with:
          state                — STATE_DETECTED / STATE_EMPTY / STATE_UNAVAILABLE
          reason               — None on success, string on failure
          boxes                — [[x, y, w, h], …] in image-pixel ints (clamped)
          scores               — per-box detection score (float)
          model_id             — pinned filename
          expected_model_hash  — MODEL_SHA256 constant (always present)
          model_hash           — measured SHA-256 from disk, or None if unread
          hash_verified        — True iff measured hash matches expected
          score_thresh         — SCORE_THRESH used
          nms_thresh           — NMS_THRESH used
          inference_ms         — wall-clock time for setInputSize + detect, or None
        """
        base: dict[str, Any] = {
            "model_id": MODEL_FILENAME,
            "expected_model_hash": MODEL_SHA256,
            "model_hash": self._measured_hash,
            "hash_verified": self._measured_hash == MODEL_SHA256,
            "score_thresh": SCORE_THRESH,
            "nms_thresh": NMS_THRESH,
        }
        if self._reason:
            return {**base, "state": STATE_UNAVAILABLE, "reason": self._reason,
                    "detail": self._detail, "boxes": [], "scores": [],
                    "inference_ms": None}
        h, w = bgr.shape[:2]
        t0 = time.perf_counter()
        try:
            self._detector.setInputSize((w, h))
            _retval, faces = self._detector.detect(bgr)
        except Exception as exc:
            return {**base, "state": STATE_UNAVAILABLE, "reason": "inference_error",
                    "detail": str(exc), "boxes": [], "scores": [], "inference_ms": None}
        ms = (time.perf_counter() - t0) * 1000.0
        # Ignore _retval; inspect faces array directly.
        # Validate shape before len() — np.array(3) is 0-D and raises TypeError.
        if faces is None:
            return {**base, "state": STATE_EMPTY, "reason": None,
                    "boxes": [], "scores": [], "inference_ms": ms}
        try:
            n_faces = len(faces)
        except TypeError as exc:
            return {**base, "state": STATE_UNAVAILABLE, "reason": "invalid_output",
                    "detail": f"faces has no len: {exc}",
                    "boxes": [], "scores": [], "inference_ms": ms}
        if n_faces == 0:
            return {**base, "state": STATE_EMPTY, "reason": None,
                    "boxes": [], "scores": [], "inference_ms": ms}
        if hasattr(faces, "ndim") and (faces.ndim != 2 or faces.shape[1] < 15):
            return {**base, "state": STATE_UNAVAILABLE, "reason": "invalid_output",
                    "detail": f"faces shape {tuple(faces.shape)} is not Nx>=15",
                    "boxes": [], "scores": [], "inference_ms": ms}
        boxes: list[list[int]] = []
        scores: list[float] = []
        for row in faces:
            try:
                rx, ry, rw, rh = float(row[0]), float(row[1]), float(row[2]), float(row[3])
                sc = float(row[14])
            except (IndexError, TypeError, ValueError) as exc:
                return {**base, "state": STATE_UNAVAILABLE, "reason": "invalid_box",
                        "detail": f"malformed row: {exc}",
                        "boxes": [], "scores": [], "inference_ms": ms}
            if not all(math.isfinite(v) for v in (rx, ry, rw, rh, sc)):
                return {**base, "state": STATE_UNAVAILABLE, "reason": "invalid_box",
                        "detail": f"non-finite coords: {[rx, ry, rw, rh]}",
                        "boxes": [], "scores": [], "inference_ms": ms}
            # Endpoint conversion avoids independent-rounding degeneracy.
            # e.g. box (1.1, 1.1, 0.3, 0.3): x1=round(1.4)=1 → iw=0 → unavailable.
            x0 = max(0.0, rx)
            y0 = max(0.0, ry)
            x1 = min(float(w), rx + rw)
            y1 = min(float(h), ry + rh)
            ix0, iy0 = int(round(x0)), int(round(y0))
            ix1, iy1 = int(round(x1)), int(round(y1))
            iw, ih = ix1 - ix0, iy1 - iy0
            if iw <= 0 or ih <= 0:
                return {**base, "state": STATE_UNAVAILABLE, "reason": "invalid_box",
                        "detail": f"degenerate after clamp+round: w={iw} h={ih}",
                        "boxes": [], "scores": [], "inference_ms": ms}
            boxes.append([ix0, iy0, iw, ih])
            scores.append(round(sc, 6))
        return {**base, "state": STATE_DETECTED, "reason": None,
                "boxes": boxes, "scores": scores, "inference_ms": ms}
