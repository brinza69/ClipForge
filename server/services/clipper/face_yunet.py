"""YuNet face detector adapter — OpenCV 4.x cv2.FaceDetectorYN.

Independent of face_detector.py and signals.py.  Does NOT alter any live
caller in B1.  Each YuNetDetector instance owns a mutable cv2.FaceDetectorYN;
instances are not thread-safe.

States match face_detector.py's vocabulary: detected / empty /
detector_unavailable.  An invalid detector output (non-finite coordinate or
degenerate box after clamping to image bounds) is STATE_UNAVAILABLE, never
STATE_EMPTY — a thing that could not be read must never read as a pass.
No face identity claim is made at any inference depth.

B2 adds two explicit profiles.  Instance settings are frozen at construction
and reported from detect(), not re-read from module constants.  Unknown
profiles are refused at construction; no automatic profile choice based on
detections.
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

# Module-level defaults (native_v1 profile) — preserved for backward-compat imports.
SCORE_THRESH = 0.9
NMS_THRESH = 0.3
TOP_K = 5000

STATE_DETECTED = "detected"
STATE_EMPTY = "empty"
STATE_UNAVAILABLE = "detector_unavailable"

# Explicit profiles — settings frozen at construction.  Unknown profile → ValueError.
# native_v1:      original BGR, score=0.9, no resize.  Same as B1 defaults.
# small_faces_v1: 2x linear resize, score=0.75.  Model sees enlarged BGR;
#                 boxes are mapped back to caller's input pixel coordinates.
PROFILES: dict[str, dict[str, Any]] = {
    "native_v1": {
        "score_thresh": 0.9,
        "nms_thresh": 0.3,
        "top_k": 5000,
        "scale": 1.0,
    },
    "small_faces_v1": {
        "score_thresh": 0.75,
        "nms_thresh": 0.3,
        "top_k": 5000,
        "scale": 2.0,
    },
}


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

    ``profile`` selects frozen inference settings (see PROFILES).  Unknown
    profiles are refused with ValueError at construction.  Construction also
    verifies the model file's identity (size + SHA-256); detect() returns
    STATE_UNAVAILABLE until the model is valid and the backend initialised.
    """

    def __init__(self, model_path: str, profile: str = "native_v1") -> None:
        if profile not in PROFILES:
            raise ValueError(
                f"unknown profile {profile!r}; valid: {sorted(PROFILES)}")
        self._profile = profile
        cfg = PROFILES[profile]
        self._score_thresh: float = cfg["score_thresh"]
        self._nms_thresh: float = cfg["nms_thresh"]
        self._top_k: int = cfg["top_k"]
        self._scale: float = cfg["scale"]

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
                self._path, "", (1, 1),
                self._score_thresh, self._nms_thresh, self._top_k)
        except Exception as exc:
            self._reason, self._detail = "init_error", str(exc)

    @property
    def init_error(self) -> tuple[str, str] | None:
        """(reason, detail) if init failed; None when ready."""
        return (self._reason, self._detail or "") if self._reason else None

    def detect(self, bgr: Any) -> dict[str, Any]:
        """Detect faces in a BGR image (H×W×3 uint8).

        Returns a dict with:
          state                  — STATE_DETECTED / STATE_EMPTY / STATE_UNAVAILABLE
          reason                 — None on success, string on failure
          boxes                  — [[x, y, w, h], …] in caller's input pixel ints
          scores                 — per-box detection score (float)
          raw_boxes_model_space  — [[x, y, w, h] floats from model before inverse
                                    mapping; None for native_v1 (scale=1.0)
          model_id               — pinned filename
          expected_model_hash    — MODEL_SHA256 constant (always present)
          model_hash             — measured SHA-256 from disk, or None if unread
          hash_verified          — True iff measured == expected
          profile                — profile name this instance was built with
          score_thresh           — instance score threshold (not module constant)
          nms_thresh             — instance NMS threshold
          top_k                  — instance top-K
          scale                  — input scale applied before inference
          input_size             — caller's [width, height], None before image inspection
          model_input_size       — dimensions accepted by setInputSize, otherwise None
          interpolation          — linear for the 2x profile, otherwise None
          inference_ms           — wall-clock time for setInputSize + detect, None on error
        """
        base: dict[str, Any] = {
            "model_id": MODEL_FILENAME,
            "expected_model_hash": MODEL_SHA256,
            "model_hash": self._measured_hash,
            "hash_verified": self._measured_hash == MODEL_SHA256,
            "profile": self._profile,
            "score_thresh": self._score_thresh,
            "nms_thresh": self._nms_thresh,
            "top_k": self._top_k,
            "scale": self._scale,
            "input_size": None,
            "model_input_size": None,
            "interpolation": "linear" if self._scale != 1.0 else None,
        }
        if self._reason:
            return {**base, "state": STATE_UNAVAILABLE, "reason": self._reason,
                    "detail": self._detail, "boxes": [], "scores": [],
                    "raw_boxes_model_space": None, "inference_ms": None}

        h, w = bgr.shape[:2]
        scale = self._scale
        base["input_size"] = [w, h]

        # For profiles with scale > 1: resize input to enlarged BGR, run detection,
        # then map endpoint coordinates back to the caller's original pixel space
        # before clamping and rounding.  Linear interpolation only.
        try:
            if scale != 1.0:
                cv2 = _cv2()
                sw, sh = int(w * scale), int(h * scale)
                inp = cv2.resize(bgr, (sw, sh), interpolation=cv2.INTER_LINEAR)
            else:
                inp, sw, sh = bgr, w, h
        except Exception as exc:
            return {**base, "state": STATE_UNAVAILABLE, "reason": "preprocess_error",
                    "detail": str(exc), "boxes": [], "scores": [],
                    "raw_boxes_model_space": None, "inference_ms": None}

        t0 = time.perf_counter()
        try:
            self._detector.setInputSize((sw, sh))
            base["model_input_size"] = [sw, sh]
            _retval, faces = self._detector.detect(inp)
        except Exception as exc:
            return {**base, "state": STATE_UNAVAILABLE, "reason": "inference_error",
                    "detail": str(exc), "boxes": [], "scores": [],
                    "raw_boxes_model_space": None, "inference_ms": None}
        ms = (time.perf_counter() - t0) * 1000.0

        # Ignore _retval; inspect faces array directly.
        # Validate shape before len() — np.array(3) is 0-D and raises TypeError.
        if faces is None:
            return {**base, "state": STATE_EMPTY, "reason": None,
                    "boxes": [], "scores": [], "raw_boxes_model_space": None,
                    "inference_ms": ms}
        try:
            n_faces = len(faces)
        except TypeError as exc:
            return {**base, "state": STATE_UNAVAILABLE, "reason": "invalid_output",
                    "detail": f"faces has no len: {exc}",
                    "boxes": [], "scores": [], "raw_boxes_model_space": None,
                    "inference_ms": ms}
        if n_faces == 0:
            return {**base, "state": STATE_EMPTY, "reason": None,
                    "boxes": [], "scores": [], "raw_boxes_model_space": None,
                    "inference_ms": ms}
        if hasattr(faces, "ndim") and (faces.ndim != 2 or faces.shape[1] < 15):
            return {**base, "state": STATE_UNAVAILABLE, "reason": "invalid_output",
                    "detail": f"faces shape {tuple(faces.shape)} is not Nx>=15",
                    "boxes": [], "scores": [], "raw_boxes_model_space": None,
                    "inference_ms": ms}

        boxes: list[list[int]] = []
        scores: list[float] = []
        # raw model coordinates for scale > 1 only (None for native_v1)
        raw_model: list[list[float]] | None = [] if scale != 1.0 else None

        for row in faces:
            try:
                rx, ry, rw, rh = float(row[0]), float(row[1]), float(row[2]), float(row[3])
                sc = float(row[14])
            except (IndexError, TypeError, ValueError) as exc:
                return {**base, "state": STATE_UNAVAILABLE, "reason": "invalid_box",
                        "detail": f"malformed row: {exc}",
                        "boxes": [], "scores": [], "raw_boxes_model_space": None,
                        "inference_ms": ms}
            if not all(math.isfinite(v) for v in (rx, ry, rw, rh, sc)):
                return {**base, "state": STATE_UNAVAILABLE, "reason": "invalid_box",
                        "detail": f"non-finite coords: {[rx, ry, rw, rh]}",
                        "boxes": [], "scores": [], "raw_boxes_model_space": None,
                        "inference_ms": ms}

            # Map endpoints from model (possibly scaled) space back to caller's
            # input pixel coordinates, then clamp to original image bounds.
            # For native_v1 (scale=1.0) this is identity.
            x0 = max(0.0, rx / scale)
            y0 = max(0.0, ry / scale)
            x1 = min(float(w), (rx + rw) / scale)
            y1 = min(float(h), (ry + rh) / scale)
            ix0, iy0 = int(round(x0)), int(round(y0))
            ix1, iy1 = int(round(x1)), int(round(y1))
            iw, ih = ix1 - ix0, iy1 - iy0
            if iw <= 0 or ih <= 0:
                return {**base, "state": STATE_UNAVAILABLE, "reason": "invalid_box",
                        "detail": f"degenerate after clamp+round: w={iw} h={ih}",
                        "boxes": [], "scores": [], "raw_boxes_model_space": None,
                        "inference_ms": ms}

            if raw_model is not None:
                raw_model.append([rx, ry, rw, rh])
            boxes.append([ix0, iy0, iw, ih])
            scores.append(round(sc, 6))

        return {**base, "state": STATE_DETECTED, "reason": None,
                "boxes": boxes, "scores": scores,
                "raw_boxes_model_space": raw_model,
                "inference_ms": ms}
