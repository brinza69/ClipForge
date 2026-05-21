"""
Auto-detect on-screen captions in a video.

Strategy:
  1. Sample frames at ~3 fps (cheap enough; catches caption transitions).
  2. Run EasyOCR on each sample frame — gives text + bbox + confidence.
  3. Cluster detected boxes by vertical position (Y-center) into "lanes".
     Most clips have one caption lane (bottom); occasionally two (top + bottom)
     or shifting lanes (caption moves mid-clip).
  4. Per lane, split detections into time segments — a >1.5s gap with no
     text → new segment. This is what handles "captions move mid-clip":
     before the move = one segment with the old bbox; after = another
     segment with the new bbox.
  5. For each segment, take the UNION of all detected boxes — the widest
     bbox spanning the segment's lifetime. This is the "longest caption
     occupies the largest zone" rule.
  6. Return a list of {start_t, end_t, x, y, w, h} entries that
     `inpaint_region(segments=...)` knows how to consume.

EasyOCR is imported lazily so the server boots even if it's not installed
yet. First detect call may take ~10s to warm up the model.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import cv2
import numpy as np

logger = logging.getLogger("clipforge.caption_detector")

_reader = None
_reader_lock = None  # lazy init
_LANGS = os.environ.get("CLIPFORGE_OCR_LANGS", "en").split(",")


def _get_reader():
    """Lazy singleton — EasyOCR Reader is expensive to construct."""
    global _reader, _reader_lock
    if _reader is not None:
        return _reader
    if _reader_lock is None:
        import threading
        _reader_lock = threading.Lock()
    with _reader_lock:
        if _reader is not None:
            return _reader
        try:
            import easyocr  # noqa
        except ImportError as e:
            raise RuntimeError(
                "easyocr is not installed. Run "
                "`pip install easyocr` in the server venv and retry."
            ) from e
        try:
            import torch
            gpu = torch.cuda.is_available()
        except Exception:
            gpu = False
        logger.info(f"Loading EasyOCR (langs={_LANGS}, gpu={gpu})…")
        t0 = time.time()
        _reader = easyocr.Reader(_LANGS, gpu=gpu, verbose=False)
        logger.info(f"EasyOCR loaded in {time.time() - t0:.1f}s")
        return _reader


@dataclass
class _Detection:
    t: float
    x: int
    y: int
    w: int
    h: int
    text: str
    conf: float

    @property
    def y_center(self) -> float:
        return self.y + self.h / 2


@dataclass
class _Lane:
    y_center: float
    detections: List[_Detection] = field(default_factory=list)

    def add(self, d: _Detection) -> None:
        n = len(self.detections)
        # Running average: weight new detection's y_center modestly so the
        # lane center doesn't drift forever if caption migrates.
        self.y_center = (self.y_center * n + d.y_center) / (n + 1)
        self.detections.append(d)


def _bbox_from_easyocr(bbox) -> tuple[int, int, int, int]:
    """EasyOCR returns 4 corner points: [[x0,y0],[x1,y0],[x1,y1],[x0,y1]]."""
    xs = [int(p[0]) for p in bbox]
    ys = [int(p[1]) for p in bbox]
    x, y = min(xs), min(ys)
    w, h = max(xs) - x, max(ys) - y
    return x, y, max(1, w), max(1, h)


def _ocr_frame(reader, frame: np.ndarray) -> List[tuple]:
    """Run OCR on a BGR frame. Returns easyocr's [(bbox, text, conf), ...]."""
    # EasyOCR accepts BGR ndarray directly
    return reader.readtext(frame)


def detect_caption_segments(
    video_path: str,
    *,
    sample_fps: float = 3.0,
    min_conf: float = 0.35,
    lane_threshold_frac: float = 0.08,
    min_detections_per_lane: int = 3,
    segment_gap_s: float = 1.5,
    padding_px: int = 12,
    bleed_s: float = 0.2,
    on_progress: Optional[Callable[[float, str], None]] = None,
) -> List[dict]:
    """
    Scan a video and return caption time-segments.

    Each returned dict: {start_t, end_t, x, y, w, h} in input-pixel coords.
    Empty list = no captions detected.
    """
    reader = _get_reader()

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    vw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    vh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration = (total / fps) if fps > 0 else 0.0

    step = max(1, int(round(fps / max(0.5, sample_fps))))
    n_samples = total // step if step > 0 else 0
    logger.info(
        f"Caption detect start: {vw}x{vh} {duration:.1f}s, "
        f"sampling every {step} frames (~{sample_fps} fps)"
    )

    detections: List[_Detection] = []
    sample_idx = 0
    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_idx % step == 0:
            t = frame_idx / fps
            try:
                ocr_results = _ocr_frame(reader, frame)
            except Exception:
                logger.exception(f"OCR failed at t={t:.2f}; skipping frame")
                ocr_results = []
            for bbox, text, conf in ocr_results:
                if conf is None or conf < min_conf:
                    continue
                if not text or not text.strip():
                    continue
                x, y, w, h = _bbox_from_easyocr(bbox)
                detections.append(_Detection(t=t, x=x, y=y, w=w, h=h, text=text, conf=float(conf)))
            sample_idx += 1
            if on_progress and n_samples > 0 and sample_idx % 5 == 0:
                try:
                    on_progress(min(1.0, sample_idx / n_samples), f"OCR {sample_idx}/{n_samples}")
                except Exception:
                    pass
        frame_idx += 1

    cap.release()
    logger.info(f"OCR done: {len(detections)} text detections across {sample_idx} sample frames")

    if not detections:
        return []

    # Cluster detections into lanes by Y-center
    lane_threshold = vh * lane_threshold_frac
    lanes: List[_Lane] = []
    for d in sorted(detections, key=lambda x: x.t):
        matched: Optional[_Lane] = None
        best_dist = lane_threshold
        for lane in lanes:
            dist = abs(d.y_center - lane.y_center)
            if dist < best_dist:
                matched = lane
                best_dist = dist
        if matched is not None:
            matched.add(d)
        else:
            lanes.append(_Lane(y_center=d.y_center, detections=[d]))

    logger.info(f"Clustered into {len(lanes)} caption lanes")

    # Per-lane: split by time gaps → segments, union boxes per segment
    segments: List[dict] = []
    for lane in lanes:
        if len(lane.detections) < min_detections_per_lane:
            continue
        lane.detections.sort(key=lambda x: x.t)

        cur = None
        for d in lane.detections:
            if cur is None or (d.t - cur["last_t"]) > segment_gap_s:
                if cur is not None:
                    segments.append(_finalise_segment(cur, vw, vh, padding_px, bleed_s))
                cur = {
                    "start_t": d.t,
                    "last_t": d.t,
                    "x0": d.x, "y0": d.y,
                    "x1": d.x + d.w, "y1": d.y + d.h,
                }
            else:
                cur["last_t"] = d.t
                cur["x0"] = min(cur["x0"], d.x)
                cur["y0"] = min(cur["y0"], d.y)
                cur["x1"] = max(cur["x1"], d.x + d.w)
                cur["y1"] = max(cur["y1"], d.y + d.h)
        if cur is not None:
            segments.append(_finalise_segment(cur, vw, vh, padding_px, bleed_s))

    # Sort by time for nicer UX in the UI
    segments.sort(key=lambda s: s["start_t"])
    logger.info(f"Produced {len(segments)} caption segments")
    return segments


def _finalise_segment(cur: dict, vw: int, vh: int, padding: int, bleed: float) -> dict:
    x = max(0, int(cur["x0"]) - padding)
    y = max(0, int(cur["y0"]) - padding)
    x_end = min(vw, int(cur["x1"]) + padding)
    y_end = min(vh, int(cur["y1"]) + padding)
    return {
        "start_t": max(0.0, float(cur["start_t"]) - bleed),
        "end_t": float(cur["last_t"]) + bleed,
        "x": x,
        "y": y,
        "w": max(1, x_end - x),
        "h": max(1, y_end - y),
    }
