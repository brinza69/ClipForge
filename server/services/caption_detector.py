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
    sample_fps: float = 5.0,        # was 3.0 — catches transient text
    min_conf: float = 0.25,         # was 0.35 — stylized fonts score lower
    lane_threshold_frac: float = 0.08,
    min_detections_per_lane: int = 3,
    segment_gap_s: float = 1.5,
    padding_px: int = 6,            # was 12 — tighter bbox; inpaint dilates separately
    bleed_s: float = 0.4,           # was 0.2 — covers brief fade-in/fade-out
    drift_threshold: float = 0.30,  # NEW — split segments when bbox center
                                     # drifts more than this fraction of current size
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

    # Per lane → a robust, tight zone bbox (percentile union drops the odd
    # over-wide OCR box that would inflate the erase region).
    zones: List[dict] = []
    for lane in lanes:
        if len(lane.detections) < min_detections_per_lane:
            continue
        xs0 = np.array([d.x for d in lane.detections], dtype=np.float32)
        xs1 = np.array([d.x + d.w for d in lane.detections], dtype=np.float32)
        ys0 = np.array([d.y for d in lane.detections], dtype=np.float32)
        ys1 = np.array([d.y + d.h for d in lane.detections], dtype=np.float32)
        x0 = max(0, int(np.percentile(xs0, 3)) - padding_px)
        y0 = max(0, int(np.percentile(ys0, 3)) - padding_px)
        x1 = min(vw, int(np.percentile(xs1, 97)) + padding_px)
        y1 = min(vh, int(np.percentile(ys1, 97)) + padding_px)
        if x1 - x0 < 8 or y1 - y0 < 8:
            continue
        zones.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1,
                      "ocr_times": sorted(d.t for d in lane.detections)})

    if not zones:
        return []

    # Frame-accurate presence: OCR (sampled) only located the zones; now scan
    # EVERY frame's edge density inside each zone. Text = many edges, idle
    # background = few. This catches frames OCR missed (hard-to-read words,
    # fades, transitions) so no caption frame is left un-erased.
    segments = _presence_segments(
        video_path, zones, fps, total, bleed_s, on_progress
    )
    segments.sort(key=lambda s: s["start_t"])
    logger.info(f"Produced {len(segments)} caption segments (per-frame presence)")
    return segments


def _presence_segments(
    video_path: str, zones: List[dict], fps: float, total: int,
    bleed_s: float, on_progress: Optional[Callable[[float, str], None]],
) -> List[dict]:
    """Per-frame edge-density scan inside each zone → contiguous time segments."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []
    dens: List[List[float]] = [[] for _ in zones]
    times: List[float] = []
    fidx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        times.append(fidx / fps)
        for zi, z in enumerate(zones):
            crop = frame[z["y0"]:z["y1"], z["x0"]:z["x1"]]
            if crop.size == 0:
                dens[zi].append(0.0)
                continue
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            edges = cv2.Canny(gray, 60, 160)
            dens[zi].append(float(edges.mean()))
        fidx += 1
        if on_progress and total and fidx % 60 == 0:
            on_progress(min(1.0, fidx / total), f"Scanning frames {fidx}/{total}")
    cap.release()
    if not times:
        return []

    bridge = max(1, int(round(0.30 * fps)))   # fill flicker gaps up to ~0.3s
    min_len = max(1, int(round(0.12 * fps)))   # drop sub-0.12s noise runs
    out: List[dict] = []
    for zi, z in enumerate(zones):
        d = np.array(dens[zi], dtype=np.float32)
        if d.max() <= 1e-3:
            continue
        # Adaptive threshold between the idle floor and the text peak.
        lo = float(np.percentile(d, 20))
        hi = float(np.percentile(d, 90))
        thr = lo + 0.30 * max(1e-3, hi - lo)
        present = d > thr
        n = len(present)
        # Anchor: every frame OCR actually saw text is forced present (so a
        # busy background that confuses the edge threshold can't drop a frame
        # OCR already confirmed — no regression vs the old detector).
        anchor = max(1, int(round(0.12 * fps)))
        for ot in z.get("ocr_times", []):
            k = int(round(ot * fps))
            present[max(0, k - anchor):min(n, k + anchor + 1)] = True
        # Bridge short gaps so word-to-word transitions stay one segment.
        i = 0
        while i < n:
            if present[i]:
                i += 1
                continue
            j = i
            while j < n and not present[j]:
                j += 1
            if 0 < i and j < n and (j - i) <= bridge:
                present[i:j] = True
            i = j
        # Emit runs of True as segments.
        i = 0
        while i < n:
            if not present[i]:
                i += 1
                continue
            j = i
            while j < n and present[j]:
                j += 1
            if (j - i) >= min_len:
                out.append({
                    "start_t": max(0.0, times[i] - bleed_s),
                    "end_t": times[min(j, n - 1)] + bleed_s,
                    "x": z["x0"], "y": z["y0"],
                    "w": max(1, z["x1"] - z["x0"]),
                    "h": max(1, z["y1"] - z["y0"]),
                })
            i = j
    return out
