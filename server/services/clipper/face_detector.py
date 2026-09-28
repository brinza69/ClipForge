"""Face detection for the AI Stream Clipper — Pass A and per-window sampling.

Extracted from ``signals.py`` (which crossed 500 lines). Public symbols are
re-exported from ``signals`` so existing callers are unaffected.

Each call to ``face_presence`` now returns one of four explicit states per
sample so that callers can distinguish a technically successful read with no
detection from a failure to decode the frame at all.
"""

from __future__ import annotations

import logging
import math
import threading
from pathlib import Path
from types import ModuleType
from typing import Any

logger = logging.getLogger("clipforge.clipper.signals")

# ── Haar cascade tuning (kept here; content_type re-exports these via signals)
FACE_SCALE_FACTOR = 1.05
FACE_MIN_NEIGHBOURS = 5
FACE_MIN_SIZE = (20, 20)
_FACE_MERGE_IOU = 0.35

# ── Observation states ────────────────────────────────────────────────────────
#
# Four states cover every possible outcome for one ``face_presence`` sample.
# "empty" is NOT the same as "no face in the frame" — it means the detector
# ran and returned zero boxes, which is a weaker signal than a positive find.
# A technical failure (state != "detected" or "empty") must never be promoted
# to an absence of faces.

STATE_DETECTED = "detected"          # frame read OK; detector found ≥1 box
STATE_EMPTY = "empty"                # frame read OK; detector found 0 boxes
STATE_UNREADABLE = "unreadable"      # file / capture / seek / read failed
STATE_UNAVAILABLE = "detector_unavailable"  # cv2 / cascades / detection failed

# Each thread detects with its OWN cascade instances (FD1). Threads detecting on
# the SAME instances at once do not get a single run's answer. Measured (ND0):
# two analyses in one process disagreed with the single run on 20–26 of 166
# frames, and a thread that arrived during the old process-wide lazy load read
# the half-built list as no cascades — its whole window came back
# cascades_unavailable. Why OpenCV diverges is not measured; that it does is.
_LOCAL = threading.local()


def _cv2() -> ModuleType | None:
    try:
        import cv2  # noqa: PLC0415
    except ImportError:
        logger.warning("opencv-python unavailable — face observations unavailable")
        return None
    return cv2


def _merge_boxes(boxes: list[list[int]]) -> list[list[int]]:
    """Drop boxes that overlap one already kept — two cascades see one face twice."""
    kept: list[list[int]] = []
    for box in sorted(boxes, key=lambda b: -b[2] * b[3]):
        x0, y0, w0, h0 = box
        for x1, y1, w1, h1 in kept:
            ix = max(0, min(x0 + w0, x1 + w1) - max(x0, x1))
            iy = max(0, min(y0 + h0, y1 + h1) - max(y0, y1))
            inter = ix * iy
            if inter and inter / float(w0 * h0 + w1 * h1 - inter) >= _FACE_MERGE_IOU:
                break
        else:
            kept.append(box)
    return kept


def face_cascades() -> list[Any]:
    """The tuned cascade set, loaded once per thread.

    Two cascades, not one: a co-stream has a facecam per person and they are
    rarely both facing the lens. Measured over 40 frames, the frontal cascade
    found the left facecam 17 times and the right one NEVER (that streamer was
    turned away); the profile cascade found the right one 3 times. Neither
    produced a false positive at 1.05/5, the best of six combinations tried —
    1.15 missed almost everything, minNeighbors=3 let nine into the gameplay.
    """
    cascades = getattr(_LOCAL, "cascades", None)
    if cascades is not None:
        return cascades
    loaded: list[Any] = []
    cv2 = _cv2()
    if cv2 is not None:
        for name in ("haarcascade_frontalface_alt2.xml", "haarcascade_profileface.xml"):
            c = cv2.CascadeClassifier(str(Path(cv2.data.haarcascades) / name))
            if not c.empty():
                loaded.append(c)
            else:
                logger.warning("face_cascades: could not load cascade %s", name)
    _LOCAL.cascades = loaded        # stored whole, never half-built
    return loaded


def detect_faces(grey: Any) -> list[list[int]]:
    """Face boxes in one GREYSCALE frame, as [x, y, w, h].

    The single entry point for face detection in the clipper. There used to be
    a second, untuned one in content_type.py, and on the co-stream it measured
    0 faces in 40 frames where this finds the left facecam in 14 and the right
    in 13 with one false positive — which is why regions.json reported no
    webcam on a source with two of them. Equalisation is part of the tuning,
    not a nicety: these facecams are small and dim.
    """
    cascades = face_cascades()
    if not cascades:
        return []
    cv2 = _cv2()
    if cv2 is None:
        return []
    equalised = cv2.equalizeHist(grey)
    raw: list[list[int]] = []
    for cascade in cascades:
        for x, y, w, h in cascade.detectMultiScale(
                equalised, FACE_SCALE_FACTOR, FACE_MIN_NEIGHBOURS,
                minSize=FACE_MIN_SIZE):
            raw.append([int(x), int(y), int(w), int(h)])
    return _merge_boxes(raw)


def face_presence(proxy_path: str, times: list[float], *,
                  detector: Any = None) -> list[dict[str, Any]]:
    """Face boxes at each requested timestamp, in PROXY pixel coordinates.

    Each entry carries:

      ``t``            – the requested seek time (seconds); backward-compatible.
      ``boxes``        – list of [x, y, w, h] in proxy pixels; [] for all
                         non-detected states; backward-compatible.
      ``state``        – one of the ``STATE_*`` constants above; the ONLY field
                         that distinguishes a clean "no detection" from a decode
                         failure.  Do not treat STATE_UNREADABLE or
                         STATE_UNAVAILABLE as evidence of face absence.
      ``frame_index``  – 0-based index of the decoded frame IN THE ANALYSED
                         FILE (not the original source), taken from
                         ``CAP_PROP_POS_FRAMES`` after seek and before
                         ``cap.read()``. None when unavailable, negative,
                         non-finite or non-integral; never rounded into an index.
      ``decoded_t``    – actual timestamp in seconds of the frame that was
                         decoded, read from ``CAP_PROP_POS_MSEC`` AFTER
                         ``cap.read()``, which the FFMPEG backend reports as
                         the PTS of the frame just decoded (not the next one).
                         Reading it BEFORE read returns the previous frame's
                         PTS — off by one frame (verified by pixel oracle on
                         this system).  None when the frame was not decoded,
                         or when the backend returns NaN/inf/negative.
      ``detector_meta`` – (only when ``detector`` is supplied) full metadata
                         dict from the injected detector's .detect() result,
                         excluding state/reason/boxes which are promoted to the
                         top-level entry.  Present even when state is
                         detector_unavailable, so the caller can inspect profile,
                         score_thresh, inference_ms, etc. alongside the decoded
                         address.  Absent on decode-failure entries.

    ``detector`` (keyword-only): if supplied, must duck-type ``.detect(BGR)``
    and return a dict with at least ``state``, ``reason``, ``boxes``.  Haar
    cascades are NOT loaded when a detector is provided.  Decoding failure
    (STATE_UNREADABLE) remains distinct from inference failure
    (STATE_UNAVAILABLE): an unavailable inference still carries the decoded
    address (frame_index, decoded_t) when the frame was successfully read.
    YuNet must not depend on Haar cascades and does not fall back to them.

    Addresses are FFMPEG backend reports, with conventions verified by real
    decoded-pixel tests. Other backends have no verified address convention
    here, so their frame can be observed but its address stays unavailable.
    The index and timestamp name the frame in the ANALYSED file.  When the
    analysed file is a re-encoded window (``dynamic_window.analyse_window``),
    adding ``start`` does not certify a source timestamp — the re-encode shifts
    the clock.  Legacy entries without ``state`` are unknown; do not infer state
    from ``boxes`` alone.

    Always returns one entry per requested time so callers can zip against their
    own sample grid.
    """
    stamps = [float(t) for t in (times or []) if float(t) >= 0]

    def failed(t: float, state: str, reason: str) -> dict[str, Any]:
        return {"t": round(t, 3), "boxes": [], "state": state, "reason": reason,
                "frame_index": None, "decoded_t": None,
                "clock": "analysed_file_requested", "decoded_space": "analysed_file",
                "address_basis": None}

    cv2 = _cv2()
    if cv2 is None or not stamps:
        return [failed(t, STATE_UNAVAILABLE, "opencv_unavailable") for t in stamps]
    if not proxy_path or not Path(proxy_path).exists():
        logger.warning("face_presence: missing proxy %s", proxy_path)
        return [failed(t, STATE_UNREADABLE, "file_missing") for t in stamps]
    if detector is None and not face_cascades():
        return [failed(t, STATE_UNAVAILABLE, "cascades_unavailable") for t in stamps]

    out: list[dict[str, Any]] = []
    cap = cv2.VideoCapture(str(proxy_path))
    try:
        if not cap.isOpened():
            logger.warning("face_presence: cannot open %s", proxy_path)
            return [failed(t, STATE_UNREADABLE, "capture_not_open") for t in stamps]
        try:
            backend = cap.getBackendName()
        except (AttributeError, cv2.error):
            backend = None
        for t in stamps:
            try:
                sought = cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
                if not sought:
                    out.append(failed(t, STATE_UNREADABLE, "seek_failed"))
                    continue
                try:
                    raw_idx = cap.get(cv2.CAP_PROP_POS_FRAMES)
                except cv2.error:
                    raw_idx = float("nan")
                ok, frame = cap.read()
            except cv2.error:
                out.append(failed(t, STATE_UNREADABLE, "decode_error"))
                continue
            # POS_FRAMES before read is the index that cap.read() will decode
            # (verified correct by pixel oracle on this system at OpenCV 4.14).
            # POS_MSEC before read is the PREVIOUS frame's PTS — one behind —
            # so it is not used.  POS_MSEC is read AFTER read instead.
            pre_idx = (int(raw_idx) if backend == "FFMPEG" and
                       math.isfinite(raw_idx) and raw_idx >= 0
                       and float(raw_idx).is_integer() else None)
            if not ok or frame is None:
                out.append(failed(t, STATE_UNREADABLE, "frame_not_decoded"))
                continue
            # POS_MSEC after read: the FFMPEG backend returns the PTS of the
            # frame just decoded.  Mark as unavailable if the backend returns
            # a non-finite or negative value rather than inventing a number.
            try:
                raw_msec = cap.get(cv2.CAP_PROP_POS_MSEC)
            except cv2.error:
                raw_msec = float("nan")
            decoded_t = (round(raw_msec / 1000.0, 6)
                         if backend == "FFMPEG" and math.isfinite(raw_msec)
                         and raw_msec >= 0 else None)
            if detector is not None:
                try:
                    det_result = detector.detect(frame)
                    if det_result["state"] not in (STATE_DETECTED, STATE_EMPTY, STATE_UNAVAILABLE):
                        raise ValueError("invalid detector state")
                except Exception as exc:
                    det_result = {"state": STATE_UNAVAILABLE, "reason": "detection_error",
                                  "boxes": [], "detail": str(exc)}
                boxes = det_result.get("boxes", [])
                state = det_result["state"]
                reason = det_result.get("reason")
                # Preserve full detector metadata alongside the decoded address.
                # Excludes state/reason/boxes which are already at top level.
                detector_meta: dict[str, Any] | None = {
                    k: v for k, v in det_result.items()
                    if k not in ("state", "reason", "boxes")}
            else:
                reason = None
                detector_meta = None
                try:
                    grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    boxes = detect_faces(grey)
                    state = STATE_DETECTED if boxes else STATE_EMPTY
                except cv2.error:
                    boxes, state, reason = [], STATE_UNAVAILABLE, "detection_error"
            entry: dict[str, Any] = {
                "t": round(t, 3), "boxes": boxes, "state": state,
                "reason": reason, "frame_index": pre_idx, "decoded_t": decoded_t,
                "clock": "analysed_file_requested", "decoded_space": "analysed_file",
                "address_basis": "opencv_ffmpeg_metadata" if backend == "FFMPEG" else None}
            if detector_meta is not None:
                entry["detector_meta"] = detector_meta
            out.append(entry)
    finally:
        cap.release()
    return out
